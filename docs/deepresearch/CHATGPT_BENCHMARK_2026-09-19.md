# ChatGPT 深度研究 UI/动效对标（2026-09-19 实测）

会话：用户本人登录并确认使用的 ChatGPT Pro 账号（会话链接与账号信息不入库）。
视口：1440×757 CSS px，DPR 2。时间为 UTC（本地 +8）。

**完成度**：a–i 全部实测。第一轮（07:39–07:49 UTC，浅色主题）测了 a–e；窗口一度不可见后，第二轮（10:50–11:05 UTC，系统已切到深色主题）补测了 f、g、h、i，并逐屏转录了报告全文（未入库，只保留下面的结构统计）。
仍然拿不到的只有 iframe 内部的计算样式（见第 0、5 节）。

## 0. 决定性的结构发现

深度研究的计划卡、进度卡、报告卡、全屏阅读器、活动/来源面板**全部渲染在跨域 iframe 里**：

- origin `https://connector-openai-deep-research.web-sandbox.oaiusercontent.com`，`title="internal://deep-research"`，
  模板由 `GET /backend-api/ecosystem/widget?uri=connectors://connector_openai_deep_research&template_pointer=internal://deep-research` 下发（需 Bearer，未取）。
- 宿主页只负责：占位骨架、按 postMessage 设置 iframe 外层 `style.height`（**无 CSS transition，一步到位**：340 → 374 → 484px）、
  全屏时把外层切成 `position:fixed; inset:0 0 0 52px; z-index:50`（同样无过渡）、右侧面板容器。
- 因此 `javascript_tool`/`get_page_text`/无障碍树都进不去 iframe：**卡片内部的 CSS 值无法读计算样式，下文凡标注“截图量取”的数值来自 2x 截图的像素测量，误差约 ±1px；标注“计算值”的来自 `getComputedStyle`/`getAnimations`/样式表原文。**

## 1. 实测时间线与统计

| 时刻 (UTC) | 事件 | 依据 |
| --- | --- | --- |
| 07:39:42.47 | 回车提交 | 页面内打点 |
| 07:39:42.96 | “正在思考” shimmer 出现（提交后 ≈0.5 s） | `getAnimations` 轮询 |
| 07:39:50.58 | shimmer 被 “思考了 6s ›” 按钮替换 | MutationObserver |
| ≈07:39:51–07:40:06 | 助手气泡里出现一块灰色骨架（widget 占位）+ 下方一段说明文字 | 截图 |
| ≈07:40:06 | 计划卡出现（iframe 高 340px），倒计时圆环 | 07:40:18 读数 32、07:40:41 读数 9，反推起点 ≈45 s |
| ≈07:40:50 | 倒计时归零，自动开始；卡片原位变为进度卡（iframe 高 374px） | 截图 |
| 07:42:06 | 166 次搜索，步骤 1–2 同时进行中 | 截图 |
| 07:44:19 | 354 次搜索，步骤 1–4 同时进行中 | 截图 |
| 07:46:45 / 07:48:16 | 仍 354 次搜索，状态行未变 | 截图 |
| 07:48:51.87 | 报告卡出现（iframe 高 374 → 484px，单步跳变） | ResizeObserver |

- 总耗时：提交到报告 9m09s；研究阶段 ≈8m02s。统计行：**研究完成情况：8m · 45 次引用 · 438 个搜索**。
- 报告结构：1 个 h1 + 7 个 h2 + 2 个 h3；**9 张表**、**1 张 Mermaid 图**、2 处无序列表、1 处引用块（结尾一句话推荐）；约 4,500–5,000 汉字；全屏阅读器内总高 ≈12,900px；文末**没有**参考文献列表；1 处 LaTeX 未渲染（原样显示 `[ 768 \times 4 = 3072\ bytes/vector ]`）。
- 引用 45 条只来自 **5 个域名**：github.com 16、qdrant.tech 10、docs.weaviate.io 8、milvus.io 6、arxiv.org 5（全部是官方文档、官方仓库与论文）。同一页面的不同摘录算不同编号（同一篇 arXiv 论文 = 1、8、42、45）。来源面板底部另有“已扫描的来源 · 483”。
- 活动面板结尾：“Worked for 7m 55s” + 绿色 “Done”，随后一条汇总 Searching（“再显示 93 个”）和 “Generated report on <标题>”。
- 报告把改写时补的假设（10–30 人团队、1–3 名运维、1 亿–10 亿条 768 维、p99<200ms）写成了“你给定的场景”，用户原始请求里没有这些。

## 2. ChatGPT 观察（按阶段）

### 入口

- 输入框内蓝色模式胶囊“深度研究”（图标 + 蓝字），右侧强度下拉“极高”、听写、蓝色圆形发送键；下方“推荐 | 报告”文字页签，推荐项为 ↗ + 加粗短标题 + 灰色单行截断描述。键入 `@深度研究` 可选中该模式。
- 当日 Plus 账号的 “+” 菜单内没有该入口，Pro 账号有。
- 提交后用户气泡为浅蓝底（深度研究专属色），内含“深度研究”标签 + 正文，16px/24px，圆角约 24px。

### a. 提交后 → 计划卡出现

