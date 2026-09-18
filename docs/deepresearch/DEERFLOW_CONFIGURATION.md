# DeerFlow 宿主配置说明（离线部署 DeepResearch）

读者：要在不联网的机器上把 DeerFlow 和 DeepResearch 跑起来的人或 Agent。
模型字段见 [MODEL_CONFIGURATION.md](MODEL_CONFIGURATION.md)，研究配置字段见 [RESEARCH_CONFIGURATION.md](RESEARCH_CONFIGURATION.md)。
DeerFlow 全部配置项的权威说明在 `config.example.yaml` 的注释和 `backend/docs/CONFIGURATION.md`。

**先明确分工**：研究用什么模型、什么数据源、什么 MCP 服务、什么角色和提示词，全部写在研究配置
（`deepresearch.local.yaml`）或设置页里，**不写在 `config.yaml`**。宿主配置只需要提供执行环境
（沙箱、子 Agent 运行时、数据库）和一行 `plugins` 注册。宿主的 `models`、`tools`、
`subagents.custom_agents`、`extensions_config.json` 都不会影响研究。

## 1. 配置文件一览

以下文件都在仓库根目录，都**不提交**到 git（已在 `.gitignore` 里）：

| 文件 | 作用 | 从哪里来 |
| --- | --- | --- |
| `config.yaml` | 主配置：模型、工具、沙箱、Agent、插件等 | 复制 `config.example.yaml`，再合并离线片段 |
| `.env` | 密钥，以及 `config.yaml` 里 `$变量` 的值 | 手写 |
| `extensions_config.json` | MCP 服务和 Skill 开关（可选，没有这个文件也能启动） | 手写，见第 4 节 |
| `deepresearch.local.yaml` | 研究配置：研究模型、数据源、MCP、角色、提示词 | 复制 `examples/deepresearch/offline/research.yaml` |
| `skills/custom/` | 研究方法论（Skill），也可以直接写在研究配置的 `methodology` 里 | 复制 `examples/deepresearch/skills/` 下的目录 |

`extensions_config.json` 与研究无关：研究自己的 MCP 服务写在研究配置的 `mcp_servers` 里。

改变文件位置的环境变量：

| 变量 | 作用 |
| --- | --- |
| `DEER_FLOW_CONFIG_PATH` | 使用指定路径的 `config.yaml` |
| `DEER_FLOW_EXTENSIONS_CONFIG_PATH` | 使用指定路径的 `extensions_config.json`（文件必须存在） |
| `DEER_FLOW_HOME` | 运行数据目录（线程、上传文件、DeerFlow 数据库）。建议显式设置，例如 `backend/.deer-flow` |

研究自己的数据（研究数据库、检查点、日志）不在 `DEER_FLOW_HOME`，而在研究配置的 `data_dir`。

## 2. 从零配置（离线）

每一步都在仓库根目录执行。

**第 1 步：生成 `config.yaml`。** 用示例配置加离线片段合并生成（`config.yaml` 已存在时脚本会报错，不会覆盖）：

```bash
cd backend
uv run --no-sync python - <<'EOF'
import yaml
base = yaml.safe_load(open("../config.example.yaml", encoding="utf-8"))
fragment = yaml.safe_load(open("../examples/deepresearch/offline/host-config.fragment.yaml", encoding="utf-8"))
base.update(fragment)  # 同名段整段替换
with open("../config.yaml", "x", encoding="utf-8") as out:
    yaml.safe_dump(base, out, allow_unicode=True, sort_keys=False)
print("config.yaml written")
EOF
cd ..
```

生成的 `config.yaml` 没有注释，每个字段的含义去 `config.example.yaml` 查。
然后打开 `config.yaml`，按实际情况修改：

- `models`：模型地址、模型名、`max_tokens`、`context_window`（见 MODEL_CONFIGURATION.md）。
- `tools` 里 `knowledge_search` 的 `base_url`：改成内网知识库的地址。

**第 2 步：写 `.env`。** 离线片段用到两个变量；每个 `$变量` 都要有值，否则启动报错：

```bash
LOCAL_MODEL_API_KEY=local-not-checked
RAGFLOW_API_KEY=你的RAGFlow密钥
```

**第 3 步：研究配置。**

```bash
cp examples/deepresearch/offline/research.yaml deepresearch.local.yaml
```

模型名不是 `local-model` 时，把文件里所有 `local-model` 替换成实际的名字。

**第 4 步：安装研究 Skill。**

```bash
mkdir -p skills/custom
cp -R examples/deepresearch/skills/deepresearch examples/deepresearch/skills/industry-trend \
  examples/deepresearch/skills/technical-route examples/deepresearch/skills/report-synthesis skills/custom/
```

**第 5 步：检查。**

```bash
cd backend
uv run --no-sync python -m deepresearch.doctor --config ../deepresearch.local.yaml --probe-model local-model
cd ..
```

期望结果：`mode` 是 `deerflow`，`agents` 全部为 `true`，`model_probe.ok` 为 `true`。

**第 6 步：启动网关。**

```bash
cd backend
DEER_FLOW_HOME=.deer-flow DEER_FLOW_AUTH_DISABLED=1 DEER_FLOW_ENV=development PYTHONPATH=. \
  uv run --no-sync uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001
```

**第 7 步：启动前端（另开一个终端）。**

```bash
cd frontend
DEER_FLOW_AUTH_DISABLED=1 DEER_FLOW_ENV=development \
  python3 ../scripts/pnpm.py exec next dev --hostname 127.0.0.1 --port 3000
```

**第 8 步：确认。**

```bash
curl http://127.0.0.1:8001/api/deepresearch/capabilities
```

返回里要有 `"mode": "deerflow"` 和 `"ready": true`。然后浏览器打开 `http://127.0.0.1:3000/workspace/deepresearch`。

