# MinIO + 视频分析 + 标准库 MCP 项目需求说明

## 1. 项目定位

本项目不是单纯的网站，而是一个带网页验证台的微服务系统。网页用于客户和领导验收、调试和查看结果；后端能力用于被网页、外部程序和 MCP Client 调用。

第一版目标是把以下能力串成可演示、可追踪、可调用的闭环：

```text
视频上传
-> 视频分析 Markdown
-> 关键帧线框图
-> 标准 PDF 入库和物化
-> 根据视频分析结果检索相关国家标准
-> 通过 4 个 MCP 工具读取标准 Markdown
-> 网页查看任务、产物、调用记录和测试记录
```

领导提到的四个关键点对应为：

- 页面：上传、预览、检索、查看产物、查看调用记录和测试记录。
- 逻辑：视频处理、图片线框图、标准检索、PDF 转 Markdown、日志记录。
- MCP：把标准 Markdown 读取能力封装成外部 Agent/程序可调用的工具。
- 大模型调用：ASR、图片转线框图、视频/文本分析、标准摘要和结构化生成。

## 2. 已确认需求口径

### 2.1 标准 PDF 和 4 个 MCP

第一版采用“先物化，后读取”的方案。

```text
标准 PDF 入库
-> 执行物化任务 materialize_standard_pdf(standard_id)
-> 一次性生成 4 个 Markdown
   - overview.md
   - structure.md
   - logic.md
   - body.md
-> 4 个 MCP 工具分别读取这 4 个 Markdown
```

领导说的“四个 MCP”明确对应下面四个读取工具：

```text
get_standard_overview_md(standard_id)
get_standard_structure_md(standard_id)
get_standard_logic_md(standard_id)
get_standard_body_md(standard_id)
```

`materialize_standard_pdf(standard_id)` 是物化/生成任务，不计入这四个 MCP。它可以做成 REST API、后台任务，也可以额外暴露成 MCP，但第一版核心口径是：

> 先把 PDF 物化成 4 个 Markdown，再由 4 个 MCP 工具分别读取 4 个 Markdown。

这样做的原因是 PDF 解析和大模型生成比较慢、可能失败、也可能产生调用成本，不适合每次外部调用 MCP 时都重新解析。

### 2.2 MCP 调用记录

第一版采用：

```text
本地 MCP Server
+ 统一中心数据库
+ 统一日志表
+ 每个调用方带自己的 client_id / user / api_key
```

也就是每个人可以在本地运行 MCP Server，但每个 MCP Server 都要把调用日志写入同一个中心数据库、同一套日志表，并且每次调用都记录调用方身份。网页端从中心数据库读取日志，所以可以看到不同调用方的 MCP 调用记录。

“统一中心数据库”不是只使用同一个用户名和密码，而是连接到同一个数据库实例、同一个库、同一套日志表。数据库用户名和密码只是连接凭据；真正用于区分“是谁调用”的，应是每次调用携带的 `client_id`、`user` 或 `api_key`。

示例：

```text
DATABASE_URL=postgresql://mcp_writer:******@10.0.0.20:5432/servforce_mcp
MCP_CLIENT_ID=alice-dev
MCP_API_KEY=******
```

调用记录示例：

```text
A 本地 MCP 调用 -> 写 servforce_mcp.mcp_call_logs，caller=alice-dev
B 本地 MCP 调用 -> 写 servforce_mcp.mcp_call_logs，caller=bob-dev
网页端 -> 读 servforce_mcp.mcp_call_logs
```

后续正式给客户或多人稳定使用时，再升级为统一 MCP Gateway / mcp-service。

### 2.3 PDF 和 Markdown 存储

确认不直接把 PDF 原件塞进数据库字段。

采用：

```text
PDF 文件本体 -> MinIO
4 个 Markdown 文件本体 -> MinIO
数据库 -> 保存元数据、object_key、版本、状态、索引、调用记录
```

这个方案业务上仍然叫“入库”或“存数据库管理”，因为数据库负责管理这些文件的身份、路径、状态、版本和检索索引；文件本体交给 MinIO 存储。

## 3. 项目范围

### 3.1 本期要做

- 视频上传到 MinIO。
- 视频提取关键帧。
- 视频调用 ASR 转写为正文。
- 生成视频分析 Markdown。
- 关键帧转线框图。
- 标准 PDF 入库。
- 标准 PDF 物化为 4 个 Markdown。
- 根据视频分析 Markdown 检索 1 个或多个相关国家标准。
- 提供 4 个 MCP 工具读取标准 Markdown。
- 记录 REST/MCP/模型调用日志。
- 记录接口测试和功能测试结果。
- 网页端展示任务、产物、标准、调用记录和测试记录。