1. **shimmer 文案“正在思考”**（宿主 DOM，计算值）：
   - class `loading-shimmer-tertiary text-token-text-tertiary pb-0.5 select-none`，`animation: loading-shimmer 1.4s linear infinite`（线程内用 1.4s；全局变量 `--cot-shimmer-duration: 2s` 用于别处）。
   - 样式表原文：
     ```css
     .loading-shimmer, .loading-shimmer-tertiary {
       --shimmer-contrast: #ffffffbf;                       /* 暗色：#0009 */
       --shimmer-text-secondary: var(--text-secondary);     /* tertiary 变体取 --text-tertiary #8f8f8f */
       background: var(--shimmer-text-secondary)
         linear-gradient(to right, var(--shimmer-text-secondary) 0%, var(--shimmer-contrast) 40%,
                         var(--shimmer-contrast) 60%, var(--shimmer-text-secondary) 100%);
       background-size: 50% 200%; background-repeat: no-repeat; background-position: -100% top;
       background-clip: text; -webkit-text-fill-color: transparent; display: inline-block;
       animation-name: loading-shimmer; animation-iteration-count: infinite;
     }
     @keyframes loading-shimmer { 0% { background-position: -100% top; } 100% { background-position: 250% top; } }
     ```
2. 6 s 后替换为可展开的 “思考了 6s ›”（`text-token-text-tertiary`，16px）。
3. **widget 占位骨架**（宿主 DOM，计算值）：`div.loading-results-shimmer.absolute.inset-0.sm:rounded-3xl`，圆角 **24px**，
   `background: linear-gradient(to left, #f9f9f9, #ececec 50%, #f9f9f9); background-size: 1000px 100%`，
   `animation: loading-results-shimmer 3s linear infinite`；
   `@keyframes loading-results-shimmer { 0% { background-position: -1000px 0 } 100% { background-position: 1000px 0 } }`。
4. **骨架 → 卡片的过渡**（计算值）：骨架层 opacity → 0，WAAPI `duration 300ms, easing ease-out, fill both`；iframe 在其下直接显示。**高度不做动画**。
5. 计划卡**不是**流式长出来的：一次性整卡出现。卡片下方同时有一段普通助手文字（“深度研究已开始处理…最终报告会在深度研究界面中更新。”）。

### b. 计划卡（iframe 内，截图量取）

| 项 | 值 |
| --- | --- |
| 尺寸 | 宽 576px（iframe 768 内左对齐），高 338px（5 步） |
| 外观 | 白底 `#fff`（页面底 `#fcfcfc`）、1px 浅灰边（≈`rgba(0,0,0,.1)`）、**无阴影**、圆角 ≈24px（与宿主 `rounded-3xl` 骨架轮廓吻合） |
| 内边距 | 左右 16px；标题基线距顶 ≈29px |
| 标题 | “开源向量数据库选型对比” 16px / w600 / `#0d0d0d` |
| 步骤 | 5 条，行距 **44px**（y=305/349/393/437/481），文字 16px/24px w400 `#0d0d0d`；图标 18px，图标与文字间距 ≈12px；**待开始 = 浅灰虚线圆**（stroke ≈1.5px，`#c4c4c4`，约 12 段 dash） |
| 按钮行 | 高 ≈36px、`border-radius: 9999px`、14px w500；“编辑”“取消”= 白底 1px 灰边；“开始”= `#0d0d0d` 底白字，宽 ≈83px，右侧内嵌圆环 |
| 圆环倒计时 | 直径 ≈24px，轨道为深灰细环、**剩余部分为白色弧线**，从 12 点方向顺时针缩短（读数 9 时弧长 ≈72°，即 9/45）；中心数字 ≈11px 白字；数字每秒变化。弧线是否逐帧平滑（`stroke-dashoffset` 线性过渡）**无法从截图确认**，实现方式（SVG/conic）也读不到 |
| 倒计时时长 | ≈45 s（由 32@07:40:18、9@07:40:41 反推；首个读数不是 45，存在 ±1 s 不确定） |

倒计时结束自动开始；未点取消/编辑（编辑与更新的交互见 `docs/deepresearch/CHATGPT_BENCHMARK_2026-09-16.md`）。

### c. 进度卡（iframe 内，截图量取）

- **原位替换**：同一张卡，按钮行消失，右上角出现“更新”胶囊（白底 1px 灰边，高 ≈36px，14px w500），底部出现状态行 + 进度条；iframe 高度 340 → 374px 单步跳变。
- **步骤图标**：进行中 = 18px 圆环 spinner（深色 `#0d0d0d` 环，约 1/4 弧为浅灰，stroke ≈2px），**持续旋转**（相隔数秒的两帧缺口位置不同）；待开始 = 虚线圆；**多个步骤可同时处于进行中**（07:42 两个、07:44 四个）。本次运行在报告出现前**没有任何步骤变成“已完成”**，因此“已完成”图标形态未观察到。进行中/待开始的文字颜色相同（都是 `#0d0d0d`），不置灰。
- **状态行**：13px 左右、`#5d5d5d`，**带 shimmer 流光**（2x 截图中 “report structure…” 一段明显变浅）；文案为英文进展标题 + “…”（本次全程只有一条 “Planning current research and report structure…”，未出现切换，因此**切换动效未观察到**）。点击状态行打开右侧活动面板。
- **搜索计数**：右对齐 “166 次搜索”→“354 次搜索”，14px `#8f8f8f`；更新是跳变（166→354 之间无中间值被截到，数字滚动动效**未观察到**）。
- **进度条**：高 ≈4px，轨道 `#ececec`，填充 `#0d0d0d`，两端全圆角；填充比例 12.4%（07:42:06）→ 19.8%（07:42:53 起一直到完成前），**阶梯式**，不是匀速爬升。
- **停止**：进度条右侧 28px 圆形浅灰底（`#ececec`）按钮，内为 ≈8px 黑色圆角方块。
- 研究中输入框占位符为“获取详细报告”，发送键变为蓝底方块（停止流式）后恢复为语音键。

