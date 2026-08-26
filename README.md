# Octopus Video Service

Octopus Video Service provides video upload, parsing, business-frame extraction, Markdown export, and a remote MCP server for external clients.

## Services

- Video API: FastAPI service, default port `8090`.
- MCP Server: Streamable HTTP MCP service, default port `8100`, default path `/mcp`.
- Storage: MinIO/S3-compatible object storage.
- Database: PostgreSQL database named `octopus_video` by default.

## MCP Tools

- `upload_and_parse_video`: wraps `POST /api/videos` and `POST /api/videos/{video_id}/parse`.
- `export_video_result`: wraps `GET /api/videos/{video_id}/export`.
- `detect_image_objects_and_mask`: wraps `POST /api/semantic-frames/image`.

The remote MCP endpoint is:

```text
http://127.0.0.1:8100/mcp
```

Set `OCTOPUS_MCP_BEARER_TOKEN` to require:

```text
Authorization: Bearer <token>
```

## Local Run

Install dependencies and start the video API:

```powershell
python -m pip install -r requirements.txt
copy .env.example .env
.\run.ps1
```

Start the MCP server in another terminal:

```powershell
.\run_mcp.ps1
```

Open the video UI:

```text
http://127.0.0.1:8090/
```

## Docker: Full Stack

Use this when the server should start PostgreSQL, MinIO, the Video API, and the MCP server together.

```bash
cp .env.full.example .env
docker compose up -d --build
```

Services:

- `postgres`: PostgreSQL for `octopus_video`.
- `minio`: object storage, API port `9000`, console port `9001`.
- `octopus-api`: video API on port `8090`.
- `octopus-mcp`: remote MCP on port `8100`.

## Docker: External Database And MinIO

Use this when PostgreSQL and MinIO already exist.

```bash
cp .env.external.example .env
docker compose -f docker-compose.external.yml up -d --build
```

Required variables:

- `VIDEO_DATABASE_URL`
- `OBJECT_STORE_ENDPOINT`
- `OBJECT_STORE_ACCESS_KEY`
- `OBJECT_STORE_SECRET_KEY`
- `OBJECT_STORE_BUCKET`

## GPU Deployment

The default Docker compose files use CPU so they can start on ordinary servers. For GPU business-frame detection/mask, install NVIDIA Container Toolkit on the server and run with the GPU override:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

For external database/MinIO mode:

```bash
docker compose -f docker-compose.external.yml -f docker-compose.gpu.yml up -d --build
```

Make sure `.env` has:

```text
VIDEO_GRAPH_INDEX_DEVICE=cuda
```

## Model Files

Model weights are not committed to Git or baked into the Docker image. Put them in the host model directory and mount that directory into `/models`.

Default `.env` values:

```text
OCTOPUS_MODEL_DIR=./models
VIDEO_GRAPH_INDEX_FINETUNED_YOLO_WORLD_MODEL=/models/yolov8s-world.pt
VIDEO_GRAPH_INDEX_MOBILE_SAM_MODEL=/models/sam2.1_b.pt
```

Expected host files:

```text
models/yolov8s-world.pt
models/sam2.1_b.pt
```

## Public URLs

Inside Docker, the MCP server calls the API through the compose network:

```text
OCTOPUS_VIDEO_API_BASE_URL=http://octopus-api:8090
```

For URLs returned to external clients, set:

```text
OCTOPUS_VIDEO_PUBLIC_BASE_URL=https://your-domain.example
```

If this is left as `http://127.0.0.1:8090`, remote clients will not be able to open returned image or result JSON URLs.

## Important Files

- `app/video_main.py`: FastAPI video service.
- `app/mcp_server.py`: remote MCP server.
- `docker-compose.yml`: full stack deployment.
- `docker-compose.external.yml`: app-only deployment with external PostgreSQL and MinIO.
- `docker-compose.gpu.yml`: optional GPU override.
