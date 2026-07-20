# 视频

## `POST /api/videos`

上传视频到 MinIO，并创建视频记录。

## `GET /api/videos`

获取视频列表。

## `GET /api/videos/{video_id}`

获取单个视频的详情。

## `POST /api/videos/{video_id}/parse`

解析视频。

`mode=full`：一键解析视频，按关键帧、转写文本、筛选关键帧/生成线框图、Markdown 报告的顺序执行。

`mode=keyframes`：解析关键帧。

`mode=wireframes`：筛选关键帧，并对入选帧生成线框图。

`mode=transcript`：生成转写文本。

`mode=markdown`：生成 Markdown 报告。

## `GET /api/videos/{video_id}/events`

订阅视频解析进度事件。

## `GET /api/videos/{video_id}/frames`

获取视频关键帧列表。

## `GET /api/videos/{video_id}/frames/{filename}`

获取单张关键帧图片。

## `GET /api/videos/{video_id}/wireframes/jobs/latest`

获取视频最近一次线框图生成任务。

## `GET /api/videos/{video_id}/wireframes/jobs/{job_id}`

获取指定线框图生成任务的状态。

## `GET /api/videos/{video_id}/wireframes`

获取视频线框图结果列表。

## `GET /api/videos/{video_id}/transcript`

获取视频转写文本。

## `GET /api/videos/{video_id}/markdown`

获取视频 Markdown 报告内容。

## `POST /api/wireframes/image`

上传单张图片并生成线框图。

# 标准库

## `POST /api/standards/upload`

上传一个或多个标准 PDF 到标准库 MinIO bucket，并创建或更新标准记录。

前端使用位置：标准库左侧“标准目录”的文件夹上传区，支持点击选择 PDF 和拖拽 PDF。

请求方式：`multipart/form-data`

字段：

`files`：PDF 文件列表，支持多个文件。

返回：

`count`：本次成功上传的 PDF 数量。

`standards`：上传后生成或更新的标准记录摘要，包含 `standard_id`、`name`、`status`、`source_pdf_object_key` 等信息。

## `GET /api/standards`

获取标准库列表。

前端使用位置：标准库左侧“标准目录”列表。

当前页面会基于返回数据在前端做关键词过滤和按最新时间排序。

返回列表字段主要包括：

`id`：标准 ID。

`name`：标准名称。

`code`：标准编号，目前可能为空。

`status`：标准状态，例如 `registered`、`processing`、`materialized`、`failed`。

`source_pdf_bucket`：源 PDF 所在 bucket。

`source_pdf_object_key`：源 PDF 在 MinIO 中的对象路径。

`artifacts`：已生成 Markdown 的固定 MinIO 路径映射，例如 `overview`、`structure`、`logic`、`body`。

`created_at` / `updated_at`：创建和更新时间。

## `GET /api/standards/{standard_id}`

获取单个标准详情。

前端使用位置：点击左侧标准目录中的某一条标准后，右侧“标准详情”加载该标准信息。

返回字段和 `GET /api/standards` 的单条记录基本一致，会包含当前标准的 Markdown 产物映射。

## `POST /api/standards/{standard_id}/materialize`

解析单个标准 PDF，后台生成四个 Markdown 文件。

前端使用位置：标准详情右侧按钮 `解析标准 PDF`。

当前按钮名称已经统一为“解析标准 PDF”，前端不再使用 `retry` 或“重试”命名。

生成的四个 Markdown：

`overview`：`standard_overview.md`

`structure`：`standard_structure.md`

`logic`：`standard_logic.md`

`body`：`standard_body.md`

行为说明：

如果该标准已经有完整 Markdown，再次调用仍然走同一个解析接口，会重新生成并覆盖四个 Markdown。

如果同一标准已有运行中的解析任务，接口会返回当前运行中的任务状态，不重复创建新任务。

返回：

`standard_id`：标准 ID。

`job_id`：解析任务 ID。

`status`：任务状态，例如 `running`。

`stage`：当前解析阶段。

`progress_percent`：当前进度百分比。

`message`：进度说明。

`error`：错误信息，失败时返回。

## `GET /api/standards/{standard_id}/materialize-status`

获取单个标准最近一次解析任务状态。

前端使用位置：标准详情右侧解析进度条。调用 `POST /api/standards/{standard_id}/materialize` 后，前端会轮询该接口直到任务完成或失败。

常见阶段：

`starting`：准备解析标准 PDF。

`downloading_pdf`：正在读取源 PDF。

