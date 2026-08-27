# Octopus 视频理解与业务帧抽取服务

## 项目介绍

Octopus 是一套面向**操作类、培训类和工业场景视频**的视频理解与内容整理服务。它将视频中的语音、画面和时间信息关联起来，把一段较长、难以快速检索的视频，转换为结构化转写、业务帧、图文 Markdown 和可供其他系统继续处理的 JSON 数据。

这里的“业务帧”不是简单地按固定时间间隔截取的普通关键帧，而是指**最能说明某个业务步骤、操作动作或讲解段落的画面**。系统会结合转写文本、段落时间范围、目标检测结果、mask、目标位置关系和图像质量，对候选画面进行过滤、去重、匹配和排序，从而为每个业务段落找到更有说明价值的图片。


### 从视频到结果的处理流程

```text
上传视频
  -> 生成分析用视频
  -> ASR 语音转写
  -> Qwen 整理结构化段落和查询图
  -> FFmpeg 抽取候选帧
  -> 图像质量过滤与 HSV + pHash 去重
  -> 微调 YOLO-World 目标检测
  -> SAM 目标 mask 分割
  -> 按段落语义、目标和空间关系匹配业务帧
  -> 生成结构化结果、Markdown 和导出包
```

### 主要产出

- 带时间范围和业务描述的结构化转写文本。
- 每个段落的候选业务帧、匹配分数、检测目标和关系信息。
- 目标检测框图片、mask 图片及相应 JSON 数据。
- 自动关联业务帧的 Markdown 分析文档。
- 包含 `result.md`、`frames/` 和 `manifest.json` 的自包含 ZIP 结果包。

### 使用方式

- **Web 页面**：适合人工上传视频、查看处理进度和检查结果。
- **HTTP API**：适合业务系统、工作流平台和脚本调用。
- **远程 MCP Server**：适合支持 MCP 的智能客户端或 AI Agent 调用。

### 当前适用边界

- Octopus 聚焦离线或异步的视频内容分析与结果服务，不提供视频剪辑、通用媒体管理、实时视频监控或实时告警能力。
- 仓库自带的微调 YOLO-World 模型主要识别机械臂装配相关零部件；迁移到其他业务领域时，需要准备相应的检测模型、类别和查询图规则。
- 完整解析依赖 PostgreSQL、MinIO、FFmpeg、本地 ASR、Qwen 模型服务以及 YOLO + SAM；目标检测与分割建议使用 GPU。

## 核心能力

- 上传视频并按需执行完整解析、仅转写、仅业务帧抽取或仅 Markdown 生成。
- 生成包含段落、时间范围、业务描述和查询图的结构化转写。
- 从候选画面中筛选、去重并排序与业务内容最相关的帧。
- 生成目标检测框、mask、目标属性、空间关系和匹配分数。
- 生成 Markdown 分析结果，并导出包含文档和业务帧图片的结果包。
- 对单张图片执行目标检测与分割，返回标注图片和结构化 JSON。
- 通过 Web、HTTP API 和 MCP 工具开放上述能力。

## 整体架构

```text
外部 MCP 客户端
  -> Octopus MCP Server /mcp
  -> Octopus Video API
  -> PostgreSQL / MinIO / 本地 ASR / Qwen 模型 / YOLO + SAM
```

服务分为两层：

- `octopus-api`：真正执行业务逻辑的视频分析服务，默认端口 `8090`。
- `octopus-mcp`：远程 MCP 服务，把视频服务的 HTTP API 封装成 MCP 工具，默认端口 `8100`，默认路径 `/mcp`。

## 功能说明

### 1. 上传视频并解析

对应 HTTP API：

```text
POST /api/videos
POST /api/videos/{video_id}/parse
POST /api/videos/upload-and-parse
```

对应 MCP 工具：

```text
upload_and_parse_video
```

实现方式：

- `POST /api/videos` 接收视频文件并上传到 MinIO。
- 数据库 `video_jobs` 保存视频任务元数据。
- `POST /api/videos/{video_id}/parse` 启动后台解析流程。
- `POST /api/videos/upload-and-parse` 在一个请求内依次完成视频上传和 `full` 完整解析，只在解析成功或失败后返回最终状态，不会在上传完成时提前返回。调用方可以同时提交自己预先生成的 `task_id`，并用该 ID 查询执行进度。
- 解析流程根据 `parse_mode` 执行：
  - `full`：完整解析，包含转写、业务帧筛选、Markdown 生成。
  - `transcript`：只生成转写文本。
  - `keyframes`：只抽取并筛选业务帧。
  - `markdown`：只生成 Markdown。

主要产物：

