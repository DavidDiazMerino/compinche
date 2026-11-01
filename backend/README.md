Continuous teaching now has its own causal coordinator and bounded HTTP/PCM WebSocket routes. See [the live flow](../docs/LIVE.md) for operation, measurements, consent, source isolation and remaining device validation. The manual session endpoints below remain supported for saved-source review.

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

### Recorded demonstration experiment

The croquetas experiment reuses the real `CookingAgent` graph. It prepares three
bounded windows from Loli Cocina's public video, each with an audio/subtitle and a
silent variant. Each variant has its own conversation so automatic captions and
answers cannot leak into the silent comparison. The review rubric is withheld
from the model. Automatic subtitles remain unconfirmed transcript evidence.

From `application/`, after downloading the MP4 and Spanish VTT into the paths in
`docs/experiments/croquetas.json`:

```sh
backend/.venv/bin/python -m backend.replay docs/experiments/croquetas.json \
  --data-dir backend/.data/croquetas-replay --env-file backend/.env.local --sam
```

`--env-file` explicitly loads the named local file, without shell evaluation.
Add `--live` to send the six prepared clips to Meta using `MODEL_API_KEY` or
`META_API_KEY`. Without a key the report records `blocked_missing_api_key`; it
does not generate placeholder questions. Completed cases are reused on rerun.
SAM uses two seconds at 4 fps per target to limit unified memory. Silent variants
reuse masks only after matching the SHA-256 of decoded video pixels; their
provenance names the original inference. There are no human answers invented by
the runner. The graph retains unanswered questions until a person answers them.

Outputs are `replay-report.json`, playable clips, mask artifacts and `review.html`
under the chosen data directory. The HTML can be opened locally. To review the
same sessions in the app, start the server with
`COMPINCHE_DATA_DIR=backend/.data/croquetas-replay` (resolved from `application/`)
and visit `/api/replay-review`. Stop that server before resuming a replay, so its
store recovery does not interfere with active jobs. Source videos and results
remain in ignored local directories; the source is not bundled or published.

The mechanical evaluation checks schema, references and question count. Question
usefulness, redundancy and timing still require human review of actual Meta
responses. Passing these checks is not evidence of good teaching questions.

Use `--only formado --revision cues-v2 --live` to keep an isolated revision
beside the baseline. The revision reuses verified identical clip pixels and mask
artifacts while keeping notes, questions and answers in new sessions. One
generation per condition is an exploratory comparison, not a statistical eval.

Local run, 30 October 2026: all six clips were prepared. Fresh two-second SAM
windows returned hand candidates in 8/8 frames at original 08:23, and spoon/sauce
candidates in 4/8 frames at 05:32. The `croquette` prompt returned 0/8 frames at
08:39 and 10:34, even though the original frames show croquettes. These counts
describe detections, not segmentation accuracy. Earlier zero-detection windows
and the aborted eight-second memory-limited attempt remain in the local report.
The live API returned HTTP 503 `meta_not_configured`; no real Meta questions were
generated or evaluated. The backend suite passed 37 tests, and JavaScript syntax
checks passed.

Later live run, 30 October 2026: a server key enabled all six baseline calls and
two isolated forming revisions with `muse-spark-1.3`. The adapter now explicitly
uses low reasoning effort and a 6000-token combined reasoning/output limit;
the initial default-effort attempts returned incomplete responses. Accepted
clip calls took 14–23 seconds including media upload. Full completed-response
usage and status are recorded with each case, without credentials or private
reasoning content.

The baseline asked for a stop signal in the béchamel and oil cues in frying. Its
forming questions were weaker: one repeated a demonstrated sequence and another
combined portion size with compactness. The revised prompt prioritises a single
missing decision; the validator rejects common compound-question forms. In the
two forming revisions, the real questions concerned portion consistency and
the feel before coating. The original replies remain available for comparison.
These are editorial observations, not confirmations by the original cook. The
updated backend suite passes 41 tests.

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
