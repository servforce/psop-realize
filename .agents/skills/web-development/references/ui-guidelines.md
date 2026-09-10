# PSOP UI 规范

本文件规定 PSOP 前端的视觉风格、布局、组件和交互要求，是前端开发必须遵循的 UI 规范。


### UI 风格与规范（必须遵循）

* 定位：深色优先、现代克制、偏“管理后台 / SaaS 控制台”风格；信息密度中等，强调可读性与状态表达
* 配色方案：

  * `docs/engineering/ui-theme.md` 若存在，则它是当前项目的配色重载事实来源；与本节默认值冲突时，以 `docs/engineering/ui-theme.md` 为准
  * 本节为本项目默认配色规范；没有项目主题文档覆盖时必须遵循
  * 后续改色时，项目内优先修改 `docs/engineering/ui-theme.md`；不要把项目主题差异直接堆回本节默认值
  * 其它地方如出现颜色 class，应优先引用“Base / Primary / Danger / Warning / Info / Overlay”这些语义槽位，而不是分散定义

  * Base（中性基底 / Neutral Gray）：

    * 页面背景：bg-slate-950（项目内重映射为 neutral gray，不带青绿色调）
    * 面板背景：bg-slate-900/40（列表/面板主体），bg-slate-950/20（更弱的底；项目内重映射为 neutral gray）
    * 顶栏背景：bg-slate-950/70（可 backdrop-blur；项目内重映射为 neutral gray）
    * 边框/分割线：border-slate-800、divide-slate-800
    * Hover：hover:bg-slate-900/50
    * 文字：主文 text-slate-200，次级 text-slate-300，弱化 text-slate-400 / text-slate-500，占位 placeholder-slate-500
  * Primary（Orange，主交互）：

    * 实心按钮：bg-orange-500 text-slate-950 hover:bg-orange-400
    * 选中/强调（弱底）：bg-orange-500/10 text-orange-200 border-orange-500/30
    * Focus ring：focus:ring-2 focus:ring-orange-500/30 focus:border-orange-500/30
    * 状态点：bg-orange-400
  * Success（Emerald / Green，成功）：

    * 成功/已发布/启用/已接受等代表成功含义的状态，必须使用绿色语义，不得使用 Primary Orange：bg-emerald-500/10 text-emerald-200 border-emerald-500/25
    * 成功提示块：bg-emerald-500/10 text-emerald-100 border-emerald-500/25
    * 状态点：bg-emerald-400
  * Danger（Rose，危险/错误）：

    * 弱底按钮/块：bg-rose-500/20 text-rose-200 hover:bg-rose-500/25
    * 错误文案：text-rose-300
    * 状态点：bg-rose-400
  * Warning（Amber，警告）：

    * 弱底：bg-amber-500/15 text-amber-200 border-amber-500/25
    * 文案：text-amber-300
  * Info（Sky，信息）：

    * 弱底：bg-sky-500/15 text-sky-200 border-sky-500/25
    * 文案：text-sky-300
  * Overlay（遮罩，默认值）：

    * 弹窗遮罩：bg-black/60
* 主题：

  * 默认深色主题，不强制实现 light/dark 切换
  * 如需主题切换：Tailwind darkMode: 'class' + Alpine theme（localStorage 持久化）
* 字体与排版：

  * 正文使用系统字体栈；图标字体例外：Material Symbols（必须自托管）
  * 页面标题/区块标题层级清晰：标题 text-md/lg，正文 text-sm，辅助信息 text-xs/text-[11px]
  * 行高与间距统一：正文 leading-6；常用内边距以 px-4 py-3（工具条）与 p-4（内容区）为主
  * 元信息排版（必须区分样式）：ID/时间/日期/时长/数值等统一使用更“技术化”的排版

    * ID：font-mono text-[11px] text-slate-500（可 select-all/break-all；避免与正文混淆）
    * 时间/日期：font-mono tabular-nums text-[11px] text-slate-400 whitespace-nowrap（保证数字等宽对齐、列表整齐）
    * 数值统计：在容器上加 tabular-nums（如列表列/指标卡），避免数字跳动