- 视频任务 ID：`video_id`
- 结构化转写文本
- 业务帧图片
- Markdown 分析结果

`POST /api/videos/upload-and-parse` 是长耗时请求。客户端可以用异步 HTTP 调用避免阻塞自身界面，但 HTTP 连接必须保持到解析结束；客户端、网关和反向代理需要配置足够长的请求超时时间。需要查看运行进度时，客户端应在上传前生成 32 位小写 UUID v4 hex 字符串，通过 multipart 字段 `task_id` 一起提交，然后每 2 秒调用一次 `GET /api/videos/{task_id}`，在状态变为 `completed`、`completed_with_warnings` 或 `failed` 后停止轮询。视频尚在网络上传或保存到 MinIO、数据库任务还未创建时，GET 暂时返回 `404` 属于正常现象。

### 2. 转写文本

实现方式：

- 使用本地 ASR 服务生成原始转写。
- 使用 Qwen 文本模型对原始 ASR 结果做结构化整理。
- 结果保存为 JSON 和可读文本，并上传到 MinIO。

相关配置：

```text
LOCAL_ASR_API_BASE_URL
LOCAL_ASR_MODEL_LABEL
QWEN_TEXT_MODEL
TRANSCRIPT_STRUCTURE_MODEL
MODEL_API_KEY
```

### 3. 筛选业务帧

实现方式：

- 使用 FFmpeg 从视频中抽取候选帧。
- 使用图像质量过滤和去重逻辑减少重复帧。
- 使用微调后的 YOLO-World 模型做目标检测。
- 使用 `sam2.1_b.pt` 做 mask 分割。
- 根据检测目标、mask、位置关系和结构化转写内容筛选业务帧。

模型说明：

```text
YOLO 微调模型：runs/yolo_world/robot_arm_parts_v1/weights/best.pt
SAM 模型：sam2.1_b.pt
```

其中 YOLO 微调模型小于 100MB，已随仓库提交；SAM 模型较大，不提交到 Git，需要部署时放到模型目录并挂载。

### 4. 生成 Markdown

实现方式：

- 基于结构化转写树生成 Markdown。
- Markdown 中会关联业务帧图片。
- Markdown 文件保存到 MinIO。

对应 HTTP API：

```text
GET /api/videos/{video_id}/markdown
```

### 5. 导出结果包

对应 HTTP API：

```text
GET /api/videos/{video_id}/export
```

对应 MCP 工具：

```text
export_video_result
```

导出内容是一个 zip 包，包含：

- `result.md`：视频分析 Markdown。
- `frames/`：业务帧图片。
- `manifest.json`：结构化索引信息。

### 6. 单图目标检测和 mask

对应 HTTP API：

```text
POST /api/semantic-frames/image
```

对应 MCP 工具：

```text
detect_image_objects_and_mask
```

输入一张图片，输出：

- `bbox_image_url` / `bbox_image_object_key`
- `mask_image_url` / `mask_image_object_key`
- `result_json_url` / `result_json_object_key`
- `objects`
- `quality`
- `mask_available`
- `model_info`

`objects` 中包含：

- `label`
- `confidence`
- `bbox`
- `mask_bbox`
- `mask_area_ratio`
- `centroid_x`
- `centroid_y`
- `touches_border`
- `mask_confidence`

## MCP Server

本项目提供远程 MCP Server，使用 Streamable HTTP 方式对外提供服务。

默认 MCP 地址：

```text
http://服务器IP:8100/mcp
```

健康检查地址：

```text
http://服务器IP:8100/health
```

MCP 工具列表：

```text
upload_and_parse_video
export_video_result
detect_image_objects_and_mask
```

如果配置了鉴权：

```text
OCTOPUS_MCP_BEARER_TOKEN=your-token
```

外部客户端需要带请求头：

```text
Authorization: Bearer your-token
```

注意：浏览器直接打开 `/mcp` 看不到工具列表是正常的。MCP 工具需要通过支持 MCP 的客户端查看，例如 MCP Inspector、Cursor、Claude Desktop、Claude Code 等。

## Windows 本地运行

要求：

- Python 3.11+
- PostgreSQL
- MinIO
- 可访问的本地 ASR 服务
- 可访问的 Qwen 模型服务

安装依赖：

```powershell
python -m pip install -r requirements.txt
copy .env.example .env
```

根据本机环境修改 `.env`，然后启动服务。该命令会同时启动视频 API 和 MCP Server：

```powershell
.\scripts\run.ps1
```

访问 Web 页面：

```text
http://127.0.0.1:8090/
```

访问 API 文档：

```text
http://127.0.0.1:8090/docs
```