### 3.2 本期不做

- 不做 Prompt Pack 页面。
- 不做 prompt pack 组装功能。
- 不做 PSkill 生成。
- 不做完整权限/租户体系。
- 不要求一开始就做统一 MCP Gateway。
- 不要求 PDF 原件直接存入数据库字段。

后续团队可以拿视频分析 Markdown、标准检索结果和标准 4 个 Markdown 去构造 PSkill/prompt，但本期只负责把这些材料准备好并通过页面和接口提供出去。

## 4. 可复用现有项目

### 4.1 视频到 Markdown

已有项目：`E:\Servforce\vidnote`

当前已具备：

- FastAPI 后端。
- 静态网页工作台。
- 视频上传。
- 原视频上传 MinIO。
- 后台任务处理。
- ffmpeg 提取关键帧。
- 调用本地 ASR 服务转写。
- 生成视频 Markdown。
- SSE 实时进度。
- SQLite 记录任务、关键帧和产物。

已有 MinIO 路径：

```text
videos/{video_id}/source/{original_filename}
videos/{video_id}/frames/000000000.jpg
videos/{video_id}/transcript/transcript.txt
videos/{video_id}/transcript/transcript.json
videos/{video_id}/markdown/result.md
videos/{video_id}/metadata/video.json
videos/{video_id}/metadata/frames.json
videos/{video_id}/metadata/processing-log.json
```

建议以 `vidnote` 作为第一版主项目继续扩展。

### 4.2 图片转线框图

已有工具：`E:\Servforce\psop\tools\qwen-image-wireframe`

当前是 CLI 工具，能力是：

- 输入图片。
- 调用阿里 DashScope / 百炼 Qwen image edit 模型。
- 输出黑白线框图 PNG。
- 输出 JSON，方便程序解析。

需要改造为后端任务能力：

- 对每张关键帧增加“生成线框图”任务。
- 线框图结果上传 MinIO。
- 数据库记录 keyframe_id 和 wireframe artifact。
- 网页展示原图/线框图对比。

建议 MinIO 路径：

```text
videos/{video_id}/wireframes/{frame_filename}.png
videos/{video_id}/wireframes/manifest.json
```

### 4.3 标准库和 MCP

已有项目：`E:\Servforce\standard-mcp`

当前已具备：

- 将 `E:\Servforce\standard` 中的 PDF 物化成本地知识库。
- 生成 `catalog.json`、`catalog.md`。
- 每个标准已有 `ref.md`、`content.md`、`clauses.json`、`meta.json`。
- 已有 MCP Server。
- 已有标准检索和条款匹配工具。
- 已有测试。

当前差异：现有物化结果不是会议要求的 4 个 Markdown。后续需要补齐：

```text
overview.md
structure.md
logic.md
body.md
```

可以从现有文件迁移：

- `ref.md` 可作为 `overview.md` 的原型。
- `content.md` 可作为 `body.md` 的原型。
- `clauses.json` 可辅助生成 `structure.md` 和 `logic.md`。
- `meta.json` 继续作为机器元数据，不替代 Markdown。

## 5. 系统页面

第一版建议页面如下：

1. 视频工作台

- 上传视频。
- 查看处理进度。
- 查看 MinIO object key。
- 预览关键帧、转写文本、视频分析 Markdown。

2. 关键帧线框图页面

- 查看关键帧原图。
- 生成并查看线框图。
- 展示模型、耗时、状态、错误、输出路径。

3. 标准库页面

- 查看标准列表。
- 查看标准 PDF 入库状态。
- 查看每个标准的 4 个 Markdown。
- 支持触发或重新触发物化任务。

4. 视频匹配标准页面

- 选择一个视频分析结果。
- 根据视频分析 Markdown 检索相关标准。
- 展示命中的标准、匹配分数、命中关键词、证据条款。
- 可打开标准的 4 个 Markdown。

5. MCP/API 调用记录页面

- 展示调用方、工具名/接口名、时间、耗时、状态、错误、输入摘要、输出摘要。
- 支持按 caller、video_id、standard_id、tool_name、status 筛选。

6. 测试记录页面

- 展示接口测试、物化测试、检索测试、模型调用测试。
- 保留测试输入、预期输出、实际输出、是否通过。

## 6. 后端模块

第一版可以做成“模块化单体 + MCP Server”，后续再拆微服务。

```text
frontend
  static web pages

backend FastAPI
  videos
  frames
  wireframes
  standards
  standard_materialization
  retrieval
  call_logs
  test_runs

MCP Server
  standard markdown tools
  search tools
  logging wrapper

storage
  MinIO
  PostgreSQL 或 SQLite
  search index
```