本机端口 3000 被占用时，前端换一个端口，并且不要结束占用 3000 的进程。

## 3. `config.yaml` 里和研究有关的段

研究不从宿主继承模型和工具，所以这里只剩执行环境：

| 段 | 离线怎么配 | 说明 |
| --- | --- | --- |
| `plugins` | 加载 `deepresearch.extension:install` | `config_path` 相对仓库根目录；没有这一段，研究接口就不存在。这是研究唯一必须的宿主配置 |
| `sandbox` | `deerflow.sandbox.local:LocalSandboxProvider` | 不需要 Docker。要用容器沙箱（`AioSandboxProvider`）的话，必须在联网时先拉好镜像 |
| `subagent_runtime.max_running` | 不小于研究配置的 `max_concurrency` | 子 Agent 同时运行的上限，超出的会排队 |
| `database` | 保持示例（sqlite） | DeerFlow 自己的数据库，和研究数据库不是同一个 |
| `skills` | 保持示例 | `container_path` 保持 `/mnt/skills`；只影响宿主自己的 Skill 索引 |
| `models` | 至少留一个能构造的模型 | 宿主聊天用；研究用研究配置里的 `models`。两边可以完全不同 |
| `title.model_name` / `suggestions.enabled` / `memory.enabled` | `null` / `false` / `false` | 少几次与研究无关的模型调用 |
| `token_budget` | 保持示例（`enabled: false`） | 主对话 Agent 的预算，不是研究预算（研究预算在研究配置的 `budget_ceiling`） |
| `summarization` | 保持示例 | 主对话的上下文压缩。**研究不使用这里的阈值**：研究角色的压缩由研究配置的 `compaction` 决定 |

不再需要为研究配置的段（旧版本要求过，现在保留只是为了兼容旧文件）：

- `subagents.custom_agents` 里的研究角色：角色现在完全由研究配置的 `skills` 定义。
- `tools` 里的 `web_search`、`web_fetch`、`knowledge_search`：研究的数据源由研究配置的 `sources`
  与其供应商提供；研究员还能使用的引擎工具由研究配置的 `engine_tools` 决定（默认只有 `read_file`）。
- `extensions_config.json` 里的 MCP 服务：研究自己的 MCP 服务写在研究配置的 `mcp_servers`。

## 4. 研究自己的 MCP 服务（内网知识库）

写在研究配置里，不需要 `extensions_config.json`：

```yaml
mcp_servers:
  company-kb:
    transport: http
    url: http://10.0.0.8:9000/mcp
    headers:
      Authorization: secret:company-kb-token   # 或 $COMPANY_KB_TOKEN
    timeout_seconds: 60

sources:
  - name: internal-knowledge
    tool: knowledge_search      # 模型看到的名字，自己取
    role: data
    origin: internal
    level: L1
    providers:
      - id: kb
        type: mcp
        server: company-kb
        tool: search            # MCP 工具原名
```

规则：

- 工具名由研究配置决定，不再是 `服务名_工具原名`。
- 返回格式不固定也没关系：系统会从 JSON、Markdown 链接、`Title/URL` 文本块或 HTML 中识别标题、链接与正文。
- 也可以用 `kind: mcp` 的数据源把某个 MCP 工具连同它自己的参数 schema 原样暴露给研究员。
- `$变量` 从 `.env` 读取；`secret:名字` 在设置页“密钥与历史”里保存（只写不读）。
- 需要按请求传递凭据时，见 `README.deepresearch.md` 的“凭据”一节。
- 设置页“MCP 服务”可以直接“连接并列出工具”，确认工具名与参数。

## 5. `.env`

- 放 `config.yaml` 和 `extensions_config.json` 里用到的每个 `$变量`。
- 网关启动时自动读取仓库根目录的 `.env`。
- 前端进程不读这个文件，前端需要的变量直接写在启动命令前面（第 6 节）。
- 不要把 `.env` 提交到 git，也不要在日志或文档里打印它的内容。

## 6. 前端环境变量

| 变量 | 取值 | 说明 |
| --- | --- | --- |
| `DEER_FLOW_AUTH_DISABLED` | `1` | 本地开发关闭登录。网关和前端两个进程都要设置，否则页面会跳到 `/setup`。不要在共享或生产环境使用 |
| `DEER_FLOW_ENV` | `development` | 值为 `production` 时，关闭登录的设置不生效 |
| `DEER_FLOW_INTERNAL_GATEWAY_BASE_URL` | 默认 `http://127.0.0.1:8001` | 前端开发服务器把 `/api/*` 转发到这个网关地址 |
| `NEXT_PUBLIC_BACKEND_BASE_URL` | 不设置 | 设置后浏览器会直接访问这个地址，后端要允许前端地址跨域：真实网关设置 `GATEWAY_CORS_ORIGINS=前端地址`，演示后端设置 `DEEPRESEARCH_DEMO_FRONTEND_PORT=前端端口`。只有演示模式和浏览器测试需要设置 |
| `SKIP_ENV_VALIDATION` | `1` | 跳过前端环境变量校验，演示模式使用 |

## 7. 验证过的内容

2026-09-17，用上面的离线模板合并出的 `config.yaml` 和研究配置（模型地址指向一个没有启动的服务）：

- 网关正常启动，`/api/deepresearch/capabilities` 返回 `mode: deerflow`、`ready: true`。
- doctor 找到全部四个研究角色；模型探测返回 `ok: false`，错误是 `APIConnectionError: Connection error.`，退出码为 1。
- 新建研究在规划阶段失败，错误码 `NATIVE_AGENT_FAILED`，Trace 里模型节点的 `error_type` 是 `APIConnectionError`。

还没有验证：接上真实的本地模型和内网知识库，完整跑完一次研究。
