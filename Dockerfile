# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 libgomp1 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements-docker.txt ./
RUN python -m pip install --upgrade pip && \
    python -m pip install -r requirements-docker.txt

COPY pattyops.py pattyops_core.py ./

ENTRYPOINT ["python", "pattyops.py"]
