"""Local PCM bridge to Meta's authenticated realtime ENDPOINTING socket.

No key reaches a browser. Only finalized speechComplete turns reach the learner.
"""
import asyncio
from collections import deque
import io
import json
import math
import time
import uuid
import wave
from urllib.parse import urlsplit
from fastapi import WebSocket,WebSocketDisconnect
import websockets
from .store import now

RATE=24000
BYTES_PER_MS=48


def install_audio_stream(app,live):
    @app.websocket('/api/live/{sid}/audio-stream')
    async def audio_stream(socket:WebSocket,sid:str):
        origin=socket.headers.get('origin')
        if (origin and urlsplit(origin).netloc!=socket.url.netloc) or socket.headers.get('sec-fetch-site')=='cross-site':
            await socket.close(code=1008);return
        try:live.core.snapshot(sid)
        except ValueError:
            await socket.close(code=1008);return
        if not getattr(live.provider,'configured',False) or not getattr(live.provider,'key',None):
            await socket.accept();await socket.send_json({'type':'error','message':'La transcripción en tiempo real no está conectada.','fallback':True});await socket.close();return
        await socket.accept()
        upstream=None;receiver=None;sender=None;slot=None;acquired=False;pending_speech={}
        try:
            start=await asyncio.wait_for(socket.receive_json(),8)
            source=start.get('origin');role=start.get('speaker_role','observer');name=start.get('speaker_name','')
            if source not in ('microphone','recorded_source') or role not in ('cook','wearer','observer') or not isinstance(name,str) or not 1<=len(name)<=120 or start.get('sample_rate')!=RATE:
                raise ValueError('Declara el origen, hablante y PCM mono de 24 kHz.')
            slot=(sid,source)
            if live.ws_count.get(slot,0):raise ValueError('Ya hay un canal de ese origen abierto en la sesión.')
            live.ws_count[slot]=1;acquired=True
            metadata={'source_seconds':float(start.get('source_seconds',0)), 'scene_epoch':int(start.get('scene_epoch',0)),
                'answer_to':None,'agent_speaking':False}
            if not math.isfinite(metadata['source_seconds']) or not 0<=metadata['source_seconds']<=86400:raise ValueError('Reloj de fuente no válido.')
            stream_id=str(uuid.uuid4())
            upstream=await websockets.connect('wss://api.meta.ai/v1/asr/realtime?sessionId=compinche-'+stream_id,
                open_timeout=10,close_timeout=3,max_size=1_000_000,ping_interval=20)
            await upstream.send(json.dumps({'authorization':{'accessToken':'Bearer '+live.provider.key},
                'audioEncoding':'PCM_24KHZ','model':'muse-voice-transcribe-1.0','mode':'ENDPOINTING',
                'partialMode':'CUMULATIVE','languageBias':['Spanish'],'emitAudioProgress':False}))
            handshake=json.loads(await asyncio.wait_for(upstream.recv(),10))
            if not handshake.get('sessionId'):raise ValueError('Meta no confirmó el canal de voz.')
            await socket.send_json({'type':'ready','model':'muse-voice-transcribe-1.0','sample_rate':RATE})
            chunks=deque();turns={};total_ms=0.;stream_started=time.monotonic();ended=False;last_turn=None
            channel='source' if source=='recorded_source' else 'human'
            send_lock=asyncio.Lock()
            async def emit(value):
                async with send_lock:await socket.send_json(value)
            def at(ms):
                return next((c for c in chunks if c['end']>=ms),chunks[-1] if chunks else None)

            async def receive_upstream():
                nonlocal last_turn
                async for raw in upstream:
                    if isinstance(raw,bytes):continue
                    event=json.loads(raw);kind=event.get('type');turn_id=event.get('turnId')
                    processed=float(event.get('audioProcessedMs',total_ms))
                    if kind=='error':raise ValueError('El proveedor interrumpió la transcripción en tiempo real.')
                    if kind=='speechStart':
                        point=at(max(0,processed-300))
                        if point is None:continue
                        frozen=dict(point['metadata']);run=live.core._get(sid)
                        # ASR progress markers are approximate. Bind eligibility to a
                        # locally frozen VAD onset, never infer an exact onset from progress.
                        candidates=[c for c in chunks if processed-500<=c['end']<=processed+500
                            and c['metadata'].get('utterance_id') and c['metadata'].get('speech_started_at') is not None]
                        if candidates:
                            onset=min(candidates,key=lambda c:abs(c['metadata']['speech_started_at']-point['wall']))
                            frozen=dict(onset['metadata'])
                        turns[turn_id]={'start':max(0,processed-300),'metadata':frozen,'generation':point['generation'],'started_wall':frozen.get('speech_started_at')}
                        last_turn=turn_id
                        if frozen['scene_epoch']==run.epoch:
                            fence=f'ws-{stream_id}-{turn_id}';pending_speech[fence]=frozen['scene_epoch']
                            await live.core.speech_processing(sid,fence,True,scene_epoch=frozen['scene_epoch'])
                            await live.core.control(sid,{'action':'speaking','channel':channel,'speaking':True})
                    elif kind=='speechEnd':
                        if turn_id in turns:turns[turn_id]['end']=processed
                        run=live.core._get(sid)
                        if turn_id in turns and turns[turn_id]['metadata']['scene_epoch']==run.epoch:
                            active=any(t['metadata']['scene_epoch']==run.epoch and 'end' not in t for t in turns.values())
                            await live.core.control(sid,{'action':'speaking','channel':channel,'speaking':active})
                    elif kind=='transcript':
                        text=event.get('transcript','')
                        if isinstance(text,str) and len(text)<=5000:await emit({'type':'partial','text':text,'turn_id':last_turn})
                    elif kind=='speechComplete':
                        fence=f'ws-{stream_id}-{turn_id}'
                        fence_epoch=pending_speech.get(fence)
                        try:
                            turn=turns.pop(turn_id,None);text=event.get('transcript','').strip()
                            if not turn or not text or len(text)>5000:continue
                            end=turn.get('end',processed);selected=[c for c in chunks if c['end']>=turn['start'] and c['start']<=end]
                            if not selected or chunks[0]['start']>turn['start']+100:
                                await emit({'type':'error','message':'El turno de voz excedió el buffer; pide repetirlo.','fallback':False});continue
                            frozen=turn['metadata'];run=live.core._get(sid)
                            if frozen['scene_epoch']!=run.epoch or run.status!='running' or turn['generation']!=run.generation:continue
                            # Keep actual received samples and their declared timeline, not an invented WAV.
                            pcm=b''.join(c['raw'] for c in selected)
                            if not any(pcm):continue
                            buffer=io.BytesIO()
                            with wave.open(buffer,'wb') as output:
                                output.setnchannels(1);output.setsampwidth(2);output.setframerate(RATE);output.writeframes(pcm)
                            eid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'compinche/{sid}/realtime/{stream_id}/{turn_id}'))
                            path=live.store.root/'media'/(eid+'.wav');path.write_bytes(buffer.getvalue())
                            start_source=frozen['source_seconds'];end_source=max(start_source,selected[-1]['metadata']['source_seconds'])
                            evidence=live.store.put(sid,'evidence',{'id':eid,'kind':'audio','mime_type':'audio/wav','path':str(path),
                                'url':'/api/media/'+eid,'size':path.stat().st_size,'duration_seconds':len(pcm)/(RATE*2),
                                'timestamp_seconds':start_source,'source_end_seconds':end_source,'scene_epoch':frozen['scene_epoch'],
                                'role':'live_audio','capture_source':source,'capture_source_verification':'client_declared',
                                'uploaded_to_meta':True,'asr_turn_bounds':'approximate_progress_markers','note':'Audio PCM realmente recibido; texto ASR no confirmado.'})
                            result=await live.core.transcript(sid,{'key':f'ws-{stream_id}-{turn_id}','text':text,'speaker':name,'role':role,
                                'origin':source,'start_seconds':start_source,'end_seconds':end_source,'scene_epoch':frozen['scene_epoch'],
                                'answer_to':frozen.get('answer_to'),'started_wall':turn['started_wall'],
                                'automatic':True,'asr_model':'muse-voice-transcribe-1.0','evidence_ids':[eid],
                                'agent_speaking':frozen.get('agent_speaking',False)})
                            await emit({'type':'transcript','text':text,'turn_id':turn_id,'result':clean_result(result)})
                        finally:
                            if fence in pending_speech:
                                pending_speech.pop(fence,None)
                                await live.core.speech_processing(sid,fence,False,scene_epoch=fence_epoch)
            async def receive_client():
                nonlocal total_ms,ended
                while True:
                    event=await socket.receive()
                    if event['type']=='websocket.disconnect':return
                    if event.get('text'):
                        msg=json.loads(event['text'])
                        if msg.get('type')=='end':
                            ended=True;await upstream.send(json.dumps({'type':'endStream'}));return
                        if msg.get('type')=='clock':
                            seconds=float(msg.get('source_seconds',metadata['source_seconds']))
                            epoch=int(msg.get('scene_epoch',metadata['scene_epoch']))
                            if not math.isfinite(seconds) or not 0<=seconds<=86400 or epoch<0:raise ValueError('Reloj no válido.')
                            onset=msg.get('speech_started_at');utterance=msg.get('utterance_id')
                            if onset is not None and (type(onset) not in (int,float) or not math.isfinite(onset) or not time.time()-90<=onset<=time.time()+3):raise ValueError('Inicio de voz fuera de ventana.')
                            if utterance is not None and (not isinstance(utterance,str) or len(utterance)>100):raise ValueError('Turno no válido.')
                            metadata.update(source_seconds=seconds,scene_epoch=epoch,utterance_id=utterance,speech_started_at=onset,
                                answer_to=msg.get('answer_to'),agent_speaking=bool(msg.get('agent_speaking',False)))
                    elif event.get('bytes') is not None:
                        pcm=event['bytes']
                        if ended or not 1<=len(pcm)<=24000 or len(pcm)%2:raise ValueError('Bloque PCM fuera de límite.')
                        if total_ms/1000>time.monotonic()-stream_started+3:raise ValueError('Envía audio a velocidad real, sin acumular retraso.')
                        run=live.core._get(sid)
                        if run.status!='running' or metadata['scene_epoch']!=run.epoch or run.agent_speaking or metadata.get('agent_speaking'):
                            pcm=b'\0'*len(pcm)
                        duration=len(pcm)/BYTES_PER_MS
                        chunks.append({'start':total_ms,'end':total_ms+duration,'raw':pcm,'metadata':dict(metadata),
                            'wall':time.time(),'seconds':duration/1000,'generation':run.generation})
                        total_ms+=duration
                        while chunks and chunks[0]['end']<total_ms-60000:chunks.popleft()
                        await upstream.send(pcm)
            receiver=asyncio.create_task(receive_upstream());sender=asyncio.create_task(receive_client())
            done,_=await asyncio.wait({receiver,sender},return_when=asyncio.FIRST_COMPLETED)
            for task in done:task.result()
            if sender in done and ended:
                try:await asyncio.wait_for(receiver,8)
                except asyncio.TimeoutError:pass
        except (WebSocketDisconnect,asyncio.CancelledError):pass
        except Exception:
            try:await socket.send_json({'type':'error','message':'El canal de voz en tiempo real no pudo continuar; se puede usar el envío por turnos.','fallback':True})
            except Exception:pass
        finally:
            for task in (receiver,sender):
                if task and not task.done():task.cancel()
            pending=[task for task in (receiver,sender) if task]
            if pending:await asyncio.gather(*pending,return_exceptions=True)
            if upstream:await upstream.close()
            for fence,epoch in list(pending_speech.items()):
                await live.core.speech_processing(sid,fence,False,scene_epoch=epoch)
            if acquired:live.ws_count.pop(slot,None)
            try:
                if not acquired:return
                await live.core.control(sid,{'action':'speaking','channel':'source' if slot and slot[1]=='recorded_source' else 'human','speaking':False})
            except Exception:pass
            try:await socket.close()
            except Exception:pass


def clean_result(value):
    if isinstance(value,dict):return {k:clean_result(v) for k,v in value.items() if k not in ('path','remote_file_id')}
    if isinstance(value,list):return [clean_result(v) for v in value]
    return value