`extracting_pdf_text`：正在本地抽取 PDF 原文。

`preparing_pdf_upload`：准备上传 PDF 给模型。

`uploading_pdf_to_qwen`：正在上传 PDF 给 qwen3.7-plus。

`generating_body`：正在生成 `standard_body.md`。

`generating_structure`：正在生成 `standard_structure.md`。

`generating_logic`：正在生成 `standard_logic.md`。

`generating_overview`：正在生成 `standard_overview.md`。

`validating_markdown`：正在校验 Markdown。

`uploading_artifacts`：正在保存四个 Markdown 文件。

`completed`：四个 Markdown 已生成。

`failed`：生成失败。

返回字段：

`standard_id`、`job_id`、`status`、`stage`、`progress_percent`、`message`、`error`、`created_at`、`updated_at`、`completed_at`。

## `GET /api/standards/{standard_id}/markdown/{kind}`

读取某个标准的单个 Markdown 内容。

前端使用位置：标准详情右侧四个 Markdown tab。

`kind` 可选值：

`overview`：读取 `standard_overview.md`。

`structure`：读取 `standard_structure.md`。

`logic`：读取 `standard_logic.md`。

`body`：读取 `standard_body.md`。

返回类型：`text/markdown; charset=utf-8`

当前标准详情页不再提供下载按钮，而是读取该接口内容后在页面内展示，并提供复制按钮。

## `GET /api/standards/{standard_id}/markdown.zip`

下载某个标准的四个 Markdown 打包 zip。

当前标准详情页已去掉下载按钮，不再使用该接口。

该接口仍保留给其他页面或外部调用使用，例如标准检索结果中的“下载全部”入口。

返回类型：`application/zip`

## `POST /api/standards/refresh-pdfs`

已停用接口。

旧版本用于从本地目录刷新 PDF，现在返回 `410 Gone`。

当前标准库页面应使用 `POST /api/standards/upload` 上传标准 PDF。

# 标准库检索

## `POST /api/standards/index/rebuild`

重建标准库向量检索索引。

前端使用位置：标准库检索页面的 `重建向量索引` 按钮。

功能说明：

从所有已解析标准的 `standard_overview.md` 中提取检索文本，调用 embedding 模型生成向量，并写入 PostgreSQL `standard_search_indexes` 表。

返回：

`indexed_count`：成功索引的标准数量。

`failed_count`：索引失败数量。

`indexed`：成功索引的标准列表。

`failed`：失败的标准和原因。

`embedding_model`：使用的向量化模型。

`embedding_dimensions`：向量维度。

## `POST /api/standards/{standard_id}/index/rebuild`

重建单个标准的向量检索索引。

功能说明：

读取该标准的 `standard_overview.md`，提取检索文本，生成 embedding，并覆盖写入该标准在 `standard_search_indexes` 中的 `overview` 索引记录。

## `POST /api/standards/search`

根据输入文本检索匹配标准。

前端使用位置：标准库检索页面的 `检索标准` 按钮。

查询参数：

`query`：检索文本，通常来自视频分析文本、作业场景、作业对象、工序、风险点等。

`limit`：返回数量上限，前端当前使用 `10`。

功能说明：

将输入文本通过 embedding 模型转成查询向量，再通过 PostgreSQL pgvector 与 `standard_search_indexes.embedding` 做相似度匹配。

返回：

`mode`：检索模式说明。

`embedding_model`：使用的向量化模型。

`embedding_dimensions`：向量维度。

`matches`：最终匹配标准列表。

`excluded`：当前简单版通常为空，保留字段用于接口兼容。

`message`：提示或错误说明。

匹配项通常包含：

`standard_id`、`standard_name`、`decision`、`match_level`、`score`、`reason`、`evidence`。

## `GET /api/standards/search/history`

获取标准检索历史。

前端使用位置：标准库检索页面的检索历史区域，以及 `刷新历史` 按钮。

查询参数：

`limit`：返回数量上限，默认 `20`，最大 `100`。

返回：

检索时间、检索文本、状态、耗时、一级候选数量、匹配数量、排除数量、匹配摘要和错误信息等。

## `POST /api/standards/match-video/{video_id}`

根据视频分析文本匹配标准。

当前标准库主页面不直接使用该接口。

查询参数：

`analysis_text`：视频分析文本。

`limit`：返回数量上限，默认 `5`。

功能说明：

用于把某个视频的分析结果和标准库进行匹配，并记录视频与标准的匹配关系。