### d. 活动面板（研究中，宿主容器计算值 + iframe 内截图量取）

- 容器：宽 **375px**（374 + `border-left: 1px rgba(0,0,0,.05)`），`bg #fff`，高 100vh；头部 53px：标题（计划标题，18px w400）+ 右侧 ✕。打开时线程列从 768 收窄到 640px，卡片随之变窄。打开后先显示“研究活动”标题 + 8 辐条小 spinner，约 1 s 后出内容。滑入动效未截到。
- 时间线条目（14px/20px）：
  - 进展摘要：6–7px 实心圆点 + 黑色标题；下方灰色（`#5d5d5d`）第一人称段落，左侧 1px 浅灰竖线贯穿到下一条目（最后一条无竖线）。
  - 搜索：14px 地球图标 + “Searching” / “Searching 18 websites”；下方域名 chip。
- **域名 chip**：高 20px、全圆角、底 `#f4f4f4`、`padding: 0 8px`，favicon 12px + 8px 间距 + 域名 13px `#5d5d5d`（字距略宽）；chip 间距 8px、行距 8px；每组最多显示 4 个，其余折叠成同款 chip “再显示 15 个”，点击后**原地即时展开**（无动画），悬停底色加深。favicon **懒加载**：chip 先以无图标形态出现，图标随后补上。

### e. 完成态（iframe 内，截图量取）

- 进度卡被整体替换为：统计行 + 报告卡；报告**一次性出现**（不流式）。研究中打开的活动面板在完成时自动收起。
- **统计行**：“研究完成情况：8m · 45 次引用 · 438 个搜索”，14px `#5d5d5d`，距卡片 ≈12px。
- **报告卡**：宽 756px（占满线程列 768 减滚动条）、高 ≈450px、白底、1px 浅灰边、圆角 ≈16px、无阴影。
  - 头部 48px：24px 蓝色圆角方块文档图标（`#0285ff` 系，圆角 ≈6px）+ 标题 14px w500；右侧两个 20px 线性图标（下载、全屏），间距 36px；头部与正文之间**没有分隔线**。
  - 预览区 ≈400px 高，**内部可滚动**（右侧有滚动条），底部 ≈64px 白色渐隐遮罩；**没有“阅读全文”按钮**。
  - 预览排版（比全屏小一号）：h1 24px/31px w700；h2 20px w600；正文 14px/22px；表头 13px w600 + 1px 深色下边线；单元格 13px；表格右上角悬浮复制按钮。

### f. 全屏阅读器

- **打开**：同一个 iframe 被宿主切到 `position:fixed`（`left:52px` 让出侧栏图标轨，`z-index:50`），宿主侧栏同时从 260px 收成 52px 图标轨（侧栏自身过渡：`opacity 150ms`、`transform 180ms cubic-bezier(.2,0,0,1)`）；宿主只记录到一个 `opacity 300ms ease-in-out`，iframe 容器本身无位移/缩放动画。点击后 ≈150ms 的中间帧里内容区正从左向右展开，2 s 后稳定。**窗口不可见（`visibilityState: hidden`）时点击全屏无效。**
- **关闭**：点 ✕ 后首帧截图（<300ms）已回到对话内的报告卡，**无可见过渡**；侧栏保持图标轨，不自动展开。
- **顶栏**：左 ✕（20px，x=74）；右 下载（圆形下箭头）、“来源与活动”开关（链环图标，悬停有 tooltip：反色、圆角 8px、13px）；**不显示标题**；静止时无底边线，**正文滚动后出现 1px 底边线**（滚动联动）。
- **正文栏**：宽 **624px**，水平居中于可用区域；右侧面板打开时整栏左移重新居中（x 423→233），栏宽不变。h1 28px/33px w700；h2 24px w600（上距 ≈36px）；h3 20px w600；正文 16px/24px；段间距 ≈8px；列表项行距 32px；行内代码等宽 + 浅底；引用块左侧 2px 竖线 + 斜体。
- **表格**：表头 14px w600 + 1px 前景色下边线，行间 1px 浅线，单元格 14px/20px、上下内边距 ≈12px，首列加粗，无斑马纹、无外框；**超宽表格在栏内横向滚动**（右侧直接裁切，表格下方有一条 ≈4px 高的圆角自绘滚动指示条）；表格右上角悬停出现复制按钮（32px 圆角方块）。
- **Mermaid 图**：单色线框（1px 描边矩形/菱形/圆柱，≈11px 等宽标签，细箭头），无底色卡片，居中，宽度不超过正文栏；深色主题下为浅色线条。
- **目录刻度条**：`left ≈62px`，首刻度与 h1 顶对齐（y≈118）；刻度高 2px、间距 **15px**；当前 = 24px 宽前景色；h2 = 18px、h3 = 12px，弱灰；h1 也占一个刻度。**当前刻度随滚动实时切换**。
- **目录浮层**：悬停刻度条即出现并**覆盖刻度条**，位置 (57,113)，宽 286px、高随内容（500px），1px 浅边 + 柔和阴影、圆角 ≈16px、内边距 20px；“目录” 12px 灰；条目 16px/24px、间距 12px；当前章节前景色 w600，其余 `--text-tertiary` w400；h3 缩进 16px。悬停后首帧截图已完整显示（<≈150ms）。点击条目的滚动动画未截到（第一轮窗口不可见时点击无响应，第二轮未重测）。

