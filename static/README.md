# PSOP 前端

项目技能：`.agents/skills/web-development/SKILL.md`。

## 开发

```bash
cd static
npm ci
npm run build:css
npm run watch:css
```

另一个终端运行 `npm run dev`，打开 `http://127.0.0.1:4173`。仅静态预览时 API 会返回明确的未连接提示。需要联调时显式设置测试 API，例如：

```bash
PSOP_API_ORIGIN=http://127.0.0.1:8090 npm run dev
```

不要把预览代理指向生产服务进行上传/解析测试。开发服务器仅监听 localhost；生产运行不需要 Node。

## 页面结构

- `index.html`：应用壳，本地字体与 Alpine 模块入口；`<base href="/">` 保证深层路由资源路径正确。
- `pages/videos.html` + `components/videosPage.js`：单行文件表格、服务端分页/搜索/状态筛选/时间排序、视频详情、解析与产物、删除确认。窄屏保持字段不换行，在列表内横向滚动。
- `pages/uploads.html` + `components/uploadsPage.js`：上传抽屉内容，提供视频选择、拖拽、标题、上传进度及任务状态。
- `assets/js/components/uploadDrawer.js`：右侧原生 dialog 抽屉，按需加载上传片段，保留表单草稿，支持关闭按钮、遮罩及 Esc 关闭和焦点恢复。
- `pages/usage.html` + `components/usagePage.js`：模型用量查询与筛选。
- `assets/js/components/frame.js`：History 路由、片段加载及 Alpine 生命周期。
- `assets/js/services/upload.js`：跨页面长请求与 2 秒任务轮询；临时 404、延迟错误及旧响应保护。
- `assets/js/utils/markdown.js`：结构化 Markdown 渲染与 URL/HTML 安全处理。
- `assets/css/style.css`：Tailwind 输入、主题与组件；不要手改 `style.compiled.css`。

品牌区展示 PSOP Realize 与本地结构图标；侧栏移除上下方说明文字，折叠按钮位于主内容顶栏。菜单仅保留“文件解析”和“用量统计”。点击文件解析列表中的“上传视频”打开右侧抽屉，不跳转页面；进行中的任务可通过同一按钮查看进度。

文件列表不展示顶部数量统计或常驻刷新按钮，“上传视频”固定在搜索栏右侧；窄屏仅筛选控件内部横向滚动，不挤出上传入口。底部分页支持上一页、下一页和每页 10/20/50/100 条（默认 20 条），不重复展示业务统计和更新时间。

分页请求使用 `GET /api/videos?page=1&page_size=20&query=&status=&sort=desc`，返回 `items`、`total`、`page`、`page_size`、`total_pages`。搜索标题/文件名、状态过滤和时间排序均在数据库分页前执行；相同时间按 ID 稳定排序。页码从 1 开始，无结果时为第 1/1 页，超出末页（如删除末页最后一项）自动回退到有效末页。不传 `page` 的调用仍返回原有最多 30 项的数组，兼容既有接口使用方。

改变搜索、筛选、排序或每页条数会回到第一页；删除后重新查询当前页。在同一浏览器会话中进入详情再返回，保留列表条件与页码。列表每 5 秒静默查询当前页，更新上传和解析状态，不重置滚动位置；连接异常保留已显示内容并提示重试。快速切换条件会取消前次请求并忽略过期响应，离开列表后清理轮询。

路由：`/videos`、`/videos/:id`、`/usage`；旧 `/uploads` 地址会替换为 `/videos` 并打开上传抽屉。FastAPI 通过 `app/frontend.py` 提供 `/assets`、`/pages`、`/node_modules`，仅前端路由 fallback，未知 API 保持 404。

上传自动调用 `/api/videos/upload-and-parse`。关闭抽屉不会中断上传或解析，重新打开保留标题和任务进度。任务可跨页面跟踪；刷新/关闭浏览器会中断当前 HTTP 连接，前端在活跃上传时提示离开风险。后台已创建的任务可在视频详情继续查询。

