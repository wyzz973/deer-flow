# 自建 MCP 的返回格式建议

写给能自己决定 MCP 服务返回什么的人。DeepResearch 本身不要求统一格式（`extract.py` 按结构识别，不认某一家的字段名），
所以下面不是「必须这样」，而是**这样写，每个下游判断都能做对，而且不会静默丢内容**。

本文每一条都在本仓库当前代码上实测过，标了实测数字的地方就是跑出来的结果。

---

## 一、结论：最小必要格式

搜索 / 知识库检索（一次调用返回若干条结果）：

```json
{
  "results": [
    {
      "title": "向量检索平台 SLA",
      "url": "https://wiki.corp.example/vector-sla",
      "snippet": "Qdrant 共享集群承诺可用性 99.9%，P99 检索延迟不超过 60ms（top_k ≤ 50）。"
    },
    {
      "title": "向量检索接入指引",
      "url": "https://wiki.corp.example/vector-onboarding",
      "snippet": "默认接入 Qdrant 共享集群，单业务配额 3000 万向量、峰值 500 QPS，超出需申请独立集群。"
    }
  ]
}
```

三个字段就够：`title`、`url`、`snippet`。其中 **`snippet` 就是会被报告引用的「原文片段」**。

可选加两个，不影响识别：

```json
{ "title": "...", "url": "...", "snippet": "...", "published_at": "2026-07-10", "id": "wiki-sla" }
```

没有结果时不要报错，返回空列表加一句人话：

```json
{ "results": [], "message": "没有匹配的文档；尝试更具体的关键词或换用工单检索。" }
```

真正的失败（鉴权过期、后端不可用）要用 MCP 的错误状态（`isError`）返回，**不要**用 200 加一段
「查询失败」的文字——那会被当成一条普通答案，研究员可能把它当成内容读。

---

## 二、每个字段最终变成什么

| 字段 | 去向 | 缺了会怎样 |
| --- | --- | --- |
| `snippet` | 证据正文，报告引用的就是它 | **该条结果不会成为证据**，静默消失（实测：只有 title+url 的结果一条都不可引用） |
| `url` | 证据地址、引用编号的依据、来源域名统计 | 仍可引用，但引用列表里没有链接，读者无法回溯 |
| `title` | 引用列表里显示的标题 | 用正文第一行凑一个标题（实测会得到很难看的截断标题） |
| `published_at` | **只给模型看**，不会成为可查询的证据元数据 | 模型无法判断时效；详见第五节 |
| `id` | 只出现在日志与导出里，方便你回查 | 无影响 |
| 其他字段（`score`、`metadata`、`_index`…） | 不读，但会占模型上下文 | 建议删掉，见第四节 |

`level`（来源等级 L1–L4）和 `publisher`（用于判断来源是否独立）**不能由返回内容决定**，
它们在研究配置的数据源上声明。你的 MCP 说自己是权威来源不算数，这是刻意的。

---

## 三、四条硬性规则（不满足就丢内容）

### 1. `snippet` 必填

没有非空 `snippet` 的结果不会成为证据。这是实测结论，不是约定：

| 返回 | 可引用证据 |
| --- | --- |
| `{"results":[{"title":"SLA","url":"...","snippet":"可用性 99.9%。"}]}` | 1 条，带标题和链接 |
| `{"results":[{"title":"SLA","url":"..."}]}`（没有 snippet） | **0 条** |

### 2. 一个 URL 只出一条结果

结果按 `url` 去重，**同一个 URL 的第二条会被静默丢弃**（实测：三条结果里两条同 URL，最终只剩两条证据）。
同一页要给多段摘要时，二选一：

**推荐：给每段一个锚点。** 两段都成为独立证据，而引用编号仍然合并成同一篇：

```json
{"results": [
  {"title": "SLA · 可用性", "url": "https://wiki.corp.example/sla#availability", "snippet": "承诺可用性 99.9%。"},
  {"title": "SLA · 延迟",   "url": "https://wiki.corp.example/sla#latency",      "snippet": "P99 不超过 60ms。"}
]}
```

（实测：2 条证据、1 个引用编号。锚点不以 `/` 或 `!` 开头时不参与页面身份判断，所以是同一篇。）

**或者：把几段拼成一条 `snippet`**，用换行分隔。一条证据、一个编号。

### 3. JSON 不要转义非 ASCII

`json.dumps(..., ensure_ascii=False)`。同样的内容实测差一倍多：

| 写法 | 字符数 |
| --- | --- |
| 精简 JSON，`ensure_ascii=False` | 1589 |
| 同样内容，转义成 `\uXXXX` | **3349** |