### g. 引用

- **标记**：句末上标小圆 chip，≈14px 圆、浅灰底、≈9px 数字，纯数字；一个标记可以聚合多个来源（悬停卡顶部有 ← → 切换），未见域名胶囊或 “+N” 文案。
- **悬停卡**：鼠标移上去后首帧（<≈200ms）已在淡入，1 s 后稳定；位于标记**右下方**（左缘对齐标记），宽 **≈340px**、高 ≈157px、圆角 ≈14px、1px 边 + 阴影。结构自上而下：① 32px 高的浅色条，内含 ← → 两个 16px 箭头；② favicon 16px + 域名 14px 灰；③ 标题 15px w600，**2 行截断**；④ 摘录 14px 灰，**2 行截断**。没有日期、没有 URL、没有编号。
- **点击**：右侧面板直接打开到“来源”页签并滚到对应条目；该条目加**圆角 16px 的浅底高亮块**；**正文里被点的标记同时变成实心高亮圆**（深色主题下为米黄色底深色字）；正文栏左移重新居中。点击后 <300ms 的首帧里面板已完全展开，未见滑入过程。
- **来源面板**（宽 ≈375px + 滚动条）：头部 48px，页签“来源 / 活动· 8m”——当前页签是 **46×34px、圆角 ≈12px 的浅底胶囊**，14px w500，非当前无底色；右侧 32px 圆角方块 ✕。“引用· 45” 14px 灰；按域名分组：favicon 16px + 域名 13px；条目 = 18px 圆形编号徽标（≈10px 数字）+ 标题 15px w600 **单行截断** + 第二行（摘录 2 行截断，或 URL）14px 灰；条目间距 ≈60–78px。引用列表之后是“已扫描的来源· 483”，同样按域名分组但没有编号徽标。
- **页签切换**：点击“活动”后首帧里旧内容整体变淡——**交叉淡入淡出**，≈1 s 内完成。

### h. 下载菜单

点击下载图标弹出菜单（宽 ≈193px、圆角 ≈16px、1px 边 + 阴影，条目 14px、行距 36px、无图标）：**复制内容 / 导出到 Markdown / 导出到 Word / 导出到 PDF**。未实际导出。

### i. 深色 / 浅色

第二轮时系统外观已切到深色，页面自动跟随（`html.dark`，`prefers-color-scheme: dark`）：

| token | 浅色 | 深色 |
| --- | --- | --- |
| `--main-surface-primary` | `#fcfcfc` | `#000` |
| `--main-surface-secondary` | `#f9f9f9` | `#212121` |
| `--text-primary / secondary / tertiary` | `#0d0d0d / #5d5d5d / #8f8f8f` | `#fff / #cdcdcd / #afafaf` |
| `--border-light / medium` | `#0000000d / #00000026` | `#ffffff0d / #ffffff26` |
| shimmer 高光 | `#ffffffbf` | `#0009` |

深色下：阅读器背景 ≈`#0d0d0d`～`#111`（比侧栏图标轨的纯黑略浅），报告卡/悬停卡/菜单底 ≈`#212121`，表头下边线为白色，用户气泡为深蓝；结构与尺寸与浅色完全一致，只换色。

### 全局 token（宿主 `:root` 计算值）

`--text-primary #0d0d0d`、`--text-secondary #5d5d5d`、`--text-tertiary #8f8f8f`、`--main-surface-primary #fcfcfc`、`--main-surface-secondary #f9f9f9`、
`--border-light #0000000d`、`--border-medium #00000026`、`--cot-shimmer-duration 2s`；缓动用 CSS `linear(...)` 弹簧曲线（`--spring-fast/common/bounce`）；
常见过渡：`background-color 150ms cubic-bezier(.4,0,.2,1)`、`opacity/width 120ms cubic-bezier(0,0,.2,1)`、`transform 180ms cubic-bezier(.2,0,0,1)`。
其他关键帧原文：

```css
@keyframes shimmer-skeleton { 0% { background-position: 100% center } 100% { background-position: 0% center } }
@keyframes pulse-dot { 0% { opacity:.1; scale:.7 } 50% { transform: scale(var(--pulse-scale,1.3)); opacity:1 } 100% { opacity:0; transform: scale(.7) } }
@keyframes icon-shimmer { 0% { mask-position: 100% center } 20%,100% { mask-position: 0 center } }
@keyframes le-qua_streaming-response-content-enter { 0% { opacity:0 } 100% { opacity:1 } }
@keyframes show { 0% { opacity:0 } 100% { opacity:1 } }
```