详情页将“重新解析”“重新转写”“重新抽帧”“重新生成 Markdown”与“导出产物”放在同一操作组，窄屏可在组内横向滚动。“重新解析”依次更新转写、业务帧和 Markdown；单独重新转写或抽帧不会自动更新后续步骤，Markdown 根据现有转写和业务帧重新生成。尚无结果时，这些操作也可用于首次生成。已有任务正在处理时，重新生成及导出按钮均禁用。

Markdown 的“预览”“源码”“复制”组成内容区右上角的悬浮操作组，不占独立工具栏。预览正文和只读源码各自内部滚动，操作组保持固定；复制始终使用完整 Markdown 原文，空内容时禁用。

## 删除文件

- 文件列表“删除”按钮打开确认对话框，明确删除范围；取消不会发送请求。上传、排队或解析中的任务禁止删除。
- `DELETE /api/videos/{id}` 删除任务自身 `videos/{id}/` 命名空间的源视频和解析产物，并清理对应任务及业务帧记录；不清理其他视频，保留历史用量记录。
- 后端 `app/services/video_deletion.py` 与解析入口通过任务行锁协调。对象清理前持久化 `deleting` 状态；清理失败返回错误并保留记录，阻止重新解析，允许重新执行删除。对象分页、批量删除和部分失败检查位于 `app/services/storage.py`。对象存储的历史版本保留遵循桶自身的版本策略。
- 删除成功后立即更新列表及上传抽屉状态。当前会话会忽略迟到的列表/上传响应，防止已删除条目重新出现；页面切换不会撤销已发出的删除请求。
- 删除确认使用原生 dialog，默认聚焦“取消”，支持 Esc、遮罩关闭及键盘焦点循环。删除请求进行中禁止重复确认或取消。

## 测试与部署

```bash
npm test
# 仓库根目录
python -m pytest tests/test_frontend_routes.py tests/test_video_frontend_runtime.py tests/test_video_frontend_upload_polling.py tests/test_video_delete_api.py tests/test_video_upload_and_parse_api.py
```

Docker 使用 Node 构建阶段执行 `npm ci` 和 CSS 编译，并将生产依赖复制到 Python 镜像；最终镜像无需 Node。非 Docker 部署必须先在 `static/` 执行 `npm ci && npm run build:css`，保留运行时 `node_modules`。

升级前先备份数据库并停止旧版本实例。启动时会执行幂等的数据库结构清理，移除已退役的专用任务表及冗余字段，保留视频、业务帧筛选状态和历史用量数据。

字体来自 Google Material Symbols，字体许可见 `assets/fonts/LICENSE.txt`。第三方依赖由 package-lock 锁定，全部本地加载；CodeMirror 与图表依赖保留技能指定的版本范围，未用到的组件不加载到当前页面。

## 依赖安全

- PostCSS 使用 `8.5.28`，修复旧版 CSS 输出和 source map 处理相关告警；Tailwind 和 Autoprefixer 版本不变。
- Plotly 使用同为 `3.3.1` 的官方 `plotly.js-basic-dist-min` 分发包。它提供本项目规范要求的 Basic 图表（scatter、bar、pie），无需安装地图模块 MapLibre。按需本地加载路径为 `node_modules/plotly.js-basic-dist-min/plotly-basic.min.js`。
- 不使用 `npm audit fix --force` 降级 Plotly，也不跨大版本强制覆盖其地图依赖。未来若需要地图或其他非 Basic 图表，应单独评估相应分发包及安全版本。
- 使用 `npm audit`（含开发依赖）和 `npm audit --omit=dev` 检查安全状态；锁文件通过 npm 更新，不手写修改。建议开发环境使用 Node 22，与 Docker 构建阶段一致。

修复依据：[MapLibre 安全公告](https://github.com/advisories/GHSA-jrc7-96c5-q579)、[PostCSS 安全公告](https://github.com/advisories/GHSA-fxqj-rqcc-2cmp)、[Plotly 官方分发包说明](https://github.com/plotly/plotly.js/blob/main/dist/README.md)。
