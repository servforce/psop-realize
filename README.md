# Servforce Material Workbench

`servforce-material-workbench` 是第一版网页验证工作台，用于串联：

- 视频上传、关键帧提取、本地 ASR 转正文、视频 Markdown 生成；
- 使用项目内置 `tools/qwen-image-wireframe` 工具调用 Qwen 生成线框图；
- 刷新 `E:\Servforce\standard` 中已有国家标准 PDF；
- 将标准 PDF 物化为 4 个 Markdown；
- 通过 4 个 MCP 工具分别读取这 4 个 Markdown；
- 在网页端查看 REST/MCP/模型调用记录和测试记录。

## 已确认范围

- 四个 Markdown 模板先由本项目生成，无需领导/客户先提供模板。
- 视频分析第一版使用本地 ASR + 关键帧，不加入视觉模型。
- 标准库第一版使用 `E:\Servforce\standard` 中已有 PDF，网页提供刷新 PDF 功能。
- 数据库建议使用 PostgreSQL，便于多人本地 MCP 写入统一日志。
- MCP 调用方身份支持 `client_id`、`user`、`api_key`，日志统一记录为 `caller`。
- PDF、视频、关键帧、线框图、Markdown 文件本体存 MinIO 或本地对象存储；数据库保存元数据、object_key、状态、版本、索引和日志。
- 标准检索是“标准级检索”：根据视频分析结果返回一个或多个完整标准 PDF，不返回 PDF 内某几条条款。因此当前版本没有 `standard_chunks` 表。
- 本期不做 Prompt Pack 页面，不做 PSkill 生成。

## 四个 MCP 工具

领导说的“四个 MCP”在当前项目中明确对应：

```text
get_standard_overview_md(standard_id)
get_standard_structure_md(standard_id)
get_standard_logic_md(standard_id)
get_standard_body_md(standard_id)
```

物化任务：

```text
materialize_standard_pdf(standard_id)
```

负责先生成 4 个 Markdown，但不计入这四个 MCP。

## 代码结构

```text
app/
  api/                  FastAPI REST API
  core/                 配置读取
  db/                   SQLAlchemy 数据库连接
  models/               ORM 表结构
  services/             存储、日志、视频、标准、线框图、ASR 服务
  templates/            四个标准 Markdown 模板
  mcp_server/           MCP stdio server
static/                 HTML/CSS/JS 网页
tests/                  基础测试
data/                   SQLite 本地测试数据库目录
storage/                STORAGE_BACKEND=local 时的本地对象存储目录
```

## 配置文件

