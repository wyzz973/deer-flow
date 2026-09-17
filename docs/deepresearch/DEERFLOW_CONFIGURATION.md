# DeerFlow 宿主配置说明（离线部署 DeepResearch）

读者：要在不联网的机器上把 DeerFlow 和 DeepResearch 跑起来的人或 Agent。
模型字段见 [MODEL_CONFIGURATION.md](MODEL_CONFIGURATION.md)，研究配置字段见 [RESEARCH_CONFIGURATION.md](RESEARCH_CONFIGURATION.md)。
DeerFlow 全部配置项的权威说明在 `config.example.yaml` 的注释和 `backend/docs/CONFIGURATION.md`。

## 1. 配置文件一览

以下文件都在仓库根目录，都**不提交**到 git（已在 `.gitignore` 里）：

| 文件 | 作用 | 从哪里来 |
| --- | --- | --- |
| `config.yaml` | 主配置：模型、工具、沙箱、Agent、插件等 | 复制 `config.example.yaml`，再合并离线片段 |
| `.env` | 密钥，以及 `config.yaml` 里 `$变量` 的值 | 手写 |
| `extensions_config.json` | MCP 服务和 Skill 开关（可选，没有这个文件也能启动） | 手写，见第 4 节 |
| `deepresearch.local.yaml` | 研究配置 | 复制 `examples/deepresearch/offline/research.yaml` |
| `skills/custom/` | 研究方法论（Skill） | 复制 `examples/deepresearch/skills/` 下的目录 |

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

| 段 | 离线怎么配 | 说明 |
| --- | --- | --- |
| `models` | 本地模型 | 见 MODEL_CONFIGURATION.md |
| `tools` | 只保留内网知识库和沙箱文件工具 | 不要配置 `web_search`、`web_fetch`、`image_search` 这类依赖公网的工具。`read_file` 不能删：规划和写作角色靠它读取 Skill 文件 |
| `tool_groups` | 保持示例 | 每个工具的 `group` 必须在这里声明过；示例里已有 `knowledge`、`file:read`、`file:write` |
| `sandbox` | `deerflow.sandbox.local:LocalSandboxProvider` | 不需要 Docker。要用容器沙箱（`AioSandboxProvider`）的话，必须在联网时先拉好镜像 |
| `skills` | 保持示例 | `path` 不写时使用仓库根目录的 `skills/`；`container_path` 保持 `/mnt/skills` |
| `subagents.custom_agents` | 四个研究角色 | 名字必须和研究配置 `skills.*.agent` 一致，见下表 |
| `subagents.timeout_seconds` | 1800 | 只对内置子 Agent 生效；研究角色用它们各自的 `timeout_seconds` |
| `subagent_runtime.max_running` | 不小于研究配置的 `max_concurrency` | 子 Agent 同时运行的上限，超出的会排队 |
| `summarization` | `trigger` 约为模型 `context_window` 的 40% | 上下文快满时自动压缩历史 |
| `title.model_name` | `null` | 不为标题额外调用模型 |
| `suggestions.enabled` | `false` | 少一次模型调用 |
| `memory.enabled` | `false` | 记忆会额外调用模型 |
| `token_budget` | 保持示例（`enabled: false`） | 这是主对话 Agent 的预算，不是研究预算。研究预算在研究配置的 `budget_ceiling` |
| `database` | 保持示例（sqlite） | DeerFlow 自己的数据库，和研究数据库不是同一个 |
| `plugins` | 加载 `deepresearch.extension:install` | `config_path` 相对仓库根目录；没有这一段，研究接口就不存在 |

研究角色（`subagents.custom_agents` 下）的字段：

| 字段 | 说明 |
| --- | --- |
| `description` | 角色说明 |
| `system_prompt` | 角色的系统提示词。研究任务自己的指令会追加在后面 |
| `skills` | 这个角色可以激活的 Skill，例如 `[deepresearch]` |
| `tools` | 工具白名单。`null` 表示继承全部宿主工具；规划和写作角色只给 `[read_file]` |
| `model` | 模型名，或 `inherit`（使用默认模型） |
| `max_turns` | 图递归步数上限（包含中间件节点，不等于模型对话轮数） |
| `timeout_seconds` | 这个角色单次执行的超时时间；实际生效的是它和研究配置 `skills.*.timeout_seconds` 中较小的值 |

研究员角色的 `tools` 必须覆盖研究配置里 `sources[].tool` 用到的工具，否则研究单元报 `TOOL_DENIED`。

## 4. `extensions_config.json`（内网 MCP 服务）

只有使用 MCP 服务作为研究来源时才需要。最小内容：

```json
{
  "mcpServers": {
    "company-kb": {
      "enabled": true,
      "type": "http",
      "url": "http://10.0.0.8:9000/mcp",
      "headers": {"Authorization": "Bearer $COMPANY_KB_TOKEN"},
      "description": "内网知识库检索"
    }
  },
  "skills": {}
}
```

规则：

- MCP 工具在宿主里的名字是 `服务名_工具原名`。例如服务 `company-kb` 的工具 `search`，名字就是 `company-kb_search`。
- 研究配置里写成 `kind: mcp`、`server: company-kb`、`tool: company-kb_search`。
- `$变量` 同样从 `.env` 读取。
- 改完要重启网关。
- 不要直接复制 `extensions_config.example.json`：其中的 `mcpInterceptors` 指向示例模块，本地并不存在。
- 需要按请求传递凭据时，参考 `README.deepresearch.md` 的“凭据”一节（`headers_from_context`）。

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