后续可拆为：

- video-service
- image-wireframe-service
- standard-service
- mcp-service / mcp-gateway
- audit-service

## 7. 主流程

### 7.1 标准库准备

```text
标准 PDF 放入标准库
-> PDF 原件上传 MinIO
-> 数据库创建 standard 记录
-> 执行 materialize_standard_pdf
-> 生成 overview.md / structure.md / logic.md / body.md
-> Markdown 上传 MinIO
-> 数据库记录 object_key、状态、版本、关键词、章节索引
-> 建检索索引
```

### 7.2 视频处理

```text
用户上传视频
-> 原视频存 MinIO
-> 创建 video_job
-> 抽关键帧
-> ASR 转写
-> 生成视频分析 Markdown
-> 产物存 MinIO
-> 数据库记录任务状态和产物路径
```

### 7.3 关键帧线框图

```text
选择关键帧
-> 调用 qwen-image-wireframe
-> 生成线框图 PNG
-> 上传 MinIO
-> 数据库记录 wireframe artifact
-> 网页展示原图/线框图对比
```

### 7.4 标准匹配

```text
输入视频分析 Markdown
-> 检索标准库
-> 返回相关标准和条款
-> 记录匹配结果
-> 网页展示命中理由和证据来源
```

### 7.5 MCP 调用

```text
MCP Client 调用四个标准 Markdown 工具之一
-> 本地 MCP Server 接收调用
-> 记录 caller、tool_name、request_summary
-> 读取数据库和 MinIO
-> 返回 Markdown 或 artifact 信息
-> 写入调用结果和耗时
-> 网页从中心数据库展示调用记录
```

## 8. 数据库设计

第一版建议使用 PostgreSQL。如果为了快速验证，也可以先 SQLite，但 MCP 调用记录要想多人统一查看，建议尽早使用中心 PostgreSQL。

### 8.1 视频相关

`video_jobs`

- video_id
- title
- filename
- source_bucket
- source_object_key
- status
- progress_percent
- current_stage
- transcript_object_key
- markdown_object_key
- created_at
- completed_at

`video_frames`

- frame_id
- video_id
- timestamp_ms
- bucket
- object_key
- caption

`generated_artifacts`

- artifact_id
- owner_type：video/frame/standard
- owner_id
- kind：source_video/keyframe/transcript/markdown/wireframe/standard_overview 等
- bucket
- object_key
- media_type
- checksum
- created_at

### 8.2 标准相关

`standards`

- standard_id
- standard_name
- standard_code
- source_pdf_bucket
- source_pdf_object_key
- status
- version
- domain_tags
- keywords
- created_at
- updated_at

`standard_artifacts`

- artifact_id
- standard_id
- kind：overview/structure/logic/body/source_pdf/meta
- bucket
- object_key
- version
- generated_by
- generated_at

`standard_chunks`

- chunk_id
- standard_id
- section
- title
- text
- page_start
- page_end
- keywords
- content_anchor

### 8.3 检索相关

`standard_matches`

- match_id
- video_id
- standard_id
- score
- matched_terms
- reason
- evidence_json
- created_at

### 8.4 调用和测试记录

`mcp_call_logs` 或 `api_call_logs`

- call_id
- interface_type：rest/mcp/internal
- caller：client_id/user/api_key 对应的调用方
- tool_or_endpoint
- request_summary
- response_summary
- status
- error_message
- duration_ms
- video_id
- standard_id
- created_at

`model_call_logs`

- model_call_id
- provider
- model
- purpose：asr/wireframe/summary/logic_extract
- input_artifact_id
- output_artifact_id
- status
- error_message
- duration_ms
- created_at

`test_runs`

- test_run_id
- test_type
- target_interface
- input_json
- expected_json
- actual_json
- passed
- created_at

## 9. REST API 建议

已有视频接口可保留：

```text
POST /api/videos
GET  /api/videos
GET  /api/videos/{video_id}
GET  /api/videos/{video_id}/events
GET  /api/videos/{video_id}/frames
GET  /api/videos/{video_id}/transcript
GET  /api/videos/{video_id}/markdown
```

新增建议：

```text
POST /api/videos/{video_id}/wireframes
POST /api/frames/{frame_id}/wireframe
GET  /api/videos/{video_id}/wireframes

POST /api/standards/upload
GET  /api/standards
GET  /api/standards/{standard_id}
POST /api/standards/{standard_id}/materialize
GET  /api/standards/{standard_id}/markdown/{kind}

POST /api/videos/{video_id}/match-standards
GET  /api/call-logs
GET  /api/test-runs
```

## 10. MCP 工具建议

保留现有 `standard-mcp` 工具：