## 3. 我方现状（127.0.0.1:3100，运行 `56343f45`：4m · 31 次引用 · 31 个搜索）

任务给出的 `9718f578-…` 返回 “Research run not found”，改用侧栏历史中的已完成研究。

| 元素 | 实测/源码 | 位置 |
| --- | --- | --- |
| 主题底色 | `--background: oklch(0.9855 0.0098 87.47)`（暖米色）；卡片多为半透明 `bg-muted/10~20` | `frontend/src/styles/globals.css:246-248` |
| 等待阶段 | 无 shimmer、无骨架；只有输入框占位符“正在制定研究计划…” | `research-conversation.tsx:613-628` |
| 计划卡 | `max-w-xl`(576) `rounded-2xl`(18px) `border-border/60` `bg-muted/20` `p-4`；无入场动画 | `plan-card.tsx:187-190` |
| 标题/步骤 | 标题 16px/24px w500；步骤 **14px**/24px，`space-y-3 py-4`（行距 36px），图标 16px；研究中待开始步骤文字置灰 | `plan-card.tsx:137-163,192` |
| 步骤图标 | 进行中 lucide `LoaderCircle` + `animate-spin`；待开始 `CircleDashed`；完成 `CircleCheck`；失败 `CircleAlert` | `plan-card.tsx:145-156` |
| 圆环倒计时 | SVG r=8、stroke 1.5、`stroke-dashoffset` + `transition 300ms`；值按整秒变化 → 一秒一跳；轨道 `opacity-20` | `plan-card.tsx:30-60` |
| 按钮 | `size="sm"`(h-8) `rounded-full`；布局与 ChatGPT 相同（编辑 \| 取消 开始+环） | `plan-card.tsx:218-255` |
| 状态行 | `text-xs` 纯文本替换，无 shimmer、无切换动效 | `plan-card.tsx:268-276` |
| 搜索计数 | `text-xs tabular-nums` 纯文本 | `plan-card.tsx:277-281` |
| 进度条 | 高 4px、`bg-muted` 轨道、`bg-foreground/70` 填充、`transition-[width] 500ms` | `plan-card.tsx:284-290` |
| 停止/更新 | 停止：ghost icon 方块 12px（无圆底）；更新：outline `h-7 rounded-full` | `plan-card.tsx:193-204,292-300` |
| 完成后的计划卡 | 原生 `<details>` 折叠条留在对话里（576×46） | `plan-card.tsx:169-177` |
| 改写卡 | “已改写研究请求 ⌄” 折叠条 | `request-card.tsx` |
| 统计行 | 12px/16px `text-muted-foreground` | `report-view.tsx:154-156` |
| 报告卡 | 784×407、`rounded-2xl`、`bg-muted/10`、头部有底边线；图标 20px **黑底**方块；标题 14px w500 | `report-view.tsx:157-166` |
| 预览区 | `max-h-80 overflow-hidden`（**不可滚动**）+ 80px 渐隐 + 整行“阅读全文”按钮；预览字号与全屏相同（15px） | `report-view.tsx:167-177` |
| 全屏阅读器 | 状态切换直接挂载，**无过渡**；顶栏显示标题 + 底边线 | `research-conversation.tsx:452-476,540-557` |
| 正文 | 栏宽 **816px**，15px/28px；h1 24px、h2 20px `mt-10`、h3 16px；表格 14px | `report-view.tsx:93`，`report-reader.tsx:110` |
| 目录刻度条 | `top-24 left-3`；刻度高 2px、间距 8px；h2 20px / h3 14px（缩进 6px）；不含 h1；`transition-colors 150ms` | `report-reader.tsx:65-80` |
| 目录浮层 | `w-64 rounded-xl border p-3 shadow-lg`，仅 `opacity` 过渡；条目 12px/20px；**刻度条不隐藏**，浮层叠在其上 | `report-reader.tsx:81-101` |
| **布局缺陷** | 右侧面板打开后正文左缘 x=283，刻度条占 x=268–304，**压在标题上** | 实测截图 |
| 引用标记 | 16×16 胶囊 `bg-muted` 10px，悬停反色黑底白字 | `report-view.tsx:65-73` |
| 引用悬停卡 | HoverCard `openDelay 150 / closeDelay 80`，`w-96 p-3 rounded-md shadow-md`，`fade-in + zoom-in-95 + slide-in 150ms` | `report-view.tsx:63-79`，`ui/hover-card.tsx:35` |
| 点击引用 | 打开“来源”，选中项 `bg-muted`；**选中项展开最长 900 字原始抓取文本（含 ``` 与 `[text](url)` 语法），实测单项高 838px** | `sources-panel.tsx:177-196` |
| 右侧面板 | 宽 473px（可拖拽），`flex-grow 280ms ease-out` + `opacity 280ms`；页签为 shadcn Button（“来源 / 活动 · 4m / 指标”） | `chat-box.tsx:428,462`，`research-conversation.tsx:327-346` |
| 活动时间线 | 13px/20px，`space-y-4`；chip `bg-muted/60 text-[11px] py-0.5 pl-0.5 pr-2` + 16px favicon；>4 折叠；条目类型多（阅读/搜索/工具/章节…），每条都带图标 | `sources-panel.tsx:241-456` |
| 下载菜单 | Word / MD / HTML | `report-view.tsx:120-124` |

## 4. 差距清单

| 元素 | ChatGPT | 我方 | 差距 | 建议改法 | 优先级 |
| --- | --- | --- | --- | --- | --- |
| 整体色调 | 冷白：页面 `#fcfcfc`、卡片纯白、文字 `#0d0d0d`/`#5d5d5d`/`#8f8f8f` | 暖米色底 + 半透明灰卡片 | 一眼不同 | 研究页作用域内覆写 token：`--background:#fcfcfc; --card:#fff; --muted-foreground:#5d5d5d`；卡片 `bg-muted/20` → `bg-card` | P0 |
| 提交后等待 | “正在思考” shimmer（1.4s linear）→ “思考了 6s ›” → 24px 圆角骨架块 shimmer（3s linear）→ 300ms ease-out 淡出换成计划卡 | 无任何占位 | 提交后页面静止 | `research-conversation.tsx`：`PLANNING/CREATED` 时在消息区渲染 shimmer 文案 + 骨架块；`globals.css` 增加 2.a 的 `loading-shimmer` 与 `loading-results-shimmer` 两组原文；计划卡包一层 `animate-in fade-in duration-300 ease-out` | P0 |
| 步骤文字 | 16px/24px，行距 44px，不置灰 | 14px/24px，行距 36px，待开始置灰 | 卡片显得小而密 | `plan-card.tsx:137-143`：`text-base`、`space-y-5`；去掉 `text-muted-foreground` 分支 | P0 |
| 计划/进度卡外观 | 纯白、1px `rgba(0,0,0,.1)`、圆角 ≈24px、标题 w600 | 18px 圆角、半透明灰底、w500 | 轮廓与底色不同 | `rounded-3xl bg-card border-black/10`，标题 `font-semibold` | P0 |
| 进行中图标 | 18px 深色圆环 + 1/4 浅灰缺口，stroke 2px，旋转；可多个并行 | lucide `LoaderCircle`（3/4 弧、细线）16px | 形态不同 | 自绘 SVG：`<circle r=8 stroke=currentColor opacity=.25/>` + `<circle stroke-dasharray="37.7 12.6"/>`，`size-[18px] animate-spin` | P0 |
| 状态行 | `#5d5d5d` 13px + shimmer 流光；可点开活动面板 | 12px 静态文本 | 没有“活着”的感觉 | `plan-card.tsx:275`：加 `loading-shimmer` 类；`key={text}` + `animate-in fade-in duration-150` | P0 |
| 选中来源 | 只加一个圆角 16px 浅底高亮块，条目仍是“标题单行 + 摘录 2 行”，不展开；正文里的标记同步变实心高亮 | 展开最长 900 字原始抓取文本（含 Markdown 围栏与链接语法），单项 838px；正文标记无选中态 | 面板被一条来源撑满 | `sources-panel.tsx:177-196` 删除展开块（摘录留给悬停卡）；`report-view.tsx:65-73` 给选中编号的按钮加 `bg-amber-200 text-foreground` | P0 |
| 目录刻度条重叠 | 正文 624px 居中，刻度条永不重叠 | 面板打开时压住标题 | 我方自身缺陷 | `report-reader.tsx`：scroller 加 `md:pl-16`，或 `@container` 宽度不足时隐藏刻度条 | P0 |
| 阅读器正文 | 栏宽 624px；16px/24px；h1 28 w700、h2 24 w600 | 816px；15px/28px；h1 24、h2 20 | 行太长、标题层级弱 | `report-reader.tsx:110` `max-w-[39rem]`；`report-view.tsx:93` 全屏态 `text-base leading-6 [&_h1]:text-[28px] [&_h2]:text-2xl`；预览态另用 14px/22px、h1 24、h2 20 | P0 |
| 报告卡预览 | 预览区内部可滚动、≈400px 高、64px 渐隐、无“阅读全文”、头部无分隔线、蓝色 24px 图标 | 320px 不可滚动 + “阅读全文”按钮 + 头部底线 + 黑色 20px 图标 | 结构不同 | `report-view.tsx:158-177`：去 `border-b`、图标 `size-6 bg-[#0285ff] rounded-md`；预览 `max-h-[400px] overflow-y-auto` + `sticky bottom-0 h-16` 渐隐；删除按钮 | P0 |
| 完成后对话区 | 进度卡被统计行 + 报告卡**替换**，不留计划折叠条 | 留 `<details>` 计划条 + 改写折叠条 | 多两行杂项 | `plan-card.tsx:169-177`：已出报告时返回 `null`（计划可在活动面板查看）；改写卡保留但弱化，或移入活动面板 | P1 |
| 圆环倒计时 | 深色按钮内 24px 环，白色剩余弧，数字 11px | 20 viewBox 环，整秒跳变 + 300ms 过渡 | 我方一秒一跳（ChatGPT 是否平滑未能确认） | `CountdownRing`：`transition: stroke-dashoffset 1s linear`，`size-6`，轨道 `opacity-30` | P1 |
| 统计行 | 14px `#5d5d5d` | 12px | 偏小 | `report-view.tsx:154` `text-sm` | P1 |
| 停止按钮 | 28px 圆形 `#ececec` 底 + 8px 黑方块 | ghost 无底 12px 方块 | 形态不同 | `plan-card.tsx:292-300`：`size-7 rounded-full bg-muted` + `Square size-2` | P1 |
| 按钮高度 | ≈36px、14px w500 | h-8(32px)；“更新” h-7 | 偏小 | `size="default"` 或 `h-9 px-4`；“更新”同高 | P1 |
| 进度条 | 填充 `#0d0d0d`、轨道 `#ececec`、阶梯推进 | `foreground/70` | 对比度略低 | `bg-foreground`；保留 500ms 过渡 | P2 |
| 目录刻度 | 间距 15px；当前 24px、h2 18px、h3 12px；含 h1；悬停时浮层**替换**刻度条 | 间距 8px；20/14px；不含 h1；浮层叠在刻度上 | 更密、层级不同 | `report-reader.tsx:69-79`：`gap-[13px]`，宽度 `w-6/w-[18px]/w-3`，纳入 h1；`group-hover:opacity-0` 隐藏刻度 | P1 |
| 目录浮层 | 286px 宽、圆角 16、内边距 20、条目 **16px/24px**、间距 12px、当前 w600 黑 / 其余 `#8f8f8f` | 256px、12px/20px 小字 | 我方像工具提示而不是目录 | `report-reader.tsx:81-101`：`w-72 rounded-2xl p-5`，条目 `text-base leading-6 py-1.5`，去 `hover:bg-muted` 方块感 | P1 |
| 阅读器顶栏 | 只有 ✕ 与右侧两个图标，无标题、无底线 | 标题 + 底线 | 更“页面化” | `research-conversation.tsx:452-476`：阅读态不渲染标题，去掉 header 边线 | P1 |
| 阅读器开合 | 宿主 300ms opacity + 侧栏收成 52px 图标轨 | 无过渡，侧栏不变 | 切换生硬 | 阅读器容器 `animate-in fade-in duration-300`；进入阅读态时 `setOpen(false)` 收起侧栏，退出恢复 | P1 |
| 表格 | 表头 1px 深色下边线、行间浅线、无外框、首列加粗、单元格 14px/20px | 沿用通用 Markdown 表格样式 | 需核对 | `.research-report table` 专用样式：`th{border-bottom:1px solid var(--foreground)} td{border-top:1px solid var(--border);padding:12px 8px}` | P1 |
| 引用标记 | ≈14px 圆、`#ececec`、9px 数字 | 16px 胶囊 10px | 接近 | `h-3.5 min-w-3.5 text-[9px]` | P2 |
| 活动面板 | 宽 375px、纯白、1px `rgba(0,0,0,.05)` 左边线；头部为计划标题 18px；条目只有“摘要圆点 / Searching 地球”两类，14px/20px | 473px、三页签、十余种条目类型、13px | 我方信息更密 | 默认宽度 375；“搜索/阅读”合并为一类，连续同类折叠；正文 `text-sm leading-5` | P1 |
| 域名 chip | 高 20、`#f4f4f4`、`0 8px`、favicon 12px、13px 字 | `text-[11px]`、favicon 16px 贴左 | 偏小 | `sources-panel.tsx:247-253`：`h-5 px-2 gap-2 text-[13px] bg-[#f4f4f4]`，`SiteIcon size-3` | P2 |
| 搜索计数 | 14px `#8f8f8f` | 12px | 偏小 | `text-sm text-muted-foreground/80` | P2 |
| 引用悬停卡 | ≈340px 宽；顶部 ← → 切换条；favicon+域名、标题 2 行、摘录 **2 行**；无 URL/日期/编号；<200ms 淡入 | 384px；域名+`[n]`、标题、“原文片段”引用块 **6 行**、片段计数、日期·URL；150ms 延迟 + zoom/slide | 我方卡片高（实测 246px vs 157px）、信息堆叠 | `citation-preview.tsx:101-138`：摘录 `line-clamp-2`、去掉引用块左线与小标题、移除 URL 行；`report-view.tsx:76` `w-[340px] rounded-2xl`；同编号多摘录用 ← → 切换而不是“另有 N 段” | P1 |
| 来源条目 | 18px 编号徽标 + 标题 15px w600 **单行** + 摘录/URL 14px 2 行；favicon 16px | 20px 徽标 + 标题 13px 2 行 + 摘要 12px + URL 11px 三层 | 我方更碎更小 | `sources-panel.tsx:120-163`：标题 `text-[15px] font-semibold truncate`；摘要与 URL 二选一，`text-sm` | P1 |
| 宽表格 | 栏内横向滚动 + 表格下方 4px 圆角滚动指示条；悬停右上角复制按钮 | 沿用通用 Markdown 表格 | 需核对 5 列以上表格在窄栏内的表现 | `.research-report table` 外包 `overflow-x-auto`；加自绘滚动条样式与复制按钮 | P1 |
| 面板页签 | 46×34、圆角 12 浅底胶囊，切换内容交叉淡入淡出 | shadcn `secondary` Button 8px 圆角，切换无动效；多一个“指标” | 形态接近，缺动效 | `research-conversation.tsx:327-346`：`rounded-xl h-[34px]`；内容区 `key={tab}` + `animate-in fade-in duration-200`；“指标”收进 Trace 入口 | P2 |
| 已扫描来源 | 引用列表后接“已扫描的来源 · 483”，同样按域名分组 | `<details>` “研究中接触的其他网页 · N” 默认折叠、平铺 | 呈现方式不同 | `sources-panel.tsx:207-236`：去掉 `<details>`，改成与引用相同的按域名分组（无编号） | P2 |
| 阅读器顶栏滚动线 | 静止无线，滚动后出现 1px 底边线 | 始终有底边线 | 细节 | 监听 scroller `scrollTop > 0` 切换 `border-b` | P2 |
| 下载菜单 | 复制内容 / Markdown / Word / PDF | Word / MD / HTML | 缺“复制内容”和 PDF，多 HTML | `report-view.tsx:120-124`：加“复制内容”（`navigator.clipboard` 写 Markdown）；PDF 视后端能力 | P2 |
| 深色主题 | 纯黑 / `#212121` 两级表面，只换色不换结构 | 暖灰 `oklch(0.24 0.0036 106.64)` | 色温不同 | 研究页作用域内深色 token：`--background:#0d0d0d; --card:#212121; --muted-foreground:#afafaf` | P2 |

