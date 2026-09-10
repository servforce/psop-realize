# 页面构建与工程实施

本文件规定 PSOP 前端的目录组织、构建与依赖加载、页面片段及组件开发方式，是前端开发必须遵循的工程规范。


### 项目前端目录结构与职责
项目根目录下的 `static/` 为前端根目录，目录组织与职责如下：

```text

static/
  package.json（Node 依赖与脚本入口：CSS 构建、开发静态服务器、Jest）
  package-lock.json（锁定依赖解析结果；用于可复现安装）
  tailwind.config.js（Tailwind content 扫描范围与主题扩展）
  postcss.config.js（PostCSS 插件：@tailwindcss/postcss + autoprefixer）
  scripts/
    build-css.cjs（编译 Tailwind：assets/css/style.css -> assets/css/style.compiled.css；支持 --watch）
    dev-server.cjs（本地 SPA 静态服务器：支持 History fallback；用于纯前端预览）
  assets/
    css/
      style.css（Tailwind 输入文件：@import "tailwindcss" + 少量 base 层修正）
      style.compiled.css（构建产物：index.html 默认引用该文件）
      material-symbols.css（Material Symbols 本地字体 @font-face 配置；woff2 位于 assets/fonts/）
    js/
      init-alpine.js（统一注册 Alpine 组件/初始化）
      alpine.min.js（本地 Alpine 单文件副本；如改为从 /assets/js 引入可使用）
      utils/
        kanban.js（通用工具：querystring/路由等）
        __tests__/...（Jest 前端单测）
      components/
        frame.js（App Shell：菜单/路由/动态加载 pages 片段）
        dashboardPage.js / tasksPage.js / ...（各页面的 Alpine 组件逻辑）
      vendor/
        qlibExprEditor.js（CodeMirror 相关：表达式编辑器等）
    fonts/（本地图标字体与字体文件）
    img/（图片资源：logo 等）
    data/（前端演示/本地数据：如 sample-kline.json）
  pages/
    dashboard.html（页面片段：被 frame.js 动态插入主内容区）
    tasks.html / symbols.html / ...（其余页面片段，必须是“片段”而非完整 HTML 文档）
  index.html（入口页：App Shell 布局 + CSS/JS 引用 + importmap）
  README.md（前端使用/开发说明）
  node_modules/（npm install 产物；不会提交 git，但会被后端作为静态文件提供给浏览器）
  .gitignore（忽略 node_modules）
```

### 构建产物
- `static/assets/css/style.compiled.css`：由 `npm run build:css` 或 `npm run watch:css` 生成/更新

### package.json 脚本（以 static/package.json 为准）
- `npm run build:css`：执行 `node scripts/build-css.cjs`（编译一次）
- `npm run watch:css`：执行 `node scripts/build-css.cjs --watch`（监听并增量编译）
- `npm run dev`：执行 `node scripts/dev-server.cjs`（History 模式预览用）
- `npm test`：执行 Jest（用于 `assets/js/**/__tests__`）

### Tailwind v4 注意事项
- 使用 `@tailwindcss/postcss` 作为 PostCSS 插件（见 `static/postcss.config.js`）
- Tailwind v4 有“自动 content 扫描/检测”的能力：当你通过 PostCSS 插件链路构建时，它会以输入 CSS 所在工程为基准做默认扫描；但本项目必须**显式维护** `static/tailwind.config.js` 的 `content`，并确保它覆盖到那些“动态拼接 class”的 JS 文件，避免样式被 tree-shake 掉。
- `static/tailwind.config.js` 基础 content 扫描范围：
  - `./*.html`（入口）
  - `./pages/**/*.html`（页面片段）
  若你在 JS 中拼接 Tailwind class（字符串模板），需要把对应 JS 路径加入 content 扫描，否则样式可能不会被生成。

### 页面开发约定
- `pages/*.html` 必须是片段：不要包含 `<!doctype> / <html> / <head> / <body> / <script>`
- 如页面需要交互：顶层容器使用 `x-data="xxxPage"` 并在 `assets/js/init-alpine.js` 注册对应组件
- 新增/改动路由与菜单：更新 `assets/js/components/frame.js` 的 `menuItems` 与路由映射逻辑
- 资源路径：`pages/*.html` 是被 `static/index.html` 动态加载并注入的片段，因此片段内的资源引用（图片/链接等）必须**以 `index.html` 的位置为基准**写相对路径（例如 `assets/img/...`、`node_modules/...`），不要写成相对于 `pages/` 目录的 `../assets/...`。

### 页面构建指引
- 用途：将 `static/index.html` 作为“Layout Cookbook/风格对齐页”，用于快速复制页面骨架与响应式写法；不承担复杂业务逻辑
- 总体原则：
  - 无外边距：页面主体默认贴边（主内容区不要默认 `p-*` / `mx-auto` / `max-w-*`），用“全高面板”贴边承载
  - 单层页级面板：页面只允许一个主要的页级全高面板；禁止出现“面板套面板 / 卡片再套卡片”的默认结构。面板内部的分区、摘要、筛选、列表、详情等内容，优先使用 `border-b`、`border-r`、`divide-y`、`divide-x` 等细线分隔，而不是继续包一层带边框/圆角/阴影的面板
  - 充满可视区域：App Shell、主内容区、页面根容器、页级面板都应形成 `h-full min-h-0 flex flex-col` 链路；内容区使用 `flex-1 min-h-0 overflow-auto`，确保页面片段自适应吃满剩余可视高度与宽度
  - 固定头 + 滚动体：工具条/筛选区 `shrink-0`，内容区 `flex-1 min-h-0 overflow-auto`；避免让 `body` 或整个右侧区域因局部列表变长而滚动
  - 用边框/分割线组织层级：页级面板用 `border` + `divide-y`；内部内容用细线分隔；列表用 `divide-y`；尽量少用大阴影/大圆角做分隔
  - 响应式优先：普通工具条可 `flex-wrap`；列表页搜索栏必须优先保持单行；按钮“图标常显、文案按断点隐藏（`hidden sm:inline`）”；输入控件 `max-w-full`