访问 MCP 健康检查：

```text
http://127.0.0.1:8100/health
```

## Linux 本地运行

要求：

- Python 3.11+
- PostgreSQL
- MinIO
- 可访问的本地 ASR 服务
- 可访问的 Qwen 模型服务

安装依赖：

```bash
python -m pip install -r requirements.txt
cp .env.example .env
```

根据本机环境修改 `.env`，然后启动服务。该命令会同时启动视频 API 和 MCP Server：

```bash
./scripts/run.sh
```

访问：

```text
http://127.0.0.1:8090/
http://127.0.0.1:8090/docs
http://127.0.0.1:8100/health
```

如果脚本没有执行权限：

```bash
chmod +x scripts/run.sh
```

## Docker 部署方式一：完整一键部署

适合新服务器从零部署。该方式会同时启动：

- PostgreSQL
- MinIO
- Octopus Video API
- Octopus MCP Server

准备配置：

```bash
cp .env.full.example .env
```

修改 `.env` 中的密码、模型服务地址、公网访问地址等配置。

启动：

```bash
docker compose up -d --build
```

查看服务：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f octopus-api
docker compose logs -f octopus-mcp
```

默认端口：

```text
Video API: http://服务器IP:8090
MCP:       http://服务器IP:8100/mcp
MinIO:     http://服务器IP:9000
MinIO UI:  http://服务器IP:9001
Postgres:  服务器IP:5432
```

## Docker 部署方式二：已有数据库和 MinIO

适合你们已经有 PostgreSQL 和 MinIO 的环境。该方式只启动：

- Octopus Video API
- Octopus MCP Server

准备配置：

```bash
cp .env.external.example .env
```

必须修改：

```text
VIDEO_DATABASE_URL
OBJECT_STORE_ENDPOINT
OBJECT_STORE_ACCESS_KEY
OBJECT_STORE_SECRET_KEY
OBJECT_STORE_BUCKET
```

启动：

```bash
docker compose -f docker-compose.external.yml up -d --build
```

查看日志：

```bash
docker compose -f docker-compose.external.yml logs -f octopus-api
docker compose -f docker-compose.external.yml logs -f octopus-mcp
```

## GPU 部署

默认 Docker 配置使用 CPU，保证普通服务器也能启动。

如果需要在 Docker 中使用 GPU 跑 YOLO 和 SAM，需要服务器安装 NVIDIA Container Toolkit，然后叠加 GPU 配置：

完整部署版：

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

外部数据库和 MinIO 版：

```bash
docker compose -f docker-compose.external.yml -f docker-compose.gpu.yml up -d --build
```

`.env` 中建议设置：

```text
VIDEO_GRAPH_INDEX_DEVICE=cuda
```

## 模型文件

YOLO 微调模型已随仓库提供：

```text
runs/yolo_world/robot_arm_parts_v1/weights/best.pt
```

Docker 镜像会把该文件复制到：

```text
/app/runs/yolo_world/robot_arm_parts_v1/weights/best.pt
```

SAM 模型不随仓库提供，需要部署时准备：

```text
models/sam2.1_b.pt
```

Docker 默认挂载：

```text
OCTOPUS_MODEL_DIR=./models
VIDEO_GRAPH_INDEX_MOBILE_SAM_MODEL=/models/sam2.1_b.pt
```

## 关键环境变量

### 应用和 MCP

```text
APP_ENV=prod
OCTOPUS_VIDEO_PUBLIC_BASE_URL=http://服务器IP:8090
OCTOPUS_MCP_PORT=8100
OCTOPUS_MCP_PATH=/mcp
OCTOPUS_MCP_BEARER_TOKEN=
```

`OCTOPUS_VIDEO_PUBLIC_BASE_URL` 很重要。MCP 返回图片 URL 和 JSON URL 时会使用这个地址。如果部署给外部客户端访问，不要使用 `127.0.0.1`。

### 数据库

```text
VIDEO_DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/octopus_video
```

### MinIO

```text
STORAGE_BACKEND=minio
OBJECT_STORE_ENDPOINT=http://minio:9000
OBJECT_STORE_ACCESS_KEY=minioadmin
OBJECT_STORE_SECRET_KEY=minioadmin
OBJECT_STORE_BUCKET=servforce-materials
OBJECT_STORE_REGION=us-east-1
OBJECT_STORE_SECURE=false
```

### 模型服务

```text
MODEL_API_KEY=
MODEL_OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL_DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com
QWEN_TEXT_MODEL=qwen3.7-plus
QWEN_VL_MODEL=qwen3-vl-plus
```

### 本地 ASR

```text
LOCAL_ASR_API_BASE_URL=http://10.0.0.10:12301/
LOCAL_ASR_LANGUAGE=zh
LOCAL_ASR_MODEL_LABEL=Qwen/Qwen3-ASR-1.7B
```

### 业务帧检测和 mask

```text
VIDEO_GRAPH_INDEX_DEVICE=cpu
VIDEO_GRAPH_INDEX_FINETUNED_YOLO_WORLD_MODEL=/app/runs/yolo_world/robot_arm_parts_v1/weights/best.pt
VIDEO_GRAPH_INDEX_MOBILE_SAM_MODEL=/models/sam2.1_b.pt
VIDEO_GRAPH_INDEX_YOLO_WORLD_CONFIDENCE=0.10
VIDEO_GRAPH_INDEX_YOLO_WORLD_IOU=0.35
VIDEO_GRAPH_INDEX_YOLO_WORLD_MAX_DET=12
```

## 目录结构

```text
app/
  api/
  core/
  db/
  models/
  services/
  video_main.py
  mcp_server.py