## 5. 无法观察到或不确定的项目

- **iframe 内部的真实 CSS/动画参数**（圆环实现方式与缓动、spinner 转速、状态行 shimmer 时长、目录浮层与悬停卡的过渡曲线、页签交叉淡入时长）：卡片全部在跨域 iframe 里，读不到计算样式；widget 模板接口需要账号 Bearer token，我没有去取。这些项只有截图量取的尺寸与“首帧是否已出现”级别的时序。
- 截图工具单次往返 ≈150–300ms，短于此的过渡（目录浮层、阅读器关闭、面板展开）只能判定为“<≈200ms 或无过渡”，给不出精确毫秒数。
- “已完成”步骤图标、状态行文案切换、搜索计数滚动：本次运行全程只有一条状态文案，步骤在出报告前没有变成已完成，未出现对应状态。
- 目录条目点击后的滚动动画：第一轮点击时窗口不可见（无响应），第二轮未重测。
- 计划卡圆角（≈24px）与倒计时总时长（≈45 s）为反推值。
- 报告全文为逐屏截图人工转录：宽表最右列被横向裁切的部分用 “…” 标出；未使用导出（未获下载许可）。
- 我方深色主题、390px 视口未复验；我方研究中的进度卡动效来自源码阅读而非实跑。

## 6. 同题对照：我方（2026-09-19，修复与调参之后）

