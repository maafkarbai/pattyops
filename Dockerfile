# syntax=docker/dockerfile:1

# The inference server image already contains the matching Roboflow client,
# WebRTC, OpenCV, and Python runtime. Docker reuses these layers for both
# services instead of installing the vision stack twice.
FROM roboflow/roboflow-inference-server-cpu:latest

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pattyops_roboflow.py pattyops_core.py ./

ENTRYPOINT ["python", "-u", "pattyops_roboflow.py"]