* 布局与层级（无边距 / 面板化）：

  * App Shell：外层使用 `h-dvh overflow-hidden flex`；顶部栏固定高度（默认 `h-14`）；左侧品牌区高度必须与顶部栏一致（默认同为 `h-14`）；侧边栏固定宽度（展开 `w-48`，折叠 `w-14`），并使用 `h-full shrink-0 flex flex-col overflow-hidden`，其中品牌区 `shrink-0`、菜单列表 `flex-1 min-h-0 overflow-y-auto`；主内容区使用 `flex-1 min-w-0 min-h-0 overflow-hidden flex flex-col` 并独立滚动
  * 主内容区默认“无外边距/无卡片外框”：不要默认使用外层 rounded-*/shadow/mx-auto/max-w-*；页面主体用一个“全高面板”贴边承载，通过 border/divide-y/半透明背景区分结构
  * 禁止面板套面板：页级全高面板内部默认不再嵌套同视觉重量的面板；需要组织内容时优先使用细线分割（`border-b` / `border-r` / `divide-y` / `divide-x`）、弱背景条或紧凑 tile，避免“卡片海”和重复圆角边框
  * 全高面板推荐结构（需贯穿各视图一致）：

    * 容器：border border-slate-800 bg-slate-900/40 overflow-hidden h-full flex flex-col min-h-0 divide-y divide-slate-800
    * 顶部工具条：shrink-0 px-4 py-3（普通操作区可 `flex flex-wrap`；列表页搜索栏应使用单行 `flex items-center gap-2 overflow-x-auto whitespace-nowrap`）
    * 内容区：flex-1 min-h-0 overflow-auto raelyn-scrollbar（列表用 divide-y；网格用 p-4 + grid；详情/分栏用 border/divide 细线分区）
  * “小卡片/Tile”仅用于面板内部的局部信息块（如统计卡、视频卡、弹窗），圆角更小（优先 rounded-md，必要时 rounded-lg），不作为页面默认外框
