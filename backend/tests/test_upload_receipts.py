from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.test_agent import FakeProvider, FakeSam


def test_native_upload_retries_recover_one_evidence_and_reject_changed_payload(tmp_path):
    provider = FakeProvider()
    app = create_app(tmp_path, provider, FakeSam())
    with TestClient(app) as client:
        sid = client.post('/api/sessions', json={'title': 'Android learner'}).json()['id']
        url = f'/api/sessions/{sid}/evidence'
        metadata = {'idempotency_key': 'sdk-capture-123', 'capture_source': 'meta_dat_mock', 'note': 'A recorded SDK clip.'}
        payload = b'\x00\x00\x00\x18ftypisom-original-capture'
        def send(content=payload, values=None):
            return client.post(url, data=values or metadata, files={'file': ('recorded.mp4', content, 'video/mp4')})
        first = send()
        assert first.status_code == 201
        retry = send()
        assert retry.status_code == 201 and retry.json()['id'] == first.json()['id']
        assert send(payload + b'changed').status_code == 409
        assert send(values={**metadata, 'note': 'Changed context'}).status_code == 409
        evidence = client.get(f'/api/sessions/{sid}').json()['evidence']
        assert len(evidence) == 1
        assert len(list((tmp_path / 'media').iterdir())) == 1
        assert client.get(first.json()['url']).content == payload
        other = client.post('/api/sessions', json={'title': 'Another learner'}).json()['id']
        response = client.post(f'/api/sessions/{other}/evidence', data=metadata, files={'file': ('recorded.mp4', payload, 'video/mp4')})
        assert response.status_code == 201 and response.json()['id'] != first.json()['id']
        assert provider.calls == []
