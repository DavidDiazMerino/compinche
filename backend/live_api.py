"""HTTP entry points for session-consented, bounded live capture tools."""
import asyncio
import base64
import binascii
import hashlib
import io
import json
import math
from pathlib import Path
import time
from uuid import uuid5,NAMESPACE_URL
import wave
from PIL import Image
from fastapi import File,Form,HTTPException,Query,UploadFile,Request
from fastapi.responses import FileResponse,StreamingResponse
from .live_models import LiveCreate,LiveObservation,LiveControl,LiveTypedAnswer
from .live import LiveCoordinator,LiveError
from .live_provider import LiveMetaProvider
from .live_voice import LiveVoice,VoiceUnavailable
from .live_segmentation import LiveSegmentation
from .store import now
from .live_report import METRIC_DEFINITIONS, provider_configuration, install_report_routes


def error(message,code='live_invalid_input',status=400):
    return HTTPException(status_code=status,detail={'code':code,'message':message})


def identity(*items):return str(uuid5(NAMESPACE_URL,'compinche/live-http/'+ '/'.join(str(x) for x in items)))


def clean(value):
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if k not in {'path','remote_file_id'}}
    if isinstance(value,list):return [clean(v) for v in value]
    return value


def decode_jpeg(value):
    if value.startswith('data:image/jpeg;base64,'):value=value.split(',',1)[1]
    try:raw=base64.b64decode(value,validate=True)
    except (ValueError,binascii.Error):raise error('La imagen no es un JPEG válido.')
    if not raw.startswith(b'\xff\xd8') or len(raw)>400_000:raise error('Envía una imagen JPEG menor de 400 kB.')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format!='JPEG' or not 1<=im.width<=960 or not 1<=im.height<=960:
                raise error('La imagen supera el tamaño de la vía rápida (960 píxeles).')
            im.verify()
    except (OSError,ValueError):raise error('La imagen no puede decodificarse.')
    return raw


def wav_info(raw):
    try:
        with wave.open(io.BytesIO(raw),'rb') as audio:
            frames=audio.getnframes();rate=audio.getframerate()
            if audio.getnchannels()!=1 or audio.getsampwidth()!=2 or rate not in (16000,24000) or audio.getcomptype()!='NONE':
                raise ValueError()
            if not 0<frames/rate<=35 or len(audio.readframes(frames))!=frames*2:raise ValueError()
            return frames/rate,rate
    except (wave.Error,ValueError,EOFError):raise error('Utiliza WAV mono PCM16 de 16/24 kHz, de hasta 35 segundos.')


def live_segmentation(store,sam,clips):
    """SAM 3.1 through the Meta Model API by default; COMPINCHE_LIVE_SAM=local keeps the resident GPU worker."""
    import os
    from .hosted_sam import HostedSAM,HostedLiveSegmentation
    key=os.environ.get('MODEL_API_KEY') or os.environ.get('META_API_KEY')
    if os.environ.get('COMPINCHE_LIVE_SAM','hosted')=='hosted' and key:
        return HostedLiveSegmentation(store,HostedSAM(key),clips)
    return LiveSegmentation(store,sam,clips)


def measure_service(store):
    """Hosted SAM 3.1 + local MapAnything; disabled unless the geometry image is configured."""
    import os
    from .hosted_sam import HostedSAM
    from .measure import GeometryRuntime,MeasureService
    image=os.environ.get('COMPINCHE_MEASURE_IMAGE');key=os.environ.get('MODEL_API_KEY') or os.environ.get('META_API_KEY')
    if not image or not key:return None
    return MeasureService(store,HostedSAM(key),GeometryRuntime(store.root,image,os.environ.get('COMPINCHE_SAM_CACHE'),
        os.environ.get('COMPINCHE_TORCH_CACHE')))


