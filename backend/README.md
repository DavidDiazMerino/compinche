# Compinche local backend

A real FastAPI + LangGraph application for two connected workflows: recording a cook's decisions and learning from their original evidence. The frontend is served from `../dist` by the same loopback server. Nothing is deployed to Sites.

## Run

From `application/`:

```sh
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.lock.txt
backend/.venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. Keep the host bound to loopback: this development app has no multi-user authentication or internet deployment configuration. Its origin and Host checks are an additional local boundary, not an authentication system.

For Meta, supply `MODEL_API_KEY` (or `META_API_KEY`) to the **server environment**, never the frontend. An optional `COMPINCHE_META_MODEL` selects the model; the default is `muse-spark-1.3`. The endpoint is fixed to `https://api.meta.ai/v1`. This server does not automatically load a `.env` file. The example file documents names only.

Without a key, recording imports, sessions, notes, human confirmations and installed local SAM remain usable. An attempted agent turn returns `503 meta_not_configured`. There is no simulated production agent response. `GET /api/health` distinguishes configured, live verified and unavailable states without returning credentials. A configured key alone does not establish that a model request works.

## Conversation and evidence

`CookingAgent` is an actual LangGraph `StateGraph` with four nodes: retrieve context, call Meta, validate, and remember. SQLite checkpoints persist by session ID; a separate SQLite store persists messages, notes, media references, claims, questions and segmentation jobs. The default directory is `backend/.data`; `COMPINCHE_DATA_DIR` can change it. Tracing is explicitly disabled so inherited LangSmith settings do not export local conversation data.

A learn session references an original record session. Original teacher messages and their source IDs stay separate from learner messages. The prompt receives the most recent 40 conversation messages, 30 original teacher messages, 100 notes and 100 claims; omitted turn counts are explicit. All local messages remain in SQLite even when older context is outside that window.

At most one unanswered question is stored. Replies have a separate single-question object; a new question is suppressed while another remains open. An answer must name the current `answer_to` ID. The exact question remains attached to its assistant turn after it is answered, in the API, recalled conversation and original teacher context. Question history retains its answer-message link. Older records containing only a question ID are resolved from the saved question entity without duplicating that question on later turns. Accepted messages, claims, question changes and an optional idempotency receipt are saved in one transaction. Failed validation saves no generated messages or claims. Transfers to Meta are logged separately so a rejected model response does not hide that media was sent.

Every candidate claim carries `source: observed|said|inferred`, source IDs and plain-language uncertainty. Only the human confirmation endpoint can mark it confirmed, with an optional correction and preserved original model text. A confidence score never promotes a claim to truth.

The structured schema and deterministic checks reject foreign source IDs, unsupported common measured quantities, invented quotations, questions outside the question channel, observations without media supplied in that turn and certain food-safety assurances. The system prompt also requires grounded recipe claims and treats OCR, video, user notes and quotations as untrusted data. The agent has no tool allowing arbitrary commands, external requests or file access. These controls constrain behavior; they do **not** establish the truth of every paraphrase, detect every hallucination or make visual cooking advice a safety certification. Human review remains necessary.

## Media and models

Uploading a recording saves it locally and does not call Meta or SAM. A turn explicitly names the media IDs to send to Meta. The adapter uses the documented Files API (`purpose=user_data`) and Responses API with `text.format` JSON schema and `store:false`. Selected media and bounded conversation context are transmitted to Meta; provider processing/retention terms still apply. `store:false` is not a promise of file deletion, and this adapter does not delete remote Files objects. Transfer status `requested` with `uploaded_to_meta:null` means a failed request may have transmitted data; `sent` means the provider response succeeded.

Uploads are streamed with a 150 MiB limit and checked for supported container/image signatures. This is file-type checking, not a full decoder or malware scan. Interactive Meta attachments are capped at 40 MiB each and four per turn. The HTTP client has 100-second read/write and 10-second connect timeouts; turns are rate limited to 12 per minute per session and serialized within a session. Media uses generated IDs and server-controlled paths. No API accepts a client filesystem path or arbitrary remote URL.

SAM is a separate explicit asynchronous action on a saved video. See [SAM.md](SAM.md) for the verified local SAM 3.1 runtime, GPU ownership, bounds and fresh smoke result. Segmentation yields object masks/pixel coordinates; it cannot infer grams, physical dimensions, temperature, taste or tactile resistance.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Capabilities and configured/live status; no secrets |
| `POST /api/sessions` | `{title, teacher?, mode:record\|learn, source_session_id?}` |
| `GET /api/sessions` | Saved sessions and counts |
| `GET /api/sessions/{id}` | Session, messages, questions, evidence, knowledge, notes, jobs, pending question |
| `POST /api/sessions/{id}/evidence` | Multipart `file`, `note?`, `timestamp_seconds?`; no inference |
| `POST /api/sessions/{id}/notes` | `{text, evidence_ids?, timestamp_seconds?}`; no model required |
| `POST /api/sessions/{id}/turn` | `{text, evidence_ids?, answer_to?, idempotency_key?}` |
| `POST /api/sessions/{id}/knowledge/{claim}/confirm` | `{text?}` confirms or corrects a claim in its original session |
| `POST /api/sessions/{id}/segment` | `{evidence_id, target:saucepan\|spoon\|food}` → queued job, HTTP 202 |
| `GET /api/jobs/{job}` | Durable queued/running/completed/failed/interrupted state |
| `GET /api/media/{evidence}` | Controlled local original media; range requests supported |
| `GET /api/artifacts/{job}/{filename}` | Controlled completed-job artifacts |

Successful turns return `{message, question, knowledge, segmentation_targets, guardrails, session}`. `message.text` is the short reply; `message.question` contains the question actually asked with that turn, or null. The top-level `question` is the currently active question with `id/text/reason/evidence_ids` or null, which may have been asked on an earlier turn. Historical session messages include their question text and current answered/pending status. User messages retain `answer_to`. New knowledge is unconfirmed. SAM jobs return result artifact URLs and per-frame mask metadata. The API's `/docs` page exposes the request schemas.

## Validation and limits

```sh
backend/.venv/bin/python -m pytest backend/tests -q
```

The suite covers persistence, original/learner provenance, one active question, idempotency, transactional rollback, evidence/measurement/quotation boundaries, local-only imports without a key, origin checks, human corrections, documented Meta request shapes, sanitised failures, SAM job artifact mapping and SAM runtime guards. Meta transport is mocked only in tests. No live Meta request was verified in this environment because no server key was configured. SAM was separately exercised against the installed model; see its smoke report.

This version is a local demonstration foundation. Camera footage must be captured in the browser or imported; there is no continuous real-time vision loop, automatic transcription pipeline, multi-user authentication or promise of production readiness. Audio container uploads are stored locally, but compatibility of each modality with the chosen Meta model must be checked against its current documentation.

## Primary documentation used

- [Meta Model API overview](https://dev.meta.ai/docs/overview): model names, authentication and base URL.
- [Meta Responses protocol](https://dev.meta.ai/docs/protocols/responses): request envelope and structured output location.
- [Meta structured output](https://dev.meta.ai/docs/structured-output): JSON schema behavior.
- [Meta video understanding](https://dev.meta.ai/docs/video-understanding): Files upload and input file reference.
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence): thread checkpoints and durable state.
- [LangGraph memory](https://docs.langchain.com/oss/python/langgraph/add-memory): SQLite development checkpointer.