对 `kind: mcp`（直接暴露给研究员的工具）来说，**返回的原文就是模型读到的内容**，
而研究员的循环每一轮都重发整个上下文。转义等于把这部分成本翻倍。

### 4. 结果条数和摘要长度自己先收着

超出上限的部分会被截断。按角色不同：

| 角色 | 单条 `snippet` 上限 | 建议条数 |
| --- | --- | --- |
| `role: data` | **4000** 字符 | 5–10 |
| `role: search` | **1200** 字符 | 5–10 |

（实测：同样发 2000 字符的摘要，`data` 存下 2000，`search` 存下 1200。）
一次返回的结果最多取前 50 条、每次调用最多 100 条成为证据，但真正的约束是上下文：
每步的检索次数有限（`max_searches_per_unit`），返回越长，能搜的轮数越少。

---

## 四、越精简越好，有一个实际后果

结果之外的字段（`score`、`highlight`、`metadata`、`request_id`、分页信息）不被读取，
但它们会稀释「结果占整个返回的比例」。这个比例有实际作用：

- 结果占返回文字 **≥ 80%** → 只产出逐条引用，干净。
- 低于 80% → 除了逐条引用，**整段返回会额外成为一条可引用证据**，标题是「数据源名: 查询词」。
  它不是错的（形状只识别了一部分时正是靠它兜底），但会在引用列表里多出一条没有链接的条目。

实测：精简的 `{"results":[...]}` 覆盖率 1.00；加上 `score`、`metadata` 和外层信封后降到 0.86。两者都还在 80% 以上，
但如果你的信封里有大段说明文字或日志，就会掉下去。

所以：**把结果之外的东西删到只剩必要的**。要带 `total`、`message` 这类字段可以，一两个短字段不影响。

---

## 五、日期：放进模型读得到的文字里

`published_at` 不会成为证据元数据（实测：证据上的 `published_at` 恒为 `None`），
所以计划里的时效要求（`not_before`）**不会**拿它做机械校验——这是刻意的设计：
不从工具返回里推断日期，由研究员自己判断并把不确定写成待解问题。

结论：日期要让模型看见。两种都行，建议都做：

1. 顶层放 `published_at`（`type: mcp` 供应商写法会渲染成 `Published: 2026-07-10` 给模型看）。
2. **同时把日期写进 `snippet` 本身**，例如「（2026-07-10 更新）承诺可用性 99.9%。」——
   这样无论走哪条路径、无论后续怎么裁剪，日期都跟着这段原文走。

---

## 六、配置要配套，这一步最容易出错

格式对了，配置不对一样引不出东西。你的场景是「只有摘要，没有能取原文的接口」，配置要这样写：

```yaml
sources:
  - name: kb-docs
    kind: mcp                # 把 MCP 工具原样暴露给研究员，保留它自己的参数 schema
    server: kb
    tool: kb_search          # 模型看到的名字
    mcp_tool: search_docs    # 服务器上真实的工具名（同名可省略）
    role: data               # 关键：摘要即证据，且不依赖别的设置
    origin: internal
    level: L1                # 来源等级由配置声明，返回内容说了不算
    publisher: platform-team # 判断来源是否独立用它
    description: 检索工程知识库，返回可直接引用的段落。

mcp_servers:
  kb:
    transport: http
    url: https://kb.corp.example/mcp
    headers:
      Authorization: Bearer ${KB_TOKEN}    # 只写引用，不写明文
    allowed_tools: [search_docs]           # 服务器级白名单
    timeout_seconds: 30

require_dual_source: false   # 只有一种来源时必须关，否则每步都报缺口且永远补不上
```

**为什么是 `role: data`**：实测三种组合——

| 声明 | 摘要可引用吗 |
| --- | --- |
| `role: data` | **可以，且无条件** |
| `role: search`，同时还有 `role: read` 的数据源 | **不可以**，摘要只算「发现过」 |
| `role: search`，没有任何 `role: read` 的数据源 | 可以（系统自动判定「结果即证据」） |

第二行就是之前那次 `NO_EVIDENCE` 的成因之一。把「摘要即原文片段」的工具填成 `search`，
只要环境里还有别的读取工具，它读到的东西就只是线索，不能引用。所以直接填 `data`，不依赖环境。

代价要知道：报告里这些引用是摘录而非原文。计划要求「只引用原文」（`require_original`）时，
该步骤会在局限里写明「没有可以打开原文的工具，引用的是检索摘录与记录」。这是如实披露，不是故障。

---

## 七、如果以后能取原文

