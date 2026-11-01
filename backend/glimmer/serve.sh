#!/usr/bin/env bash
# Muse Glimmer (Meta open weights, Apache-2.0) on the local GPU through vLLM's OpenAI-compatible API.
# Compinche uses it for learner questions over confirmed family knowledge (COMPINCHE_GLIMMER_URL).
# Image and flags follow Meta's cookbook: muse-glimmer/inference-server/vllm.md. ~60 GB bf16 weights.
set -euo pipefail
PORT="${GLIMMER_PORT:-8100}"
exec docker run --rm --name compinche-glimmer --gpus all --ipc=host \
  -p "127.0.0.1:${PORT}:8000" \
  -v "${HF_HOME:-$HOME/.cache/huggingface}:/root/.cache/huggingface" \
  -e HF_HUB_OFFLINE=1 \
  vllm/vllm-openai:muse-glimmer \
  meta-models/Muse-Glimmer-30B \
  --served-model-name muse-glimmer \
  --gpu-memory-utilization "${GLIMMER_GPU_FRACTION:-0.6}" --max-model-len "${GLIMMER_CONTEXT:-16384}" \
  --enable-auto-tool-choice --tool-call-parser muse_glimmer --reasoning-parser muse_glimmer \
  --generation-config auto
