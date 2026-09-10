---
name: web-development
description: PSOP 项目前端开发规范与流程。用于 static/ 下的页面与交互开发、样式调整和前端验收，规定 TailwindCSS v4、Alpine.js、本地 Material Symbols 技术栈、构建与依赖管理、App Shell、动态页面片段及 UI 规范。
---

# PSOP 项目前端开发技能

## 1. 适用范围

本技能规定 PSOP 项目 `static/` 前端的开发规范与流程，覆盖工程组织、页面与交互开发、UI 样式及验收。

- 前端技术栈、构建与依赖管理、页面构建方式及 UI 规范为统一要求，不得擅自替换、弱化或改为可选方案。
- 前端须支持私有化部署，资源本地加载，运行时不依赖 Node；Node 仅用于开发、构建与测试。
- 页面业务行为与 API 对接遵循项目接口契约；本技能不规定后端业务实现。
- 项目路径均相对仓库根目录，参考文档链接相对本文件；目录中的业务页面名称用于说明组织方式。

## 2. 核心技术与工程约束


### 技术栈与应用结构
- UI 技术栈：TailwindCSS v4 + Alpine.js + Material Symbols（都必须从本地静态资源加载，不引用任何外部 CDN）
- 静态服务约定：从 `static/` 目录直接提供静态资源，并支持下列静态映射与 SPA fallback
- 访问路径约定：
  - 入口：`/` -> `static/index.html`
  - 静态资源：`/assets/*`、`/pages/*`、`/node_modules/*` -> `static/` 下同名目录
  - SPA：`/dashboard`、`/tasks`、`/symbols/...` 等路由会 fallback 到 `index.html`
- 交互框架：提供统一的“应用壳（App Shell）”布局，包含顶部状态栏 + 左侧菜单 + 主体内容区
  - 顶部状态栏：固定高度（默认 `h-14`），展示当前页面标题、全局状态（例如：同步状态/连接状态）、右侧操作区（例如：用户/设置入口）
  - 左侧菜单：固定宽度；顶部品牌区高度必须与顶部状态栏一致（默认 `h-14`）；侧边栏自身高度必须为 `100%` / `h-dvh`，不随着右侧内容滚动；菜单列表在侧边栏内部独立滚动；至少包含 3 个一级菜单项；支持高亮当前项
  - 主体内容区：通过左侧菜单切换路由（无需刷新页面），并 `fetch('pages/<route>.html')` 动态加载页面片段；注入 DOM 后调用 `Alpine.initTree(...)` 激活新页面；主体区域必须 `flex-1 min-h-0 overflow-hidden`，页面片段应自适应充满可视区域
- 工具链尽量轻：不使用 Vite/webpack 等打包器；只在 `static/` 下用 Node + PostCSS 编译 Tailwind 输入样式
- 依赖管理：以 `static/package.json`/`static/package-lock.json` 为唯一事实来源；新增依赖必须同时更新 lockfile（用 `npm install`），不要手写改 lockfile

### 依赖版本基线

依赖采用以下版本基线，由 `static/package.json` 与 `static/package-lock.json` 统一记录。未经用户明确要求，不升级或替换依赖。
- Tailwind 构建链（固定版本）
  - tailwindcss：4.1.18
  - @tailwindcss/postcss：4.1.18
  - postcss：8.5.28
  - autoprefixer：10.4.23
- 前端运行时依赖（本地加载）
  - alpinejs：3.15.3
  - codemirror：^6.0.2
  - lightweight-charts：^5.1.0
  - Plotly（官方 Basic 分发包 `plotly.js-basic-dist-min`）：^3.3.1
- 图标与字体（本地资源，不走 NPM）
  - Material Symbols（Outlined/Rounded/Sharp）：`assets/css/material-symbols.css` + `assets/fonts/material-symbols-*.woff2`

### 输出形式
- 只修改/新增必要文件；不要引入新框架或打包器
- 输出时必须标明文件路径；并仅给出你修改/新增文件的完整内容


## 3. 开发流程与必读规范

执行前端开发任务时，以下两份文档均为本技能的组成部分，**不是可选建议**：

1. 阅读 [页面构建与工程实施](references/frontend-development.md)：落实目录、构建脚本、依赖加载、App Shell、路由和页面片段。
2. 阅读 [UI 规范](references/ui-guidelines.md)：落实主题、层级、滚动、响应式、控件、编辑器和可视化规范。

### 实施流程

