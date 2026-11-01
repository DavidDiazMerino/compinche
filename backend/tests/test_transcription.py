import json

import httpx
import pytest

from backend.provider import MetaProvider, ProviderFailure


@pytest.mark.parametrize("end,accepted", [(10, True), (31, False)])
def test_transcript_uses_spark_structured_output_and_validates_local_times(tmp_path, monkeypatch, end, accepted):
    monkeypatch.setenv("MODEL_API_KEY", "test-only")
    provider = MetaProvider()
    response = {"recipe_title": None, "summary": "A cook stirs.", "segments": [
        {"start_seconds": 1, "end_seconds": end, "text": "Removemos despacio.", "uncertainty": "Automatic transcript."}]}
    def handler(request):
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"id": "transcript-file"})
        payload = json.loads(request.content)
        assert payload["model"] == "muse-spark-1.3"
        assert payload["text"]["format"]["name"] == "compinche_transcript"
        assert payload["store"] is False
        assert "UNCONFIRMED" in payload["instructions"]
        return httpx.Response(200, json={"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(response)}]}]})
    real = httpx.Client
    monkeypatch.setattr("backend.provider.httpx.Client", lambda **kw: real(**kw, transport=httpx.MockTransport(handler)))
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"test media")
    if accepted:
        result = provider.transcribe({"clip_duration_seconds": 30}, [{"id": "clip", "path": str(clip), "mime_type": "video/mp4"}])
        assert result["segments"][0]["text"] == "Removemos despacio."
    else:
        with pytest.raises(ProviderFailure, match="interval"):
            provider.transcribe({"clip_duration_seconds": 30}, [{"id": "clip", "path": str(clip), "mime_type": "video/mp4"}])


def test_dedicated_audio_transcription_uses_voice_not_the_agent_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-only")
    provider = MetaProvider()
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF-test-WAVE")
    def handler(request):
        assert request.url.path == "/v1/asr/transcribe"
        assert b"muse-voice-transcribe-1.0" in request.content
        assert b"ENDPOINTING" in request.content
        return httpx.Response(200, json={"sessionId": "audio-session", "audioDurationMs": 2000,
            "transcript": "Removemos.", "turns": [{"startMs": 100, "endMs": 1900, "transcript": "Removemos."}]})
    real = httpx.Client
    monkeypatch.setattr("backend.provider.httpx.Client", lambda **kw: real(**kw, transport=httpx.MockTransport(handler)))
    result = provider.transcribe_audio(wav, 2)
    assert result["model"] == "muse-voice-transcribe-1.0"
    assert result["transcript"]["segments"][0]["start_seconds"] == .1
    assert provider.model == "muse-spark-1.3"
    assert provider.live_verified is False


@pytest.mark.parametrize("body", [{"error": "upstream failed"}, {"transcript": "", "turns": [], "audioDurationMs": 0}])
def test_an_invalid_audio_response_is_not_presented_as_no_speech(tmp_path, monkeypatch, body):
    monkeypatch.setenv("MODEL_API_KEY", "test-only")
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF-test-WAVE")
    real = httpx.Client
    monkeypatch.setattr("backend.provider.httpx.Client", lambda **kw: real(**kw, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))))
    with pytest.raises(ProviderFailure, match="valid audio transcript"):
        MetaProvider().transcribe_audio(wav, 2)
