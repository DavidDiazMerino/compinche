"""Warm CPU speech service. Never clones a family member or sends text to a TTS cloud."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

class VoiceUnavailable(RuntimeError): pass

class LiveVoice:
    def __init__(self, root):
        self.root=Path(root);self.directory=self.root/'voice';self.directory.mkdir(exist_ok=True)
        default=Path(__file__).parent/'.data'/'voice'/'es_ES-davefx-medium.onnx'
        self.model=Path(os.environ.get('COMPINCHE_PIPER_MODEL',str(default))).expanduser()
        self.python=os.environ.get('COMPINCHE_PIPER_PYTHON',sys.executable)
        self.process=None;self.lock=asyncio.Lock();self.last_ms=None

    def status(self):
        return {'provider':'local_piper','configured':self.model.is_file() and Path(str(self.model)+'.json').is_file(),
            'warm':bool(self.process and self.process.returncode is None),'last_synthesis_ms':self.last_ms,
            'voice':'es_ES-davefx-medium','voice_cloned':False}

    async def warm(self):
        async with self.lock:
            await self._warm()

    async def _warm(self):
        if self.process and self.process.returncode is None:return
        if not self.status()['configured']:raise VoiceUnavailable('Configura la voz local o utiliza la voz del dispositivo.')
        self.process=await asyncio.create_subprocess_exec(self.python,str(Path(__file__).with_name('voice_worker.py')),
            '--model',str(self.model),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        try:
            ready=json.loads(await asyncio.wait_for(self.process.stdout.readline(),12))
            if not ready.get('ready'):raise ValueError('Not ready')
        except BaseException as error:
            await self._stop()
            raise VoiceUnavailable('La voz local no pudo iniciarse; usa la voz del dispositivo.') from error

    async def synthesize(self,text):
        if not isinstance(text,str) or not 1<=len(text)<=500:raise VoiceUnavailable('Pregunta de voz fuera del límite.')
        key=hashlib.sha256((str(self.model)+'\0'+text).encode()).hexdigest()
        target=self.directory/(key+'.wav')
        async with self.lock:
            if target.is_file():return target
            await self._warm();started=time.monotonic()
            temporary=target.with_suffix('.tmp.wav')
            try:
                self.process.stdin.write((json.dumps({'id':key,'text':text,'path':str(temporary)},ensure_ascii=False)+'\n').encode())
                await self.process.stdin.drain()
                result=json.loads(await asyncio.wait_for(self.process.stdout.readline(),15))
                if not result.get('ok') or result.get('id')!=key:raise ValueError('Synthesis failed')
                temporary.replace(target)
                self.last_ms=round((time.monotonic()-started)*1000,1)
                return target
            except BaseException as error:
                temporary.unlink(missing_ok=True)
                await self._stop()
                if isinstance(error,asyncio.CancelledError):raise
                raise VoiceUnavailable('No se pudo leer la pregunta con la voz local.') from error

    async def _stop(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:await asyncio.wait_for(self.process.wait(),3)
            except asyncio.TimeoutError:
                self.process.kill();await self.process.wait()
        self.process=None

    async def close(self):
        async with self.lock:await self._stop()