- 常用页面布局建议：
  1) 工具条 + 可滚动列表（如 `tasks` / `symbols` / `signals` / `alerts` 等列表页）
     - 页级面板：`border border-slate-800 bg-slate-900/40 overflow-hidden h-full flex flex-col min-h-0 divide-y divide-slate-800`；这是该页面的唯一主要面板，内部不要再套同级别 `border + rounded + shadow` 的面板
     - 搜索/筛选栏：`shrink-0 px-4 py-3 flex items-center gap-2 overflow-x-auto whitespace-nowrap`；搜索输入可 `flex-1 min-w-48`，筛选控件 `shrink-0`，尽量将所有搜索条件放在同一行
     - 搜索控件数量：列表页只保留高价值筛选项，默认不超过 3–4 个；避免把大量搜索控件铺满工具条。复杂筛选应放入“更多筛选”弹层/抽屉，而不是常驻主工具条
     - 即时搜索：去除“搜索”和“重置”按钮；控件值变化即触发查询（输入框用 `@input.debounce.300ms`，select/date/tag 用 `@change` 或对应组件的变更回调）。清空某个控件即视为重置该条件
     - 列表：`flex-1 min-h-0 overflow-auto divide-y divide-slate-800`；行：`p-4 hover:bg-slate-900/50`；行内：`flex-col sm:flex-row` 自适应
  2) 筛选条 + 自适应网格（网格页通用骨架）
     - 工具条放筛选控件（tag select / select / datetime / query），用 `flex flex-wrap gap-2`
     - 内容区：`p-4 grid grid-cols-1 min-[640px]:grid-cols-2 min-[1024px]:grid-cols-3 min-[1536px]:grid-cols-4 gap-4`
  3) 仪表盘统计块（如 `dashboard`）
     - 内容区：`p-4 space-y-4`；统计 tile：`rounded-md border border-slate-800 bg-slate-900/40 p-4`
     - 状态/服务：用 pill（`rounded-full border px-2 py-0.5 text-[11px]`）+ `text-xs text-slate-400 truncate` 错误提示
  4) 详情页（如 `symbol-detail` / `signal-detail`）
     - 顶部信息条固定高度（如 `h-20`），其余区域 `flex-1 min-h-0` 并独立滚动（避免 body 整页滚动）
     - 需要覆盖层（下拉/编辑/选择器）时：`absolute inset-0` + 半透明遮罩；覆盖层内容可用 `max-w-*` 居中并加内边距（覆盖层允许有边距）
  5) 小图表/趋势条（任务/概览类常见）
     - 图表容器固定高度（如 `h-32`），上方预留图例/筛选（避免图表挤占主列表空间）
     - chart 必须随容器 resize，并保持深色配色（背景/网格/文字弱化）
- Vendor 组件接入建议（必须本地加载，不可 CDN；统一在 `static/index.html` 管理引用）：
  - CodeMirror 6：使用 `static/index.html` 的 `importmap`（将模块映射到 `/node_modules/...`）+ `<script type="module" src="assets/js/vendor/qlibExprEditor.js"></script>`；布局上让编辑器容器 `flex-1 min-h-0`，并确保 `.cm-editor` 可“吃满剩余高度”（优先配合 `h-full`/`min-h-0`）
  - lightweight-charts：从 `node_modules/lightweight-charts/dist/lightweight-charts.standalone.production.js` 加载（须在 `static/index.html` 引用）
  - Plotly：从 `node_modules/plotly.js/dist/plotly-basic.min.js` 加载（须在 `static/index.html` 引用）
- 页面资源引用必须为本地资源（不可 CDN），并与目录结构一致（按访问路径约定，不使用 `/static/...` 前缀）：
  ```html
  <!-- 样式（由 npm 构建产物 + 本地字体） -->
  <link href="assets/css/material-symbols.css" rel="stylesheet" />
  <link href="assets/css/style.compiled.css" rel="stylesheet" />

  <!-- 运行时依赖（全部本地加载，不走外部 CDN） -->
  <script src="node_modules/plotly.js/dist/plotly-basic.min.js"></script>
  <script src="node_modules/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
  <script defer src="node_modules/alpinejs/dist/cdn.min.js"></script>
  ```


### 构建与路径验证补充

- 显式维护的 Tailwind 配置必须实际接入构建；通过生成的 CSS 验证扫描范围与主题确已生效，不能仅创建配置文件。
- 保留动态样式能力时优先使用完整类名映射；扩大扫描路径不能替代对拼接类名的产物验证。
- 片段中的相对 URL 由文档 URL 或 `<base>` 解析。使用 `assets/...`、`pages/...` 与 `node_modules/...` 路径时，必须确保嵌套路由下的解析基准正确，并测试直达与刷新。
- 路由快速切换时防止旧响应覆盖新页面；协调 DOM 注入与 Alpine 初始化，避免重复注册，并清理离开页面后的轮询、监听器和图表实例。