1. **明确需求**：阅读适用的 `AGENTS.md`、页面需求、API 契约和相关测试，确定页面内容、交互状态及改动范围。
2. **检查工程基础**：核对 npm 清单与锁文件、PostCSS/Tailwind 构建配置、本地资源和静态路径，确保符合下列工程规范。
3. **开发页面与组件**：在 `pages/*.html` 编写页面片段，在页面组件中实现业务逻辑；同步维护 Alpine 注册、菜单与路由映射，按 UI 规范落实布局与交互。
4. **接口联调**：验证数据加载、空态、错误态和操作反馈；涉及异步任务时检查任务切换、轮询清理与过期响应隔离。
5. **构建与验收**：执行 CSS 构建、相关测试和浏览器检查，覆盖路由直达/刷新/前进后退、窄屏以及关键业务流程，并逐项核对验收清单。
6. **交付**：标明文件路径、验证结果和未验证项，不虚报构建或测试通过；未收到明确要求不自行提交或推送 Git。

按具体任务选择相关步骤，所有开发结果均须符合本技能的技术、工程与 UI 规范。

## 4. 验收清单


### 资源与私有化

* 资源引用必须为本地资源（不可 CDN），并与目录结构一致（按访问路径约定，不使用 `/static/...` 前缀）
* Material Symbols 必须自托管（`assets/css/material-symbols.css` + `assets/fonts/*.woff2`）
* 若项目存在 `docs/engineering/ui-theme.md`，验收时配色应以该文档为准；本 skill 中的配色表仅作为默认值

### 路由与 App Shell

* App Shell 必须包含：顶部状态栏 + 左侧菜单 + 主体内容区
* 顶部状态栏高度必须与左侧菜单顶部品牌区高度一致（默认同为 `h-14`）
* 左侧菜单栏必须为 `h-full` / `h-dvh`，不随右侧主内容滚动；菜单列表只在侧栏内部滚动
* 左侧菜单切换路由无需刷新页面
* 主体内容区必须通过 `fetch('pages/<route>.html')` 动态加载页面片段
* 页面片段注入 DOM 后必须调用 `Alpine.initTree(...)` 激活新页面
* `assets/js/components/frame.js` 的路由切换逻辑里：在 `innerHTML` 赋值后应**立即**对注入容器执行 `Alpine.initTree(container)`，确保新 DOM 被 Alpine 识别（不要延后到下一次交互才初始化）。
* 左侧菜单至少包含 3 个一级菜单项，并支持高亮当前项
* App Shell、主内容区、页面根容器与页级面板必须形成完整的 `h-full min-h-0 flex flex-col` 链路，页面内容应自适应充满可视区域

### 页面片段规范

* 页面片段根容器必须优先使用 `h-full min-h-0 flex flex-col`，并让页级面板吃满可视区域
* 页面应使用单层页级面板；内部区块通过细线、分割线、弱背景和紧凑 tile 组织信息，避免默认面板套面板
* 列表页搜索/筛选栏必须单行展示常驻控件，去除“搜索/重置”主按钮，并在控件值变化时即时触发搜索
* 所有支持复制的字段必须使用 readonly 文本框 + 右侧等高复制按钮；不得用普通文本/链接/徽标旁挂复制按钮替代
* `pages/*.html` 必须是片段：不要包含 `<!doctype> / <html> / <head> / <body> / <script>`
* 如页面需要交互：顶层容器使用 `x-data="xxxPage"` 并在 `assets/js/init-alpine.js` 注册对应组件
* 新增/改动路由与菜单：更新 `assets/js/components/frame.js` 的 `menuItems` 与路由映射逻辑
* 资源引用路径必须相对 `static/index.html`：片段里用 `assets/...`、`node_modules/...` 等路径；不要写 `../assets/...`（因为浏览器解析相对路径时基于当前 document，即 `index.html`）。

### Tailwind 构建与 content 扫描

* 使用 `@tailwindcss/postcss` 作为 PostCSS 插件（见 `static/postcss.config.js`）
* Tailwind v4 默认具备自动扫描能力，但本项目以 `static/tailwind.config.js` 的 `content` 为准；若存在动态 class（字符串模板/拼接），必须把对应 JS 文件路径补进 `content`，避免样式缺失。
* `static/tailwind.config.js` 基础 content 扫描范围：

  * `./*.html`（入口）
  * `./pages/**/*.html`（页面片段）
    若在 JS 中拼接 Tailwind class（字符串模板），需要把对应 JS 路径加入 content 扫描，否则样式可能不会被生成。
* `static/assets/css/style.compiled.css` 由 `npm run build:css` 或 `npm run watch:css` 生成/更新，并确保入口页引用正确

### 工具链与依赖管理

* 不使用 Vite/webpack 等打包器；只在 `static/` 下用 Node + PostCSS 编译 Tailwind 输入样式
* 以 `static/package.json`/`static/package-lock.json` 为唯一事实来源；新增依赖必须同时更新 lockfile（用 `npm install`），不要手写改 lockfile

### 输出要求

* 只修改/新增必要文件；不要引入新框架或打包器
* 输出时必须标明文件路径；并仅给出你修改/新增文件的完整内容
