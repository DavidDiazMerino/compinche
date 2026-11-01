# SAM integration

`SamService(data_root)` runs fresh SAM 3.1 video segmentation on media uploaded to the local backend. It does not replay the earlier Compinche experiment's masks. The worker runs offline in an operator-configured Docker runtime, loading previously downloaded official weights.

## Contract

```python
service = SamService(data_root)
service.status()  # ready | busy | unavailable, configured, model, limits
result = await service.segment(data_root / "media" / "uploaded.mp4", "saucepan", generated_job_id)
```

Targets are allowlisted: `saucepan`, `spoon`, `food` (`food` retains the SAM prompt `sauce`), `hand`, `dough` and `croquette`. `start_seconds` selects the interval in the uploaded file; `duration_seconds` is greater than zero and at most eight (default eight). Sampling remains four frames per second, at most 640 pixels wide and 32 frames. The UI and replay experiment request two seconds by default to reduce unified-memory demand. It is a bounded asynchronous job, not a real-time camera loop.

Per-frame `seconds` is relative to the analysis window; `source_seconds` is
relative to the uploaded video, including the selected offset. A replay clip's
`timestamp_seconds` separately anchors that uploaded clip to the full original.
The agent receives bounded pixel samples with these media IDs and both time
origins. They are candidate masks, not evidence of texture or readiness.

Artifacts are written beneath `data_root/artifacts/<job_id>/`: a browser-playable H.264 `overlay.mp4`, `poster.jpg`, binary PNG masks, and `result.json`. Each result contains timestamps, mask/object IDs, pixel counts, bounding boxes `[x, y, width, height]`, model scores, source hash, execution time and runtime provenance. The API layer maps artifact basenames to controlled local URLs.

## Runtime

The optional GPU path requires Linux, Docker with NVIDIA Container Toolkit, host FFmpeg, an NVIDIA GPU compatible with the runtime, and pre-cached SAM checkpoints. The app and capture/learn interface run without this path; the status screen reports SAM as unavailable until configured.

Portable defaults:

- Docker image: `compinche-sam:local`.
- Hugging Face cache: `~/.cache/huggingface/hub` (mounted read-only).
- Shared GPU lock: `~/.cache/compinche/gpu.lock.d`.

Override these with `COMPINCHE_SAM_IMAGE`, `COMPINCHE_SAM_CACHE` and `COMPINCHE_GPU_LOCK`. Use the same lock path as other GPU applications on a shared machine. These are server configuration, never browser input. The adapter does not pull images, download checkpoints, stop other services or take over their locks.

### Prepare a runtime

[Meta's installation instructions](https://github.com/facebookresearch/sam3#installation) specify Python 3.12+, a supported CUDA/PyTorch environment, and checkpoint access through Hugging Face. Follow their access and license requirements for both [SAM 3](https://huggingface.co/facebook/sam3) and [SAM 3.1](https://huggingface.co/facebook/sam3.1). Download the authorized checkpoints **before** starting the app. Keep Hugging Face credentials outside the repository and image.

An optional [`Dockerfile.sam`](Dockerfile.sam) provides a build starting point with NVIDIA PyTorch 26.02 and a pinned official SAM source commit. It does not bundle weights:

```sh
# From the repository root; image build requires network access.
docker build -f backend/Dockerfile.sam -t compinche-sam:local .

# Select another previously prepared runtime/cache if needed.
export COMPINCHE_SAM_IMAGE=compinche-sam:local
export COMPINCHE_SAM_CACHE="$HOME/.cache/huggingface/hub"
export COMPINCHE_GPU_LOCK="$HOME/.cache/compinche/gpu.lock.d"
```

**Build status:** the optional Dockerfile has not been built or GPU-validated as part of this release. The successful test below used an existing local runtime with PyTorch `2.11.0a0+eb65b36914.nv26.2`, torchvision `0.25.0a0+1e53952f.nv26.2.44259020`, NumPy `1.26.4`, OpenCV `4.11.0.86`, and SAM package version `0.1.0`. Its exact image digest is preserved in the sample report. Validate your prepared image on a short, non-sensitive clip before a live demonstration.

The checkpoint requires a multiplex capacity of 16. The installed package's optional `init_state` arguments are filtered against its actual signature, as in the earlier successful probe. Inference is uncompiled and cold-loads per job; the CUDA process exits afterward to release memory.

## Boundaries

Only paths inside the server media directory and generated job identifiers are accepted. There is no shell interpolation. FFmpeg is limited to local file/pipe protocols, and model inference has no network. Jobs are serialized, respect the configured shared GPU lock, have a 300-second model execution limit, and stop the owned container if free unified memory falls below 20 GiB. The current conservative policy requires at least 42 GiB of free system memory to start; it was validated on a GB10 unified-memory host. Other hardware configurations need separate validation. Other applications are not stopped.

SAM masks and scores are model predictions. They do not establish grams, centimetres, temperature, tactile resistance, food safety or doneness. Object continuity can fail under occlusion. These limits accompany every result so an agent cannot equate segmentation with physical understanding.

## Validation

Focused tests cover path escape/symlink rejection, target and job ID validation, concurrency, unavailable runtime, artifact mapping, and ownership-safe GPU lock release.

```sh
python -m unittest discover -s backend/tests -p test_sam_service.py -v
```

## Primary references

- https://github.com/facebookresearch/sam3
- https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md
- https://github.com/facebookresearch/sam3/blob/main/examples/sam3.1_video_predictor_example.ipynb

The March 27, 2026 SAM 3.1 release introduces Object Multiplex and new checkpoints. The local API follows the predictor's session/prompt/propagation interface and keeps the evidence and measurements separate.

### Fresh integration smoke result — 29 October 2026

A new job executed through `SamService.segment`, not the old probe script, on two seconds of the existing Pexels 4253326 stock source (cottonbro studio). It processed eight frames at 4 fps and returned a saucepan mask in eight frames. Total execution was 23.615 seconds, including 11.656 seconds loading the model. The playable overlay decoded successfully; the poster and all referenced masks were inspected/validated. No accuracy metric follows from this count and the test is not a real-time latency claim.

The public sample is in `dist/assets/sam-test-result.json`, with `sam-test-overlay.mp4` and `sam-test-poster.jpg`. The full mask set stays in the local test artifacts and is not included in the public bundle. Source SHA-256: `76d808d7e61ed417ccdac1d29429fe62fe6526dd5499e141da4f099574bb9acd`. This source is stock footage, not a family session. The GPU lock was released after the job.