复制配置模板：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
copy .env.example .env
```

后续只改 `.env`，不要直接改 `.env.example`。

## 数据库配置

共享第一版建议 PostgreSQL：

```text
DATABASE_URL=postgresql+psycopg://mcp_writer:your-password@10.0.0.20:5432/servforce_mcp
```

本机单人冒烟测试可临时用 SQLite：

```text
DATABASE_URL=sqlite:///./data/workbench.db
```

## 文件存储配置

`STORAGE_BACKEND` 控制文件本体存哪里。这里的文件包括：

- 上传的视频；
- 提取的关键帧；
- Qwen 生成的线框图 PNG；
- 标准 PDF 原件；
- 生成的 `overview.md`、`structure.md`、`logic.md`、`body.md`。

本地存储：

```text
STORAGE_BACKEND=local
LOCAL_STORAGE_ROOT=./storage
```

MinIO 存储：

```text
STORAGE_BACKEND=minio
OBJECT_STORE_ENDPOINT=http://10.0.0.20:9000
OBJECT_STORE_ACCESS_KEY=minioadmin
OBJECT_STORE_SECRET_KEY=minioadmin
OBJECT_STORE_BUCKET=servforce-materials
OBJECT_STORE_REGION=us-east-1
OBJECT_STORE_SECURE=false
```

注意：程序要填 MinIO S3 API 端口，通常是 `9000`，不是控制台端口 `9001`。

## 标准 PDF 目录

Windows 运行：

```text
STANDARD_PDF_DIR=E:\Servforce\standard
```

WSL 运行：

```text
STANDARD_PDF_DIR=/mnt/e/Servforce/standard
```

网页点击“刷新 PDF”时，会扫描这个目录。

## 本地 ASR 配置

当前项目已按 `vidnote` 的方式调用本地 ASR：上传整个视频到：

```text
{LOCAL_ASR_API_BASE_URL}/v1/audio/transcriptions
```

配置：

```text
LOCAL_ASR_API_BASE_URL=http://10.0.0.20:12302
LOCAL_ASR_LANGUAGE=zh
LOCAL_ASR_TIMEOUT_SECONDS=3600
LOCAL_ASR_MAX_RETRIES=1
```

如果 `LOCAL_ASR_API_BASE_URL` 为空，系统会 fallback 到占位转写，方便先跑通页面。

## Qwen 线框图配置

当前项目已内置 Qwen 线框图工具，不依赖本机其他项目目录。工具位置：

```text
tools/qwen-image-wireframe/scripts/generate_wireframe.py
```

默认配置：

```text
WIREFRAME_TOOL_DIR=./tools/qwen-image-wireframe
```

配置图像模型 API key：

```text
DASHSCOPE_API_KEY=your-key
```

可选模型配置：

```text
WIREFRAME_IMAGE_MODEL=qwen-image-2.0-pro
WIREFRAME_IMAGE_SIZE=auto
WIREFRAME_TIMEOUT_SECONDS=900
VIDEO_FRAME_SELECTION_ENABLED=true
VIDEO_FRAME_SELECTION_GROUP_SIZE=8
VIDEO_FRAME_SELECTION_MAX_SELECTED_FRAMES=24
```

视频一键解析顺序为：先抽取候选关键帧，再生成 ASR/结构化转写，然后基于结构化转写筛选关键帧并生成线框图，最后生成 Markdown。`VIDEO_FRAME_SELECTION_GROUP_SIZE` 只控制每次送入视觉模型的候选帧批量；`VIDEO_FRAME_SELECTION_MAX_SELECTED_FRAMES` 控制所有批次汇总后最多保留多少张入选帧。

prompt 也已经随项目内置：

```text
tools/qwen-image-wireframe/prompts/wireframe-prompt.md
```

## 四个 Markdown 模板在哪里

模板文件已经拆出来，位置是：

```text
app/templates/standard_overview.md
app/templates/standard_structure.md
app/templates/standard_logic.md
app/templates/standard_body.md
```

后续如果领导/客户给固定格式，改这四个模板即可。

## WSL 启动

```bash
cd /mnt/e/Servforce/servforce-material-workbench
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
./run-dev.sh
```

打开：

```text
http://127.0.0.1:8090/
```

## Windows 启动

需要 Python 3.11+。

```powershell
cd E:\Servforce\servforce-material-workbench
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
copy .env.example .env
.\run-dev.ps1
```

## MCP Server 启动

```bash
export PYTHONPATH=$PWD
python -m app.mcp_server.server
```

Windows PowerShell：

```powershell
$env:PYTHONPATH = "$PWD"
python -m app.mcp_server.server
```

## 演示流程

1. 打开网页。
2. 进入“标准库”页。
3. 点击“刷新 E:\Servforce\standard PDF”。
4. 选择一个标准，点击“物化生成 4 个 Markdown”。
5. 查看 `overview.md`、`structure.md`、`logic.md`、`body.md`。
6. 上传视频。
7. 查看关键帧、转写文本和视频分析 Markdown。
8. 点击筛选关键帧/生成线框图，系统会先筛选入选帧，再调用项目内置 Qwen 线框图工具。
9. 使用视频分析文本检索相关标准。
10. 查看调用记录和测试记录。