同一条请求（“对比 2026 年主流的开源向量数据库（Milvus、Qdrant、Weaviate、pgvector），从性能、运维成本、生态与许可证角度给出中型团队的选型建议”），
我方运行 `82225b11`：DeepSeek `deepseek-v4-flash`，公网搜索（DuckDuckGo 兜底）与网页读取（Jina 无 key + 直接抓取），
研究配置覆盖层为：`max_concurrency: 6`、`writer_concurrency: 8`、`plan_min_units/plan_max_units: 4/6`、`max_searches_per_unit: 20`、
`supplement_gap_codes: [coverage, unsupported]`、JSON 节点温度 0–0.2、`section` 0.5。

| | ChatGPT | 我方 `82225b11` |
| --- | --- | --- |
| 提交到报告 | 9 分 09 秒（研究约 8 分 02 秒） | **5 分 39 秒**（改写 3 秒、计划 11 秒、研究 279 秒、写报告 42 秒） |
| 搜索 / 读取页面 | 438 次搜索（已扫描来源 483） | 47 次搜索、79 个页面 |
| 引用 | 45 条，5 个域名（github.com、qdrant.tech、docs.weaviate.io、milvus.io、arxiv.org） | 60 条，10 个域名（milvus.io 16、qdrant.tech 11、docs.weaviate.io 9、github.com 7、raw.githubusercontent.com 6、zilliz.com 3 …） |
| 报告规模 | 约 4,500–5,000 字、7 个二级章节、9 张表、1 张 Mermaid 图 | 29,486 字符、8 个二级章节、8 张表、1 张 Mermaid 图 |
| 假设的处理 | 把改写时补的假设写成“你给定的场景” | 单独一节“把未提供的团队条件显式化为假设”，并列出每个假设改变后会翻转的结论 |
| 错误 | 1 处 LaTeX 未渲染 | 0 次模型错误、0 次截断、0 次格式重试、0 条被删除的陈述 |
| Token / 费用 | 不可见 | 190.6 万 token；按节点：research 160.8 万、section 14.6 万、conversion 6.0 万、summary 3.6 万、compaction 3.6 万 |

结论：耗时已经快于 ChatGPT（此前约为它的 2 倍，主要来自研究并发 3 → 6、补研只为“没有发现/结论无证据”触发、整理输出减半）；
来源同样以官方文档、官方仓库为主；报告篇幅约为 ChatGPT 的 5–6 倍。`report_length_scale` 可以压缩篇幅，但模型并不严格按字数写：
同题把它设为 0.45 并关闭摘要节点后（运行 `8654f210`），报告 18,185 字符、8 表 1 图、59 条引用、372 秒，其中两个章节触发了一次“请缩短”的修复。
搜索次数的量级差异（438 对 47）来自 ChatGPT 单次研究内大量并行的检索；我方每个研究单元把检索次数控制在 `max_searches_per_unit` 内，
改为多读原文（79 个页面）。报告事实没有逐条人工核验。
