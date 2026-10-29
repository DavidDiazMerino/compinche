# Compinche

### From their hands to yours.

**Capture how someone cooks. Keep the little things that make it theirs. Learn to do it in your own kitchen.**

Recipes leave things unsaid. “A little more.” “Until it feels right.” “Like this.” Compinche is an attentive apprentice for that knowledge: the gesture, the signal a cook notices, the reason behind a decision, and the explanation that makes it repeatable.

The first story is **Mom’s croquettes**. The ambition is bigger: help people pass on a way of doing things, with the original person and evidence still attached.

**Hackathon prototype · English interface · Mobile first · FastAPI + LangGraph · Meta Model API · Local SAM 3.1**

[Run locally](#run-locally) · [Try the demo](#a-three-minute-demo) · [Architecture](#how-it-works) · [What is working](#what-is-working) · [SAM evidence](#real-sam-output)

<table>
  <tr>
    <td align="center"><strong>Choose your experience</strong></td>
    <td align="center"><strong>Capture a recipe</strong></td>
    <td align="center"><strong>Learn a recipe</strong></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/home-mobile.jpg" width="240" alt="Compinche mobile home with Capture and Learn experiences"></td>
    <td><img src="docs/screenshots/capture-mobile.jpg" width="240" alt="Mobile recipe capture with camera controls and personal notes"></td>
    <td><img src="docs/screenshots/learn-mobile.jpg" width="240" alt="Mobile learning experience for Mom's croquettes"></td>
  </tr>
</table>

## Two halves of the same story

### 01 · Capture their way

Let the cook cook. Record a demonstration or import a video, add notes at the moments that matter, and keep the original on your device. Open a working copy with the local agent when you are ready.

With Meta connected, ask Compinche to observe selected evidence. It can connect a visible action with the conversation and ask one useful question at a time. An explanation becomes a candidate discovery with its source attached; a person confirms what should be kept.

### 02 · Learn their way

Start a separate learning session linked to the original recipe. Bring the teacher’s explanations into your own kitchen, add your attempt, and ask about the difference. The original demonstration and the learner’s evidence keep their own identities.

The included example also offers a six-moment cooking journey, a manual gesture comparison, and a circular-pan surface comparison based on measurements you enter.

## What is working

| Experience | Current status |
| --- | --- |
| English mobile interface | Implemented, with distinct Capture and Learn routes, bottom navigation, local fonts and responsive layouts. |
| Camera and import | Browser camera/microphone recording, pause/resume, imported clips, timestamped notes, saved recordings, drafts and downloads. Requires browser permission and a supported secure context. |
| Personal memory | Saved discoveries, notes, progress, search and JSON export. Video files are downloaded separately. |
| Local application | FastAPI serves the interface and API; SQLite stores sessions, evidence, questions, claims and jobs. |
| Conversational agent | Real LangGraph workflow with persistent checkpoints, explicit question/answer state and source validation. |
| Meta integration | Server adapter for the Meta Files and Responses APIs. **A live Meta turn has not been verified in this build environment because no API key was configured.** The app shows the missing connection instead of generating a pretend response. |
| SAM 3.1 | Real local segmentation adapter, exercised on a stock clip. The included output is a recorded test; new jobs require a separately configured compatible GPU runtime. |
| Example recipe | Scripted, clearly labelled demonstration using illustrative media. Its questions and discoveries are not presented as live model findings. |

## Run locally

Use **Python 3.12** and a modern browser. Node.js 18+ is needed only for the npm convenience commands and JavaScript checks. There is no frontend build step and no npm package installation.

On Linux or macOS:

```bash
git clone https://github.com/DavidDiazMerino/compinche.git
cd compinche
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.lock.txt
npm start
```

Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)**. The same server serves the application and its API. Interactive API documentation is at [/docs](http://127.0.0.1:8000/docs).

Without Node.js, use the equivalent command:

```bash
backend/.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

On Windows, create the same virtual environment and use `backend\.venv\Scripts\python.exe` in place of `backend/.venv/bin/python` for installation and the direct server command.

**No API key or GPU is needed to explore the example, capture/import a recipe, save notes or create local sessions.** Model features show their actual availability in the app’s connection screen and at `/api/health`.

### Connect Meta

Obtain an API key through the [Meta Model API](https://dev.meta.ai/docs/overview). Supply `MODEL_API_KEY` or `META_API_KEY` in the **server environment**, then restart the application. Keep the key out of browser code, Git and screenshots. The server does not automatically load `.env` files; [backend/.env.example](backend/.env.example) documents the supported names.

For a temporary Bash session, this reads the key without displaying it or putting its value in command history:

```bash
read -r -s -p "Meta API key: " MODEL_API_KEY
export MODEL_API_KEY
npm start
```

The default model is `muse-spark-1.3`, configurable with `COMPINCHE_META_MODEL`. The adapter uses `https://api.meta.ai/v1`, selected media uploads and structured JSON output. Its request format follows Meta’s [video understanding](https://dev.meta.ai/docs/video-understanding) and [Responses API](https://dev.meta.ai/docs/protocols/responses) documentation. A configured key becomes a live-verified connection only after a successful response.

### Enable local SAM

SAM is optional and separate from the Python web-server installation. It requires Docker with GPU access, FFmpeg, a compatible runtime image and authorized local SAM model weights. Checkpoints are not included in this repository and are not downloaded automatically.

Configure `COMPINCHE_SAM_IMAGE`, `COMPINCHE_SAM_CACHE` and `COMPINCHE_GPU_LOCK` for your machine. The current adapter was validated on a GB10 host and uses conservative memory guards: at least 42 GiB available to start, stopping below 20 GiB. This is not a claim that the same setup runs on every laptop or GPU.

See [the SAM runtime guide](backend/SAM.md) for configuration, output contracts and limits. An optional [Docker build recipe](backend/Dockerfile.sam) is included but has not been built or validated; the recorded test used an existing compatible runtime. The official [SAM 3 repository](https://github.com/facebookresearch/sam3) and [SAM 3.1 release notes](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md) cover model access and upstream setup.

## A three-minute demo

| Time | Show | Story to tell |
| --- | --- | --- |
| 0:00–0:25 | Home → Capture / Learn | “Some things are written in a recipe. Some things live in the person making it.” |
| 0:25–1:00 | Capture a short clip or import one; add a note; save | Keep the cook’s original demonstration and the details you noticed. Camera access begins with an explicit action. |
| 1:00–1:40 | Open the local agent; select evidence; observe and answer | With a configured Meta key, show a complete observation → question → answer exchange and human confirmation. Without a key, use the labelled example and explain that the connection is not enabled. |
| 1:40–2:05 | Track an object or show the included SAM test | A real mask follows a saucepan. Explain which result is pre-recorded and which request is running now. |
| 2:05–2:40 | Learn from the recipe; add an attempt; compare | A new cook has a separate session with the original lesson available. The manual pan comparison uses entered diameters. |
| 2:40–3:00 | Memories and original evidence | End with the person behind the knowledge: “I wanted to learn to cook like her.” |

Use a short, consented demonstration for a live capture. The packaged cooking montage is stock footage; the app labels it as an example. Live inference time is variable, so keep the recorded SAM evidence available for the presentation.

## How it works

```mermaid
flowchart TD
    Person["Cook or learner"] --> UI["Mobile-first browser app"]
    UI -->|"Record, import and save"| Browser["IndexedDB: originals and drafts<br/>localStorage: notes and preferences"]
    UI -->|"Explicit local upload"| API["FastAPI: same-origin local API"]
    API --> DB["SQLite: sessions, evidence,<br/>questions, claims and jobs"]
    API -->|"Conversation turn"| Retrieve["LangGraph: retrieve context"]
    Retrieve --> Meta["Meta: selected media + conversation"]
    Meta --> Validate["Validate schema, sources<br/>and question state"]
    Validate --> Remember["Persist accepted turn<br/>and unconfirmed claims"]
    Remember --> DB
    API -->|"Explicit tracking request"| SAM["Local SAM 3.1<br/>offline GPU worker"]
    SAM --> Artifacts["Masks, overlay, timestamps<br/>and source provenance"]
    Artifacts --> DB
    DB -->|"Original recipe + separate learner session"| UI
```

The agent follows four explicit steps: **retrieve → reason → validate → remember**. Each session has a persistent LangGraph checkpoint and application records in SQLite. Learning sessions reference the source recipe without merging teacher and learner messages. See the [backend guide](backend/README.md) for API contracts and the [LangGraph persistence documentation](https://docs.langchain.com/oss/python/langgraph/persistence) for the underlying checkpoint model.

Every candidate discovery distinguishes **observed**, **said** and **inferred** information. Source IDs stay attached. Deterministic checks reject unknown evidence, unsupported common physical quantities, invented quotations and some unsupported visual or food-safety claims. A pending question has explicit state; the person’s answer can close it. Only a human can confirm a discovery. These checks constrain the model; they do not prove that every generated interpretation is correct.

SAM provides a different kind of evidence: object masks, pixel coordinates and tracking across frames. It does not establish weight, physical dimensions, temperature, tactile resistance or doneness.

## Real SAM output

[![Actual SAM 3.1 saucepan tracking test on stock footage](dist/assets/sam-test-poster.jpg)](dist/assets/sam-test-overlay.mp4)

[Watch the recorded overlay](dist/assets/sam-test-overlay.mp4) · [Inspect the JSON report](dist/assets/sam-test-result.json)

A fresh local integration test on **29 October 2026** processed a two-second stock clip at four frames per second and returned saucepan masks in eight frames. Total execution was **23.615 seconds**, including **11.656 seconds** loading the model. These are one test’s measurements, not an accuracy benchmark or a real-time performance claim.

The public sample includes the overlay, poster and report; individual mask files remain in the local test artifacts. New jobs analyze at most the **first eight seconds**, at **4 fps**, up to **640 px** wide and **32 frames**. See [SAM.md](backend/SAM.md) for the full evidence record.

## Data, privacy and current limits

| Action | Where data goes |
| --- | --- |
| Record, import, save a draft or personal note | This browser’s storage. Nothing is uploaded by recording alone. |
| Open a recording with the local agent | A working copy is saved under the local server’s data directory. |
| Send a conversation turn | Bounded conversation context is sent to Meta when the connection is configured. |
| Ask Meta to observe selected media | Those selected files are uploaded to Meta along with context. Provider processing and retention terms apply. |
| Run SAM tracking | The local runtime processes server-owned media; the model worker has no network access. |

Server data defaults to `backend/.data/`; `COMPINCHE_DATA_DIR` can change it. Browser storage and server storage are separate. Keep video downloads or backups you control: browser storage can be cleared, and abruptly closing a browser during recording can lose an unfinished take.

The Meta adapter requests `store:false`. That is not a promise of immediate deletion of uploaded Files objects; the current adapter does not delete remote files. LangSmith tracing is disabled to avoid inheriting a tracing configuration that would export conversations.

This is a **local development application**. Keep it bound to `127.0.0.1`; it has no account system, multi-user authorization or public deployment configuration. Origin and Host checks are additional boundaries, not authentication. The responsive interface can be inspected in browser mobile emulation. Using the camera on a separate phone would need a deliberately configured HTTPS and authenticated deployment; this release does not expose one.

Current boundaries:

- Uploads are limited to **150 MiB**; interactive Meta attachments to **40 MiB** each. Supported media types and browser playback still matter.
- There is no continuous live-video agent loop, voice conversation or dedicated automatic transcription workflow.
- Gesture playback is manually compared. The pan tool calculates circular surface-area ratios from entered measurements; it does not measure from images or validate cooking adaptations.
- Automatic 3D reconstruction, physical scale, ingredient weights, thermal modelling and reliable doneness evaluation remain research directions.
- Example questions and discoveries are authored from the story script. A real family teaching session and an independent learner evaluation are still needed to validate the broader product idea.

## Development

```bash
npm run check
npm test
```

**Release validation:** 31 backend tests pass. The browser flow was also exercised from stock-video import through notes, a fresh SAM job and a linked learning session; the second real segmentation run completed in 24.418 seconds with detections in eight of eight sampled frames. Mobile layouts were checked at 320 and 390 CSS-pixel viewport settings. Camera hardware and a live Meta turn remain unverified.

`check` validates the frontend JavaScript modules. `test` runs the backend tests with the project’s Python environment. Tests cover session persistence, conversation continuity, question state, evidence boundaries, confirmations, API behavior and SAM runtime guards. Meta transport is mocked in tests; that is separate from a live provider verification. [GitHub Actions](.github/workflows/ci.yml) runs the JavaScript and backend checks without API keys or GPU inference.

```text
dist/                    Browser application; no build step
  app.js                 Routes, interaction and session UI
  capture.js             Camera, imports, drafts and IndexedDB
  api.js                 Client for the local backend
  assets/                Illustrative media, fonts and SAM sample
backend/
  app.py                 FastAPI routes and local server
  agent.py               LangGraph workflow and validation
  provider.py            Meta Model API adapter
  store.py               Durable application records
  sam_service.py         Bounded local segmentation jobs
  sam_worker.py          Isolated SAM inference worker
  tests/                 API, state and integration-boundary tests
docs/screenshots/        Screenshots of the actual interface
```

The next useful experiments are a consented end-to-end teaching session, a learner repeating an unfamiliar gesture, measured tests of adaptations to another pan, and a live Meta evaluation across varied cooking footage. Each should retain inputs, questions, corrections and failure cases.

## Credits and license

Application code is released under the [MIT License](LICENSE). Third-party assets and model weights retain their own terms; they are not relicensed by the application license.

Cooking footage comes from **Pressmaster**, **Taryn Elliott** and **cottonbro studio** on Pexels. The cover image is AI-generated and illustrative. Asset-level sources are recorded in [credits.json](dist/assets/credits.json). DM Sans and DM Serif Display are bundled with their respective [font license](dist/assets/DM-Sans-LICENSE.txt) [files](dist/assets/DM-Serif-Display-LICENSE.txt).

Built with [FastAPI](https://fastapi.tiangolo.com/), [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview), the [Meta Model API](https://dev.meta.ai/docs/overview), and [Segment Anything](https://github.com/facebookresearch/sam3). This independent hackathon project does not imply endorsement by these projects or by the people shown in the stock footage.