static/
tests/
tools/
scripts/
runs/
work/
Dockerfile
docker-compose.yml
docker-compose.external.yml
docker-compose.gpu.yml
requirements.txt
pyproject.toml
```

目录和文件作用：

- `app/api/`：FastAPI HTTP 接口定义。
- `app/api/videos.py`：视频上传、解析、查询、导出接口。
- `app/api/semantic_frames.py`：单图目标检测和 mask 接口。
- `app/api/usage.py`：视频功能用量记录接口。
- `app/core/`：环境变量和配置读取。
- `app/db/`：数据库连接和初始化。
- `app/models/`：SQLAlchemy ORM 表结构。
- `app/services/`：核心业务逻辑，包括存储、视频解析、转写、业务帧筛选、导出、审计记录等。
- `app/video_main.py`：视频 API 服务入口。
- `app/mcp_server.py`：远程 MCP Server 入口。
- `static/`：前端页面、CSS、JavaScript。
- `tests/`：测试代码。
- `tools/`：训练和辅助脚本。
- `scripts/`：本地启动和运维脚本。
- `runs/yolo_world/robot_arm_parts_v1/weights/best.pt`：随仓库提供的微调 YOLO 模型。
- `work/`：运行时临时文件目录，不提交 Git。
- `Dockerfile`：构建 Octopus 应用镜像。
- `docker-compose.yml`：完整一键部署，包含数据库和 MinIO。
- `docker-compose.external.yml`：只部署应用，使用外部数据库和 MinIO。
- `docker-compose.gpu.yml`：GPU 部署覆盖配置。
- `.env.example`：本地运行配置模板。
- `.env.full.example`：完整 Docker 部署配置模板。
- `.env.external.example`：外部数据库和 MinIO 部署配置模板。
- `requirements.txt`：Python 依赖。
- `pyproject.toml`：Python 项目信息和依赖。
- `scripts/run.ps1` / `scripts/run.sh`：本地统一启动视频 API 和 MCP Server。

## 上传 GitHub 注意事项

不要提交：

- `.env`
- 数据集目录 `datasets/`
- 临时工作目录 `work/`
- 训练输出目录中除 `best.pt` 以外的内容
- 大模型文件 `sam2.1_b.pt`
- 其他大文件或密钥

当前仓库只放行：

```text
runs/yolo_world/robot_arm_parts_v1/weights/best.pt
```

## 常见问题

### 浏览器打开 `/mcp` 显示 Not Found 正常吗？

正常。`/mcp` 是 MCP 客户端使用的接口，不是网页。

可以用健康检查确认 MCP 服务是否启动：

```text
http://服务器IP:8100/health
```

### 外部客户端怎么看到 MCP 工具？

需要在 MCP 客户端中配置：

```json
{
  "mcpServers": {
    "octopus-video": {
      "type": "streamable-http",
      "url": "http://服务器IP:8100/mcp",
      "headers": {
        "Authorization": "Bearer your-token"
      }
    }
  }
}
```

如果没有配置 `OCTOPUS_MCP_BEARER_TOKEN`，可以去掉 `headers`。

### 只启动 MCP Server 可以吗？

不可以。MCP Server 只是包装层，真正的业务由 Video API 执行。远程 MCP 调用需要同时运行：

```text
octopus-api
octopus-mcp
```

### 为什么 Docker 还要挂载 `models/sam2.1_b.pt`？

因为 SAM 模型文件较大，不适合直接提交到 GitHub 普通仓库。YOLO 微调模型已随仓库提供，SAM 需要部署时单独放到 `models/` 目录。