有能按 URL 或文档 ID 返回全文的接口，就再加一个 `role: read` 的数据源。它的返回更宽松，
实测 Markdown、任意字段名的 JSON 信封、转义过的 JSON 字符串、只返回部分正文都能正确识别成「已读原文」：

```json
{ "title": "向量检索平台 SLA", "url": "https://wiki.corp.example/vector-sla", "content": "# 向量检索平台 SLA\n\n..." }
```

- 地址**取自调用参数**（`url` / `uri` / `link`，或任何取值是绝对 URL 的参数），不从返回里猜。
- 按文档 ID 打开、没有 URL 的内部文档也可以，引用标识为 `mcp://<数据源名>/<ID>`，读几次都是一条引用。
- 正文放在 `content` / `markdown` / `text` / `body` / `raw_content` 任一字段即可；没有任何可读字段时，
  整个 JSON 会被当成正文、标题退化成 URL（实测），所以别把正文只放在自定义字段名里。
- 少于 40 个字符的返回不算页面（`403 Forbidden` 这类不会被误当成原文）。

加了 read 工具之后，搜索工具就该回到 `role: search`：摘要回归线索，报告引用的是打开过的原文，质量更高。

---

## 八、自检

把你的真实返回贴进去，直接看会产出什么证据：

```python
# backend/.venv/bin/python
import json
from deepresearch.config import SourceSpec
from deepresearch.observations import NativeExecution, research_observations
from deepresearch.report_policy import citable

payload = {"results": [{"title": "SLA", "url": "https://wiki.corp.example/sla", "snippet": "可用性 99.9%。"}]}

src = SourceSpec(name="kb-docs", kind="mcp", server="kb", tool="kb_search", role="data", origin="internal", level="L1", publisher="platform-team")
messages = [
    {"type": "ai", "tool_calls": [{"id": "c1", "name": "kb_search", "args": {"query": "配额"}}]},
    {"type": "tool", "name": "kb_search", "tool_call_id": "c1", "status": "success",
     "content": json.dumps(payload, ensure_ascii=False)},
]
evidence, catalog = research_observations(NativeExecution("notes", "exec", messages), [src])
superseded = {item["raw_id"] for item in catalog if item.get("superseded")}
for item in evidence:
    if item.raw_id in superseded or not citable(item, {"kb-docs": "data"}):
        continue
    print(f"{item.raw_id[:9]}  {item.title[:30]:32s} {item.url}  {len(item.snippet)} 字符")
```

每条结果应该出现一行，带标题、链接和摘要长度。少了几行，就是被第三节的规则丢掉了。

跑通一次真实研究之后再核对两件事：

```sh
# 一次研究的全部记录，含每次 MCP 往返的实际参数与返回
backend/.venv/bin/python -m deepresearch.logbook export --data-dir <含 research.sqlite3 的目录> --run latest --out run.jsonl
# 审计底稿：不应出现 findings-pruned（转换时因为找不到可引用证据而裁掉结论）
backend/.venv/bin/python .agents/skills/deepresearch-engineering/scripts/audit_run.py --run latest
```

上线前还可以先探一次连通性与工具清单，不发起研究：

```sh
backend/.venv/bin/python -m deepresearch.doctor --config <research.yaml> --probe-mcp
```

---

## 九、检查清单

- [ ] 每条结果都有非空 `snippet`，它就是会被引用的原文片段
- [ ] 每条结果都有 `url`；同一个 URL 不出现两次（多段用 `#锚点`）
- [ ] 有 `title`，不指望系统从正文里凑
- [ ] `ensure_ascii=False`，不转义中文
- [ ] 删掉 `score`、`highlight`、`metadata`、分页等不被读取的字段
- [ ] 单条摘要控制在 4000 字符内（`role: search` 是 1200），一次返回 5–10 条
- [ ] 日期既放 `published_at`，也写进 `snippet` 文字里
- [ ] 数据源声明 `role: data`、`origin`、`level`、`publisher`；`require_dual_source: false`
- [ ] 失败用 MCP 错误状态返回，不用 200 加错误文案
- [ ] 凭据在配置里只写 `$ENV` 或 `secret:NAME` 引用

---

## 相关文档

- 数据源与 MCP 的全部配置字段：[RESEARCH_CONFIGURATION.md](RESEARCH_CONFIGURATION.md)
- 新增数据源、供应商的扩展方式：[EXTENDING.md](EXTENDING.md)
- 证据、引用与报告校验的规则：[ARCHITECTURE.md](ARCHITECTURE.md)
- 排查「读到了却引不出来」：`.agents/skills/deepresearch-engineering/references/debugging.md`
- 可直接起一个返回三种形状的 MCP 桩：`examples/deepresearch/mcp-stub/`
