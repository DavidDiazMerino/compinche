"""Automatically dispatched temporal SAM tool with a bounded warm runtime."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from .store import uid,now
from .sam_service import SamBusy,SamFailure,SamUnavailable,TARGETS,_memory_available_gib,_sha256
from .segmentation_context import summarise_job

class LiveSegmentation:
    def __init__(self,store,sam,clips):
        self.store,self.sam,self.clips=store,sam,clips
        self.process=None;self.owner=None;self.container=None;self.log=None
        self.lock=asyncio.Lock();self.load_seconds=None;self.jobs=0

    def status(self):
        return {'provider':'local_sam_3.1','warm':bool(self.process and self.process.returncode is None and self.load_seconds is not None),
            'resident_load_seconds':self.load_seconds,'jobs_in_runtime':self.jobs,
            'busy':self.lock.locked(),'pixel_localisation_only':True}

    async def packet(self,timeout):
        started=time.monotonic()
        while time.monotonic()-started<timeout:
            if _memory_available_gib()<self.sam.stop_memory_gib:raise SamBusy('SAM se detuvo para liberar memoria compartida.')
            try:line=await asyncio.wait_for(self.process.stdout.readline(),1)
            except asyncio.TimeoutError:
                if _memory_available_gib()<self.sam.stop_memory_gib:raise SamBusy('SAM se detuvo para liberar memoria compartida.')
                continue
            if not line:
                code=await self.process.wait()
                raise SamFailure(f'El proceso de segmentación se cerró (código {code}).')
            if not line.startswith(b'COMPINCHE_SAM_EVENT ') and self.log:
                self.log.write(line);self.log.flush()
            if line.startswith(b'COMPINCHE_SAM_EVENT '):return json.loads(line[len(b'COMPINCHE_SAM_EVENT '):])
        raise SamFailure('La segmentación excedió el tiempo permitido.')

    async def warm(self):
        if self.process and self.process.returncode is None:return
        config=await asyncio.to_thread(self.sam._runtime_status)
        if not config['configured']:raise SamUnavailable(config.get('message','SAM no está disponible.'))
        self.owner='compinche-live-sam '+uid();await asyncio.to_thread(self.sam._acquire_gpu,self.owner)
        self.container='compinche-live-sam-'+uid()
        self.log=(self.store.root/'live-sam-worker.log').open('ab')
        worker=Path(__file__).with_name('sam_worker.py').resolve();live=Path(__file__).with_name('sam_live_worker.py').resolve()
        args=['docker','run','--rm','-i','--name',self.container,'--network','none','--gpus','all',
            '--ipc','private','--shm-size','2g','--user',f'{os.getuid()}:{os.getgid()}','--memory','36g','--memory-swap','36g','--cpus','6',
            '--cap-drop','ALL','--security-opt','no-new-privileges','-e','HOME=/tmp','-e','HF_HOME=/cache/huggingface',
            '-e','HF_HUB_OFFLINE=1','-e','OMP_NUM_THREADS=4','-e','PYTHONUNBUFFERED=1',
            '-v',str(self.sam.cache)+':/cache/huggingface/hub:ro','-v',str(self.store.root/'media')+':/media:ro',
            '-v',str(self.store.root/'artifacts')+':/output','-v',str(worker)+':/app/sam_worker.py:ro',
            '-v',str(live)+':/app/sam_live_worker.py:ro',self.sam.image,'python','/app/sam_live_worker.py']
        try:
            self.process=await asyncio.create_subprocess_exec(*args,stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,stderr=self.log)
            packet=await self.packet(90)
            if not packet.get('ready'):raise SamFailure('SAM no se pudo cargar.')
            self.load_seconds=packet['load_seconds'];self.jobs=0
        except BaseException:
            await self._close();raise

    async def __call__(self,media,target,sid):
        if self.lock.locked():raise SamBusy('La herramienta SAM sigue trabajando; la captura continúa.')
        if target not in TARGETS:raise SamFailure('Objetivo de segmentación no permitido.')
        async with self.lock:
            job_id=uid();output=self.store.root/'artifacts'/job_id;output.mkdir(parents=True)
            session=self.store.get_session(sid);config=session['live']['config']
            source=self.store.get(config.get('source_evidence_id'),'evidence') if config.get('source_evidence_id') else None
            start=min(float(m.get('timestamp_seconds',0)) for m in media)
            end=max(float(m.get('timestamp_seconds',0)) for m in media)
            motion=self.store.get(media[0].get('live_video_evidence_id'),'evidence') if media[0].get('live_video_evidence_id') else None
            if motion and (motion['session_id']!=sid or motion.get('scene_epoch')!=media[0].get('scene_epoch') or motion.get('source_end_seconds',86400)>end+.3):motion=None
            replay_motion=config.get('source_kind')=='replay' and source and end>start+.1
            sampled=not (replay_motion or motion)
            if not sampled:
                # Real motion from the original, physically clipped BEFORE the cutoff.
                if replay_motion:
                    # Two seconds / eight frames keep the live tool within the shared-memory budget.
                    start=max(start,end-2)
                    cut=await asyncio.to_thread(self.clips.create,sid,source,start,min(end-start,2))
                    evidence=cut['evidence'];source_path=Path(evidence['path'])
                else:
                    length=min(motion['duration_seconds'],end-motion['timestamp_seconds'])
                    cut=await asyncio.to_thread(self.clips.create,sid,motion,max(0,length-2),min(length,2))
                    evidence=cut['evidence'];source_path=Path(evidence['path'])
                    start=evidence['timestamp_seconds'];end=min(end,start+evidence['duration_seconds'])
                prepared=output/'prepared.mp4'
                cmd=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-protocol_whitelist','file,pipe',
                    '-i',str(source_path),'-vf',"scale='min(640,iw)':-2,fps=4",'-frames:v','32','-an',
                    '-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p','-threads','2',str(prepared)]
            else:
                from PIL import Image
                frames=output/'source-frames';frames.mkdir()
                for i,item in enumerate(media):
                    with Image.open(item['path']) as frame:
                        frame.thumbnail((640,640));frame.convert('RGB').save(frames/(f'{i:03d}.jpg'))
                prepared=output/'prepared.mp4'
                cmd=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-framerate','4','-i',str(frames/'%03d.jpg'),
                    '-vf','pad=ceil(iw/2)*2:ceil(ih/2)*2','-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p','-threads','2',str(prepared)]
            await asyncio.to_thread(subprocess.run,cmd,capture_output=True,check=True,timeout=30)
            # Stable mounted media root; job output stays in the existing controlled artifact tree.
            media_id=uid()
            media_path=self.store.root/'media'/(media_id+'-sam.mp4');media_path.write_bytes(prepared.read_bytes())
            evidence=self.store.put(sid,'evidence',{'kind':'video','path':str(media_path),'mime_type':'video/mp4',
                'url':'/api/media/'+media_id,'id':media_id,'timestamp_seconds':start,'source_end_seconds':end,
                'scene_epoch':media[0].get('scene_epoch',0),'role':'analysis_clip',
                'duration_seconds':len(media)/4 if sampled else end-start,'sampled_sequence':sampled,
                'source_frame_ids':[m['id'] for m in media],'uploaded_to_meta':False})
            job=self.store.put(sid,'job',{'id':job_id,'evidence_id':evidence['id'],'target':target,'status':'running','started_at':now(),
                'start_seconds':0,'duration_seconds':min(8,evidence['duration_seconds']),'automatic_live_tool':True})
            started=time.monotonic()
            try:
                await self.warm()
                self.process.stdin.write((json.dumps({'id':job_id,'input':'/media/'+media_path.name,'output':'/output/'+job_id,'target':TARGETS[target]})+'\n').encode())
                await self.process.stdin.drain();packet=await self.packet(120)
                if packet.get('id')!=job_id or not packet.get('ok'):raise SamFailure('SAM no pudo analizar esa ventana.')
                result=json.loads((output/'result.json').read_text());self.jobs+=1
                for i,frame in enumerate(result['frames']):
                    frame['source_seconds']=float(media[min(i,len(media)-1)]['timestamp_seconds'])-start if sampled else frame['seconds']
                result.update(elapsed_seconds=round(time.monotonic()-started,3),resident=True,
                    resident_load_seconds=self.load_seconds,source_sha256=await asyncio.to_thread(_sha256,media_path),
                    sampled_sequence=sampled,source_frame_ids=[m['id'] for m in media],
                    provenance='Real SAM inference; '+('ordered captured JPEGs, sample times stored separately, no original movement speed claim' if sampled else 'actual original motion clipped at the received cutoff'))
                await asyncio.to_thread(subprocess.run,['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-i',str(output/'overlay-raw.mp4'),
                    '-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p','-threads','2','-movflags','+faststart',str(output/'overlay.mp4')],capture_output=True,check=True,timeout=25)
                result['artifacts']={k:f'/api/artifacts/{job_id}/{v}' for k,v in {'overlay':'overlay.mp4','poster':'poster.jpg','report':'result.json'}.items()}
                (output/'result.json').write_text(json.dumps(result,indent=2))
                job=self.store.put(sid,'job',{**job,'status':'completed','completed_at':now(),'result':result})
                return {**summarise_job(job,evidence),'original_end_seconds':end,'artifacts':result['artifacts'],'resident':True,
                    'resident_load_seconds':self.load_seconds,'elapsed_seconds':result['elapsed_seconds'],'sampled_sequence':sampled}
            except BaseException as error:
                self.store.put(sid,'job',{**job,'status':'interrupted' if isinstance(error,asyncio.CancelledError) else 'failed',
                    'error':'SAM no terminó esta ventana; no se ha generado una máscara de ejemplo.'})
                await self._close();raise

    async def _close(self):
        if self.container:
            try:await asyncio.to_thread(subprocess.run,['docker','stop','--time','2',self.container],capture_output=True,timeout=8)
            except (OSError,subprocess.TimeoutExpired):pass
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:await asyncio.wait_for(self.process.wait(),3)
            except asyncio.TimeoutError:self.process.kill();await self.process.wait()
        if self.owner:self.sam._release_gpu(self.owner)
        self.process=None;self.container=None;self.owner=None;self.load_seconds=None
        if self.log:self.log.close();self.log=None

    async def close(self):
        async with self.lock:await self._close()
