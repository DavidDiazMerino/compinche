import json
from types import SimpleNamespace
import httpx
import pytest
from backend.live_provider import LiveMetaProvider
from backend.provider import ProviderFailure

@pytest.mark.asyncio
async def test_live_vision_sends_inline_ordered_images_without_file_uploads(tmp_path):
    calls=[]
    def handler(request):
        calls.append(request.url.path);body=json.loads(request.content)
        assert body['reasoning']=={'effort':'minimal'} and body['store'] is False
        content=body['input'][0]['content']
        assert content[1]['text'].endswith('segundo 2.000')
        assert content[3]['text'].endswith('segundo 3.000')
        assert content[2]['image_url'].startswith('data:image/jpeg;base64,')
        return httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'observation':'Remueve en la sartén.','phase':None,'gap':None,'segmentation_targets':[]})}]}]})
    async with httpx.AsyncClient(base_url='https://api.meta.ai/v1',transport=httpx.MockTransport(handler)) as client:
        p=LiveMetaProvider(SimpleNamespace(key='test',model='muse-spark-1.3'),client)
        path=tmp_path/'frame.jpg';path.write_bytes(b'bounded-test-image')
        result=await p.live_observe({'cutoff_seconds':3},[{'id':str(n),'kind':'image','path':path,'timestamp_seconds':n} for n in (2,3)])
        assert result['gap'] is None
        assert calls==['/v1/responses']
        assert p.last_response_metadata['transport']=='inline_images_pooled_http'

@pytest.mark.asyncio
async def test_visual_measurement_is_rejected(tmp_path):
    def handler(request):
        return httpx.Response(200,json={'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'observation':'Hay 200 gramos de harina.','phase':None,'gap':None,'segmentation_targets':[]})}]}]})
    async with httpx.AsyncClient(base_url='https://api.meta.ai/v1',transport=httpx.MockTransport(handler)) as client:
        p=LiveMetaProvider(SimpleNamespace(key='test',model='muse-spark-1.3'),client)
        with pytest.raises(ProviderFailure,match='medida'):
            await p.live_observe({},[])

@pytest.mark.asyncio
async def test_fallback_asr_requires_real_processed_audio(tmp_path):
    wav=tmp_path/'voice.wav';wav.write_bytes(b'RIFF-test')
    def handler(request):
        assert request.url.path=='/v1/asr/transcribe'
        assert b'muse-voice-transcribe-1.0' in request.content
        return httpx.Response(200,json={'transcript':'removemos','audioDurationMs':0})
    async with httpx.AsyncClient(base_url='https://api.meta.ai/v1',transport=httpx.MockTransport(handler)) as client:
        p=LiveMetaProvider(SimpleNamespace(key='test',model='muse-spark-1.3'),client)
        with pytest.raises(ProviderFailure):await p.live_transcribe(wav,3)