* 组件风格（保持一致即可）：

  * 按钮：Primary（bg-orange-500 text-slate-950）、Secondary（border border-slate-700 bg-slate-950/20）、Danger（bg-rose-500/20 text-rose-200）三类；hover/disabled/焦点 ring 一致（focus:ring-orange-500/30）
  * 表单：input/select 默认使用 rounded-md border border-slate-700 bg-slate-950/30；focus 使用 focus:ring-2 focus:ring-orange-500/30 focus:border-orange-500/30；错误态用 rose 文案与边框
  * 列表页搜索栏：所有常驻搜索/筛选控件必须尽量在一行展示；不要堆叠多行筛选表单；不要提供独立“搜索”与“重置”按钮。控件值变化应立即驱动搜索状态并刷新列表，输入类控件使用防抖，筛选类控件直接触发。需要全量重置时可提供一个低优先级的“清空条件”文本动作或让用户逐项清空，但不要把它作为主按钮常驻
  * 多行文本输入：textarea、提示词编辑区、cookies 编辑区这类“大段文本编辑面板”不要直接沿用最深底色；优先使用更浅一层的 slate 背景，并把正文降到更柔和的 slate-300 左右，而不是高对比纯白，placeholder 保持 slate-400/500，caret 与 focus 反馈可以继续更亮，以减少长时间阅读/编辑的视觉疲劳
  * 贴边编辑器（Edge-to-edge editor）：适用于提示词、Markdown、JSON、cookies、长文本说明等“大段文本编辑”区域；外层面板负责边框、圆角、分割线，编辑器内容区默认与面板贴边，不再额外包一层 `p-*`
  * 贴边编辑器结构：头部/说明/错误提示作为独立分区放在编辑器上方，使用 `border-b` 分隔；编辑器本体优先 `block w-full rounded-none border-0`，仅保留文本阅读所需的 `px-4 py-3`
  * 贴边编辑器禁忌：不要给编辑器外再套一层 `p-4`；不要同时保留“外层面板边框 + textarea 自身边框 + rounded-lg”；视觉目标是“面板即编辑器表面”，不是“面板里再嵌一张卡片”
  * 贴边编辑器优先级：当“无边距面板化”与“通用表单控件样式”冲突时，普通 input/select 保持 `rounded-md border`，大段文本编辑器优先使用贴边编辑器模式，不套用通用 textarea 卡片样式
  * 贴边编辑器推荐配方：外层 `rounded-lg border border-slate-800 bg-slate-950/20 overflow-hidden`；头部 `px-4 py-3 border-b border-slate-800`；错误行 `px-4 py-3 text-xs text-rose-200 border-b border-rose-900/60`；textarea `block w-full h-64 rounded-none border-0 px-4 py-3 text-xs font-mono leading-5 resize-none focus:outline-none`
  * 可复制字段：所有支持复制的短文本/ID/URL/路径等字段，必须统一使用“不可编辑文本框 + 右侧复制按钮”的组合展示，不使用普通文本、pill、info-chip 或裸链接旁挂复制按钮。文本框使用 `readonly`，不可用 `disabled`（保留聚焦、选择与复制能力）；按钮紧贴文本框右侧，按钮高度必须与文本框一致，图标使用 Material Symbols 的 `content_copy` / `check`
  * 可复制字段推荐结构：外层 `flex items-stretch min-w-0`；文本框 `h-11 flex-1 min-w-0 rounded-l-md rounded-r-none border border-r-0 ... font-mono select-all`；按钮 `h-11 w-11 shrink-0 rounded-l-none rounded-r-md ...`。长 URL/路径仍放入 readonly input 中，通过横向光标/全选处理，不改成多行正文
  * 多选：优先使用 tag select 控件（“已选标签 + 搜索输入 + 下拉选项”），而不是原生 multi-select；需支持 Enter 添加第一个匹配项、Backspace 删除最后一个 tag、Esc 关闭下拉；列表页搜索栏中的 tag select 默认保持单行紧凑展示，必要时在控件内部横向滚动或收纳，而不是撑高整条搜索栏
  * 列表/表格：优先用 divide-y divide-slate-800；行 p-4 hover:bg-slate-900/50；空状态/加载态需要占位（如 暂无数据、加载中…）
  * 徽标/状态：pill badge 统一 `rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em]`；状态标签必须按状态语义区分 tone，不使用单一主色覆盖所有状态。成功/已发布/启用/已接受等代表成功含义的状态必须用 Emerald / Green，不得用 Primary Orange；运行中/编译中用 Sky，待处理/排队/草稿用 Amber，失败/拒绝/取消/超时用 Rose，归档/跳过/未知用 Slate。状态点同样按语义用对应色
  * 弹窗：遮罩 bg-black/60；内容容器 rounded-lg border border-slate-800 bg-slate-900 shadow-lg p-4
* 交互细节：

  * 过渡动画克制：只在菜单切换/弹层/提示条使用 transition/x-transition，时长 150–250ms
  * 响应式：小屏侧边栏可隐藏/展开（顶部栏 hamburger，md:hidden）；工具条/列表行用 flex-wrap 适配窄屏；按钮文案用 hidden sm:inline
* 可视化区域规范（既定组件规范）：

  * CodeMirror：放入面板内容区；使用等宽字体；优先自适应高度并“吃满剩余可视区域”（与全高面板的滚动策略一致），而不是固定高度；提供“复制/保存/格式化”示例操作区；深色主题
  * lightweight-charts：放入面板工具条下方或内容区顶部；容器固定高度（如 h-32/h-48）；随容器 resize；深色配色与网格弱化
  * Plotly：放入面板内容区；容器固定高度（如 320px）；精简/关闭 modebar（保持界面干净）
* 图标策略：

  * 统一使用 Material Symbols（同一套风格/笔画/尺寸），禁止运行时从 CDN 加载
  * 推荐用自托管字体 + ligature 方式（如 <span class="material-symbols-rounded">settings</span>），并用 Tailwind 控制大小与对齐（如 text-[20px] leading-none）

### Material Symbols 使用约定

* 样式引入（入口页 `static/index.html`）：

  ```html
  <!-- Material Symbols：本地字体图标（禁止外部 CDN） -->
  <link href="assets/css/material-symbols.css" rel="stylesheet" />
  ```
* 图标使用方式：

  ```html
  <!-- 空心 Outlined 风格 -->
  <span class="material-symbols-outlined">menu</span>
  <!-- 实心 Rounded 风格 -->
  <span class="material-symbols-rounded">settings</span>
  ```