class LiveRoutes:
    def __init__(self,store,base_provider,sam,clips,provider=None,voice=None):
        self.store=store;self.provider=provider or LiveMetaProvider(base_provider)
        self.voice=voice or LiveVoice(store.root)
        self.segment=live_segmentation(store,sam,clips)
        self.measure=measure_service(store)
        self.gesture=None
        if self.measure and hasattr(self.provider,'response'):
            from .gesture import GestureService
            self.gesture=GestureService(store,self.measure.geometry,self.provider)
        self.core=LiveCoordinator(store,self.provider,sam,segment_observation=self.segment,measure=self.measure,gesture=self.gesture)
        self.tasks=set();self.locks={};self.audio_locks={};self.last_audio={}
        self.ws_count={}

    def task(self,coro):
        task=asyncio.create_task(coro);self.tasks.add(task);task.add_done_callback(self.tasks.discard);return task

    def snapshot(self,sid,after=0):
        data=self.core.snapshot(sid,after)
        data['tools']={**data.get('tools',{}),'voice':self.voice.status(),
            'audio':{'provider':'muse-voice-transcribe-1.0','configured':getattr(self.provider,'configured',False),'realtime_supported':True},
            'segmentation_runtime':self.segment.status(),
            'measurement_runtime':self.measure.status() if self.measure else {'configured':False}}
        data['server_wall_time']=time.time()
        data['metric_definitions']=METRIC_DEFINITIONS
        return clean(data)

    async def close(self):
        await self.core.close()
        for task in list(self.tasks):task.cancel()
        if self.tasks:await asyncio.gather(*list(self.tasks),return_exceptions=True)
        await self.segment.close();await self.voice.close()
        if self.measure:await self.measure.close()
        if hasattr(self.provider,'close'):await self.provider.close()

    def receipt(self,sid,lane,key,fingerprint):
        previous=self.store.get(identity(sid,'http-receipt',lane,key),'live_http_receipt')
        if previous:
            if previous['fingerprint']!=fingerprint:raise error('Esa clave ya identifica otros datos.','live_key_conflict',409)
            return {**previous['result'],'cached':True}
        return None

    def save_receipt(self,sid,lane,key,fingerprint,result):
        self.store.put(sid,'live_http_receipt',{'id':identity(sid,'http-receipt',lane,key),'fingerprint':fingerprint,'result':result})
        return result

    async def observe(self,sid,values):
        admission_started=time.monotonic();http_received_wall=time.time()
        payload=values.model_dump();fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        key=f'{values.scene_epoch}:{values.sequence}'
        async with self.locks.setdefault(sid,asyncio.Lock()):
            self.core.snapshot(sid)
            previous=self.receipt(sid,'observe',key,fingerprint)
            if previous:return previous
            snapshot=self.core.snapshot(sid)
            if snapshot['status']!='running' or snapshot['scene_epoch']!=values.scene_epoch:
                raise error('La captura cambió de estado; se descarta esta ventana.','live_stale_epoch',409)
            times=[frame.source_seconds for frame in values.frames]
            if times!=sorted(times):raise error('Las imágenes deben conservar el orden temporal.')
            raws=[decode_jpeg(frame.jpeg_base64) for frame in values.frames]
            if sum(map(len,raws))>1_048_576:raise error('La ventana supera 1 MiB.','live_window_too_large',413)
            config=snapshot['session']['live']['config']
            if config.get('source_kind')=='replay':
                source=self.store.get(config.get('source_evidence_id'),'evidence')
                if source and times[-1]>source.get('duration_seconds',86400)+.1:raise error('El instante está fuera de la grabación.')
            media=[]
            motion=None
            if values.video_evidence_id:
                motion=self.store.get(values.video_evidence_id,'evidence')
                if not motion or motion['session_id']!=sid or motion.get('kind')!='video':
                    raise error('La ventana de movimiento no pertenece a esta sesión.')
                from .clips import duration as probe_duration
                try:length=await asyncio.to_thread(probe_duration,motion['path'])
                except Exception:raise error('La ventana de movimiento no se puede decodificar.')
                begin=float(motion.get('timestamp_seconds',0))
                if length is None:
                    remaining=min(6,times[-1]-begin)
                    if remaining<=0:raise error('La ventana de movimiento rebasa el instante observado.')
                    normal=await asyncio.to_thread(self.segment.clips.create,sid,motion,0,remaining)
                    motion=normal['evidence'];length=motion['duration_seconds']
                if length is None or not 0<length<=6 or begin<max(0,times[-1]-10) or begin+length>times[-1]+.3:
                    raise error('La ventana de movimiento rebasa el instante observado.')
                motion=self.store.put(sid,'evidence',{**motion,'scene_epoch':values.scene_epoch,'duration_seconds':length,'source_end_seconds':begin+length})
            for i,(frame,raw) in enumerate(zip(values.frames,raws)):
                eid=identity(sid,'frame',key,i);path=self.store.root/'media'/(eid+'.jpg')
                if not path.exists():path.write_bytes(raw)
                media.append(self.store.put(sid,'evidence',{'id':eid,'kind':'image','mime_type':'image/jpeg','path':str(path),
                    'url':'/api/media/'+eid,'size':len(raw),'timestamp_seconds':frame.source_seconds,'source_end_seconds':frame.source_seconds,
                    'scene_epoch':values.scene_epoch,'sequence':values.sequence,'role':'live_frame','capture_source':config['source_kind'],
                    'capture_source_verification':'client_declared','source_evidence_id':config.get('source_evidence_id'),
                    'sdk_presentation_time_us':frame.sdk_presentation_time_us,'uploaded_to_meta':False,
                    'live_video_evidence_id':motion['id'] if motion else None,
                    'note':'Frame real recibido durante una sesión de aprendizaje; no describe el futuro.'}))
            receipt=await self.core.observe(sid,{'key':'obs-'+str(values.sequence),'sequence':values.sequence,
                'scene_epoch':values.scene_epoch,'start_seconds':times[0],'end_seconds':times[-1],
                'source_kind':config['source_kind'],'source_speaking':values.source_speaking,
                'http_received_wall':http_received_wall,'admission_ms':round((time.monotonic()-admission_started)*1000,1),
                'agent_speaking':values.agent_speaking,'evidence_ids':[e['id'] for e in media]})
            return self.save_receipt(sid,'observe',key,fingerprint,receipt)

    async def finish_audio(self,sid,key,raw,metadata):
        lane=metadata['origin'];epoch=metadata['scene_epoch']
        try:
            duration,rate=wav_info(raw)
            evidence_id=identity(sid,'audio',key);path=self.store.root/'media'/(evidence_id+'.wav')
            path.write_bytes(raw)
            evidence=self.store.put(sid,'evidence',{'id':evidence_id,'kind':'audio','mime_type':'audio/wav','path':str(path),
                'url':'/api/media/'+evidence_id,'size':len(raw),'duration_seconds':duration,'timestamp_seconds':metadata['start_seconds'],
                'source_end_seconds':metadata['end_seconds'],'scene_epoch':epoch,'role':'live_audio',
                'capture_source':lane,'capture_source_verification':'client_declared','uploaded_to_meta':True,
                'note':'Turno de audio real; identidad del hablante declarada por quien usa la sesión.'})
            started=time.monotonic()
            transcript=await self.provider.live_transcribe(path,duration)
            run=self.core._get(sid)
            if run.generation!=metadata.get('generation',run.generation):
                self.store.put(sid,'live_audio_job',{'id':identity(sid,'audio-job',key),'status':'discarded','reason':'capture_changed','audio_evidence_id':evidence_id})
                return
            if transcript['text']:
                await self.core.transcript(sid,{'key':key,'text':transcript['text'],'speaker':metadata['speaker_name'],
                    'role':metadata['speaker_role'],'origin':lane,'start_seconds':metadata['start_seconds'],
                    'end_seconds':metadata['end_seconds'],'scene_epoch':epoch,'answer_to':metadata.get('answer_to'),
                    'started_wall':metadata.get('started_wall'),'automatic':True,'asr_model':transcript['model'],
                    'evidence_ids':[evidence_id],'agent_speaking':metadata.get('agent_speaking',False)})
            self.store.put(sid,'live_audio_job',{'id':identity(sid,'audio-job',key),'status':'completed',
                'elapsed_ms':round((time.monotonic()-started)*1000,1),'text':transcript['text'],'audio_evidence_id':evidence_id})
        except asyncio.CancelledError:raise
        except Exception:
            await self.core.tool_error(sid,'transcription','No se pudo completar este turno de voz; no se ha inventado una explicación.',scene_epoch=epoch)
            self.store.put(sid,'live_audio_job',{'id':identity(sid,'audio-job',key),'status':'failed',
                'error':'No se pudo completar este turno de voz; no se ha inventado una explicación.'})
        finally:
            self.last_audio.pop((sid,lane),None)
            await self.core.speech_processing(sid,key,False,scene_epoch=epoch)


