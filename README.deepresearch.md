# DeepResearch：DeerFlow 原生研究模块

在 DeerFlow 工作区侧栏打开 **DeepResearch**，进入 `/workspace/deepresearch`。
研究计划可以编辑、确认或拒绝；确认后由原生子 Agent 执行，最终输出带可追溯引用的报告。

## 设计原则

- LangGraph 管计划审批、状态、依赖调度与补研。
- DeerFlow `SubagentExecutor` 管 Agent、工具授权、Skill 激活、沙箱、上下文压缩、运行保护和取消。
- MCP 的参数和结果由原生工具链处理，**不要求任何统一搜索返回格式**。模型直接阅读原始 ToolMessage。
- 研究完成后，从原生 ToolMessage 和 receipts 建立报告引用；调用记录不等于语义支持证明。
- 普通 Chat Completions 回复通过独立、有限重试的输出整理进入研究契约，不要求 JSON mode。
- 本地 Trace 与轮转日志支持调查，不依赖 LangSmith 或其他遥测服务。

详细说明：[设计与原生能力复用](docs/deepresearch/NATIVE_RUNTIME.md) · [逐步评审记录](docs/deepresearch/REUSE_AUDIT.md) · [API](docs/deepresearch/API.md) · [公司 Agent 交接](docs/deepresearch/HANDOFF.md)

## 接入已有 DeerFlow

1. 合并 `examples/deepresearch/host-config.fragment.yaml` 到本地 `config.yaml`，保留原模型、其他 Agent 与插件配置。
2. 复制 `deepresearch.example.yaml` 为 `deepresearch.local.yaml`，设置 `runner: deerflow`。
3. 将 Skill registry 的 `agent` 对应到现有 Custom Agent，`path` 指向实际方法论文件。
4. 在 `sources` 填写已在 DeerFlow 中配置成功的 MCP 服务名、**精确工具名**、来源分类；不再配置返回字段映射。
5. MCP 连接、stdio/HTTP/SSE、认证及凭据策略沿用宿主；研究模块不建立另一套客户端或推断工具名前缀。
6. 执行 `uv sync` 安装宿主依赖（本分支已声明 DOCX 依赖）。检查后重启 Gateway。
7. 打开工作区侧栏 DeepResearch，新建研究。旧运行配置指纹不兼容时应新建任务。

原生 Agent 和激活 Skill 的工具白名单仍生效。`native_tools: null` 表示使用宿主候选工具；可以额外配置列表收紧权限。研究模块不会绕过原生授权，也不会自动开放嵌套 Agent 或非交互任务中的澄清工具。

默认要求 internal/external 两类来源；可以设置 `require_dual_source: false` 并在计划中选择实际来源类别。模型负责调用顺序和并行机会，没有强制的“两次预搜索”。

配置检查（不访问模型/MCP）：

```sh
PYTHONPATH=backend python -m deepresearch.doctor --config deepresearch.local.yaml
```

## 凭据

优先使用已经可用的 DeerFlow MCP 认证。需要请求级凭据时，沿用宿主的 `headers_from_context`，通过专用请求头或本地环境变量传递：

```yaml
local_secret_env:
  research_cookie: DEEPRESEARCH_MCP_COOKIE
request_secret_headers:
  X-Research-Cookie: research_cookie
```

这里存的是环境变量名/映射，不是真实凭据。凭据只进入原生 executor 的 runtime context；不要把宿主登录 Cookie 自动转发到另一个业务系统。

## 本地 Trace 与日志

工作台展开“本地 Trace”，查看 workflow/node/agent/model/tool/conversion 的父子关联、输入输出、耗时、错误和调用记录；支持分页及完整 JSONL 导出。

研究数据目录默认 `.deerflow/deepresearch`：

- `research.sqlite3`：研究数据、事件和本地 Trace。
- `checkpoints.sqlite3`：LangGraph 检查点。
- `research.log`：不含原始 prompt/header 的运行元数据，5 MiB 轮转、3 个备份。

`trace_capture_content: false` 可关闭明细采集；`trace_max_chars` 限制单条载荷长度。已知凭据与敏感字段被脱敏，不采集隐藏推理块。数据仍可能包含业务内容，必须按私有应用数据保护；当前 Trace 无自动 TTL。

## 演示与验收

演示使用合成数据，不调用业务模型/MCP：

```sh
python -m pip install -r backend/deepresearch/requirements.txt
python -m uvicorn deepresearch.demo:app --app-dir backend --host 127.0.0.1 --port 8022
python scripts/pnpm.py dev
```

打开 `/deepresearch-demo`。它与真实工作区共用研究组件，但后端仅接受回环地址。

从 `backend/` 执行回归：

```sh
uv run --no-sync --with python-docx python -m pytest ../tests/deepresearch tests/test_subagent_executor.py -q
DEEPRESEARCH_E2E_FRONTEND_PORT=3100 uv run --no-sync --with python-docx python ../scripts/pnpm.py exec playwright test -c playwright.deepresearch.config.ts
```

浏览器测试使用独立演示数据库和宿主已有的开发免登录模式，覆盖演示页与真实工作区页面、侧栏、移动端、计划编辑/确认、报告/引用、Trace 和下载。权限隔离由后端测试覆盖，不能把开发免登录测试当作真实企业 SSO 验收。

## 当前边界

单进程 SQLite；不支持多实例负载均衡。成功的完整 Agent 执行会缓存，格式修复不重复研究；中途失败的子 Agent 会重启，不自动缓存/重放任意 MCP 调用。工具可能有副作用，不能宣称 exactly-once。

原生子 Agent 的能力不等于全部 Lead 能力。Lead 记忆写入、聊天历史、上传管理没有被暗中移植。原始文档日期、发布方独立性和语义支持由研究内容评审，代码仅确认执行来源和结构覆盖。真实业务验收清单见交接文档。