```text
get_catalog
search_standards
match_standard_clauses
get_clause
get_clauses
```

新增本期核心 4 个 MCP：

```text
get_standard_overview_md(standard_id)
get_standard_structure_md(standard_id)
get_standard_logic_md(standard_id)
get_standard_body_md(standard_id)
```

可选新增：

```text
get_video_analysis_markdown(video_id)
search_standards_by_video(video_id, limit)
generate_frame_wireframe(frame_id)
```

MCP 返回建议使用结构化 JSON，不只返回纯文本：

```json
{
  "standard_id": "...",
  "kind": "overview",
  "markdown": "...",
  "artifact": {
    "bucket": "...",
    "object_key": "..."
  },
  "call_id": "..."
}
```

## 11. 大模型调用点

本期可能涉及的大模型调用：

1. ASR

- 输入：视频。
- 输出：转写文本和 JSON。
- 现有 `vidnote` 已接本地 Qwen/Qwen3-ASR 服务。

2. 关键帧/视频分析

- 第一版可先用 ASR + 关键帧清单。
- 如果要更准确检索标准，建议后续加入视觉模型识别动作、设备、风险点。

3. 图片转线框图

- 使用 `qwen-image-wireframe` 调用 Qwen image edit。

4. 标准 PDF 物化

- `body.md` 以 PDF 文本抽取为主。
- `overview.md`、`structure.md`、`logic.md` 可用规则 + 大模型生成。
- `logic.md` 最可能需要大模型辅助提取条款关系。

## 12. 验收演示路径

建议使用 `铸件 工业计算机射线照相检测.pdf` 和一个相关视频作为样例。

演示步骤：

1. 打开网页。
2. 上传视频。
3. 看到视频进入 MinIO，并显示 object key。
4. 看到关键帧、转写文本、视频分析 Markdown。
5. 点击关键帧生成线框图。
6. 打开标准库，看到国家标准列表。
7. 打开“铸件 工业计算机射线照相检测”，看到 PDF 和 4 个 Markdown。
8. 点击“根据视频分析匹配标准”，看到命中的标准和条款。
9. 打开“调用记录”，看到 REST/MCP/模型调用记录。
10. 打开“测试记录”，看到接口测试和样例测试结果。

## 13. 实施阶段

### 第 1 阶段：整合现有功能

- 以 `E:\Servforce\vidnote` 为主项目。
- 接入 `qwen-image-wireframe`。
- 接入 `standard-mcp` 的检索服务。
- 页面增加标准匹配结果。
- 调用记录先覆盖 REST、MCP 和内部任务。

### 第 2 阶段：标准库正式化

- 设计 `standards`、`standard_artifacts`、`standard_chunks` 表。
- PDF 原件进 MinIO。
- 每个 PDF 生成 4 个 Markdown。
- 建关键词/全文检索索引。

### 第 3 阶段：MCP 接口正式化

- 保留现有 `standard-mcp` 工具。
- 新增 4 个 Markdown 读取工具。
- 新增视频分析和标准匹配工具。
- 所有 MCP 工具统一写调用日志。
- 调用方必须带 `client_id`、`user` 或 `api_key` 至少一个。

### 第 4 阶段：验收页面和测试记录

- 页面展示视频、关键帧、线框图、标准 Markdown、匹配证据。
- 页面展示 REST/MCP/模型调用记录。
- 页面展示测试记录。
- 完善 README：启动方式、MinIO bucket、数据库配置、MCP 工具名、API 文档、样例数据。

## 14. 仍需确认的问题

1. 四个 Markdown 的具体格式模板是否由领导/客户指定？
2. “视频分析结果”第一版是否只用 ASR + 关键帧，还是必须加入视觉模型？
3. 标准库第一版是否只使用 `E:\Servforce\standard` 中已有 PDF，还是要支持网页上传新 PDF？
4. 中心数据库第一版使用 PostgreSQL 还是 SQLite 共享文件？建议 PostgreSQL。
5. MCP 调用方身份使用 `client_id`、`user` 还是 `api_key` 作为主字段？建议三者都支持，日志统一记录为 `caller`。

## 15. 最终交付物

第一版建议交付：

- 一个可运行网页。
- 一个 FastAPI 后端。
- 一个 MCP Server。
- 一个 MinIO bucket 结构。
- 一个中心数据库及日志表。
- 视频上传和 Markdown 生成流程。
- 关键帧线框图生成流程。
- 标准 PDF 到 4 个 Markdown 的物化流程。
- 4 个标准 Markdown 读取 MCP 工具。
- 视频分析到标准匹配流程。
- 接口调用记录页面。
- 测试记录页面。
- README 文档。
