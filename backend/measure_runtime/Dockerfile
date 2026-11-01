# Metric geometry runtime for Compinche (MapAnything, Meta, Apache-2.0 weights).
# Built on the local aarch64 CUDA PyTorch image already used for SAM; torch is not reinstalled.
ARG BASE=footballai-spark-training:2026-08
FROM ${BASE}
RUN python3 -m pip install --no-cache-dir --no-deps "git+https://github.com/facebookresearch/map-anything.git@main" \
 && python3 -m pip install --no-cache-dir --no-deps "uniception==0.1.7" \
 && python3 -m pip install --no-cache-dir hydra-core natsort orjson pillow-heif plyfile python-box trimesh safetensors huggingface_hub jaxtyping einops termcolor scikit-learn minio timm matplotlib
WORKDIR /work