def install_live_routes(app,store,base_provider,sam,clips,provider=None,voice=None):
    live=LiveRoutes(store,base_provider,sam,clips,provider,voice)
    app.state.live=live.core;app.state.live_routes=live

    @app.exception_handler(LiveError)
    async def live_error_handler(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.status,content={'detail':{'code':exc.code,'message':str(exc)}})

    @app.post('/api/live',status_code=201)
    async def create(values:LiveCreate):
        if not values.consent:raise error('Inicia la sesión aceptando el análisis de imagen y voz.','live_consent_required')
        if values.source_kind=='replay':
            source=store.get(values.source_evidence_id,'evidence') if values.source_evidence_id else None
            if not source or source.get('kind')!='video':raise error('Elige una grabación original para el replay.','live_source_not_found',404)
        config=values.model_dump();config['cook_name']=values.teacher;config['input_mode']=values.source_kind
        config['provider_configuration']=provider_configuration(live.provider)
        data=await live.core.create(config)
        async def warm():
            try:await live.voice.warm()
            except VoiceUnavailable:pass
            if live.measure:
                try:await live.measure.geometry.warm()
                except Exception:await live.core.tool_error(data['id'],'measurement','La geometría local no está disponible; la sesión continúa.')
        live.task(warm())
        return live.snapshot(data['id'])

    @app.get('/api/live/{sid}')
    async def snapshot(sid:str,after:int=Query(0,ge=0)):
        return live.snapshot(sid,after)

    @app.get('/api/live/{sid}/timeline')
    async def timeline(sid:str):
        return clean(live.core.timeline(sid))

    @app.get('/api/live/{sid}/events')
    async def events(sid:str,request:Request,after:int=Query(0,ge=0)):
        live.core.snapshot(sid)
        async def stream():
            cursor=after;last=None;heartbeat=time.monotonic()
            while not await request.is_disconnected():
                snap=live.snapshot(sid,cursor)
                signature=json.dumps([snap['last_event_id'],snap['status'],snap['speaking'],snap.get('speech_pending'),snap['question']],sort_keys=True)
                if signature!=last:
                    yield 'event: snapshot\ndata: '+json.dumps(snap,ensure_ascii=False)+'\n\n'
                    last=signature;cursor=snap['last_event_id'];heartbeat=time.monotonic()
                    if snap['status'] in {'stopped','interrupted'}:return
                elif time.monotonic()-heartbeat>15:
                    yield ': heartbeat\n\n';heartbeat=time.monotonic()
                await asyncio.sleep(.15)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})

    @app.post('/api/live/{sid}/observe',status_code=202)
    async def observe(sid:str,values:LiveObservation):return clean(await live.observe(sid,values))

    @app.post('/api/live/{sid}/control')
    async def control(sid:str,values:LiveControl):
        await live.core.control(sid,values.model_dump(exclude_none=True))
        if values.action in {'stop','pause'} and not any(r.status=='running' for r in live.core.runs.values()):
            live.task(live.segment.close())
        return live.snapshot(sid)

    @app.post('/api/live/{sid}/answer')
    async def answer(sid:str,values:LiveTypedAnswer):
        receipt=await live.core.transcript(sid,{'key':values.utterance_id,'text':values.text,
            'speaker':values.speaker_name,'role':values.speaker_role,'origin':'typed',
            'start_seconds':values.source_seconds,'end_seconds':values.source_seconds,
            'answer_to':values.answer_to,'scene_epoch':values.scene_epoch,'automatic':False})
        return clean({**receipt,'snapshot':live.snapshot(sid)})

    @app.post('/api/live/{sid}/audio',status_code=202)
    async def audio(sid:str,file:UploadFile=File(...),utterance_id:str=Form(...),speaker_name:str=Form(...),
        speaker_role:str=Form('observer'),origin:str=Form('microphone'),start_seconds:float=Form(0),end_seconds:float=Form(0),
        answer_to:str|None=Form(None),scene_epoch:int=Form(0),capture_started_at:float|None=Form(None),
        capture_ended_at:float|None=Form(None),speech_started_at:float|None=Form(None),agent_speaking:bool=Form(False)):
        snap=live.core.snapshot(sid)
        if len(utterance_id)>100 or not utterance_id or not 1<=len(speaker_name)<=120 or speaker_role not in ('cook','wearer','observer') or origin not in ('microphone','recorded_source'):
            raise error('Declara el turno, el hablante y el origen de audio.')
        if not all(math.isfinite(t) and 0<=t<=86400 for t in (start_seconds,end_seconds)) or end_seconds<start_seconds:
            raise error('El intervalo de audio no es válido.')
        raw=await file.read(2_000_001);await file.close()
        if len(raw)>2_000_000:raise error('Ese turno de voz es demasiado largo.','live_audio_too_large',413)
        duration,_=wav_info(raw)
        if snap['status']!='running' or scene_epoch!=snap['scene_epoch']:
            raise error('Ese audio pertenece a una captura anterior.','live_stale_epoch',409)
        key=f'{scene_epoch}:{utterance_id}'
        fingerprint=hashlib.sha256(raw+json.dumps([speaker_name,speaker_role,origin,start_seconds,end_seconds,answer_to,scene_epoch],sort_keys=True).encode()).hexdigest()
        previous=live.receipt(sid,'audio',key,fingerprint)
        if previous:return previous
        if (sid,origin) in live.last_audio:raise error('El turno anterior sigue procesándose; la escucha continua usa el canal en tiempo real.','live_audio_busy',429)
        started=speech_started_at if speech_started_at is not None else capture_started_at
        if started is not None and (not math.isfinite(started) or started>time.time()+3 or started<time.time()-90):
            raise error('El reloj de captura de voz está fuera de la ventana admitida.')
        metadata={'speaker_name':speaker_name,'speaker_role':speaker_role,'origin':origin,
            'start_seconds':start_seconds,'end_seconds':end_seconds,'answer_to':answer_to,
            'scene_epoch':scene_epoch,'started_wall':started,'agent_speaking':agent_speaking,'generation':live.core._get(sid).generation}
        live.last_audio[(sid,origin)]=key
        await live.core.speech_processing(sid,key,True,scene_epoch=scene_epoch)
        live.task(live.finish_audio(sid,key,raw,metadata))
        return live.save_receipt(sid,'audio',key,fingerprint,{'accepted':True,'key':key,'status':'transcribing','duration_seconds':duration})

    @app.get('/api/live/{sid}/questions/{qid}/voice')
    async def voice(sid:str,qid:str):
        preparation_started=time.monotonic()
        snap=live.core.snapshot(sid);question=store.get(qid,'question')
        if not question or question['session_id']!=sid:raise error('Esa pregunta no pertenece a esta sesión.','live_question_not_found',404)
        if question['status'] not in {'pending','ready','spoken'}:raise error('La pregunta ya no está activa.','live_question_changed',409)
        try:path=await live.voice.synthesize(question['text'])
        except VoiceUnavailable as exc:
            await live.core.tool_error(sid,'voice','No se pudo preparar la voz de esta pregunta.')
            raise error(str(exc),'live_voice_unavailable',503)
        current=live.core.snapshot(sid)
        if current['status']!='running' or not current['question'] or current['question']['id']!=qid:
            raise error('La pregunta cambió mientras se preparaba la voz.','live_question_changed',409)
        await live.core.voice_prepared(sid,qid,round((time.monotonic()-preparation_started)*1000,1))
        return FileResponse(path,media_type='audio/wav',headers={'Cache-Control':'private, max-age=3600'})

    from .live_audio_stream import install_audio_stream
    install_audio_stream(app,live)
    install_report_routes(app,live)
    return live
