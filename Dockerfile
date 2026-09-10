FROM node:22-bookworm-slim AS frontend
WORKDIR /build/static
COPY static/package.json static/package-lock.json ./
RUN npm ci
COPY static ./
RUN npm run build:css && npm prune --omit=dev

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY app ./app
COPY --from=frontend /build/static ./static
COPY tools ./tools
COPY runs/yolo_world/robot_arm_parts_v1/weights/best.pt ./runs/yolo_world/robot_arm_parts_v1/weights/best.pt

EXPOSE 8090 8100

CMD ["python", "-m", "uvicorn", "app.video_main:app", "--host", "0.0.0.0", "--port", "8090"]
