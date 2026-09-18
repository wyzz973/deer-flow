# 模型配置说明

读者：要把 DeerFlow 和 DeepResearch 接到本地或内网模型的人或 Agent。
宿主配置的其他部分见 [DEERFLOW_CONFIGURATION.md](DEERFLOW_CONFIGURATION.md)，研究配置见 [RESEARCH_CONFIGURATION.md](RESEARCH_CONFIGURATION.md)。
可直接复制的离线模板：`examples/deepresearch/offline/host-config.fragment.yaml`。

## 1. 模型在哪里配置

**研究和宿主聊天用各自的模型列表，互不影响。**

| | 宿主聊天 | DeepResearch |
| --- | --- | --- |
| 在哪定义 | `config.yaml` 的 `models:` | 研究配置的 `models:`，或设置页“模型” |
| 谁引用 | `subagents.*.model`、`title.model_name`、`summarization.model_name` | 研究配置的 `default_model`、`rewrite_model`、`extraction_model`、`skills.*.model`、`compaction.model` |
| 默认模型 | 列表第一个 | `default_model` |
| 何时生效 | 重启网关 | 设置页保存后立刻对新建的研究生效 |

研究模型会被写进一份**私有**的引擎配置副本（同名时覆盖宿主模型），宿主的 `config.yaml` 不受影响。
所以宿主可以只留一个能构造的模型，研究用完全不同的模型。

研究模型的字段名更简单（`provider` 代替 `use`、`timeout_seconds` 代替 `request_timeout`），
见 [RESEARCH_CONFIGURATION.md 第 3 节](RESEARCH_CONFIGURATION.md#3-models研究模型)：

```yaml
models:
  - name: local-model
    provider: vllm                       # openai | deepseek | vllm | anthropic | custom
    model: Qwen3-32B                     # --served-model-name
    base_url: http://127.0.0.1:8000/v1
    api_key: $LOCAL_MODEL_API_KEY        # 只写引用，不写明文
    max_tokens: 8192
    context_window: 65536
    timeout_seconds: 900
    supports_thinking: true
    extra: {}                            # 额外构造参数，原样传给模型类
default_model: local-model
```

`api_key` 只接受 `$环境变量` 或 `secret:名字`（设置页保存，只写不读）；解析不到值时报 `MODEL_AUTH_REQUIRED`。
形如 `$变量名` 的值从环境变量或仓库根目录的 `.env` 读取。

## 2. 字段说明（宿主 `config.yaml` 的 `models:`）

研究模型的等价字段见上一节的表格；`extra` 里可以写这里的任何原生字段。

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 唯一名字，别处用它引用这个模型 |
| `display_name` | 否 | 界面上显示的名字 |
| `use` | 是 | 模型类，见第 3 节 |
| `model` | 是 | 服务端的模型名。vLLM 就是 `--served-model-name` 的值 |
| `base_url` | 本地模型必填 | OpenAI 兼容接口地址，通常以 `/v1` 结尾 |
| `api_key` | 是 | 写成 `$变量名`；服务端不校验时，在 `.env` 里给任意非空值 |
| `max_tokens` | 建议填 | **单次输出**上限，不是上下文长度 |
| `context_window` | 建议填 | **上下文总长度**（输入加输出），与服务端的最大长度一致 |
| `request_timeout` | 建议填 | 单次请求超时秒数（OpenAI 兼容类也接受 `timeout`） |
| `stream_chunk_timeout` | 建议填 | 流式输出时两个块之间最多等待的秒数，默认 240。本地慢模型调到 600 以上 |
| `max_retries` | 否 | 失败后的重试次数 |
| `temperature` | 否 | 采样温度 |
| `stream_usage` | 否 | 默认会在流式请求里要求返回 Token 用量。服务端不支持 `stream_options` 而报错时设为 `false`，代价是指标里没有 Token |
| `supports_thinking` | 否 | 模型是否支持思考模式开关 |
| `when_thinking_enabled` / `when_thinking_disabled` | 否 | 打开或关闭思考时额外发送的请求参数 |
| `supports_vision` | 否 | 是否支持图片输入 |

表里没有的字段也会原样传给模型类。字段名以 `config.example.yaml` 里同类模型的示例为准。

## 3. 本地模型模板

### 3.1 vLLM（推荐）

模型服务的启动示例。工具调用解析器和推理解析器要按模型选，以 vLLM 文档为准：

```bash
vllm serve Qwen/Qwen3-32B \
  --served-model-name Qwen/Qwen3-32B \
  --max-model-len 65536 \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --host 0.0.0.0 --port 8000
```

`config.yaml`：

```yaml
models:
  - name: local-model
    display_name: Local model (vLLM)
    use: deerflow.models.vllm_provider:VllmChatModel
    model: Qwen/Qwen3-32B
    base_url: http://127.0.0.1:8000/v1
    api_key: $LOCAL_MODEL_API_KEY
    request_timeout: 900.0
    stream_chunk_timeout: 900.0
    max_retries: 1
    max_tokens: 8192
    context_window: 65536
    supports_thinking: true
    supports_vision: false
    when_thinking_enabled:
      extra_body:
        chat_template_kwargs:
          enable_thinking: true
    when_thinking_disabled:
      extra_body:
        chat_template_kwargs:
          enable_thinking: false
```

`.env`：

```bash
LOCAL_MODEL_API_KEY=local-not-checked
```

说明：

- `VllmChatModel` 能在多轮工具调用中保留 vLLM 返回的推理内容。
- 两段 `when_thinking_*` 让 Qwen3 这类模型真正按开关关闭思考。研究角色总是关闭思考，省时间。
- `cumulative_stream_usage` 只在服务端每个流式块都返回累计用量时才设为 `true`，默认保持不填。

### 3.2 其他 OpenAI 兼容服务

适用于 Xinference、LM Studio、公司模型网关，以及 Ollama 的 `/v1` 接口：

```yaml
models:
  - name: local-model
    display_name: Local model (OpenAI compatible)
    use: langchain_openai:ChatOpenAI
    model: your-served-model-name
    base_url: http://127.0.0.1:11434/v1
    api_key: $LOCAL_MODEL_API_KEY
    request_timeout: 900.0
    stream_chunk_timeout: 900.0
    max_retries: 1
    max_tokens: 8192
    context_window: 32768
    supports_thinking: false
    supports_vision: false
```

Ollama 要注意两点：

- Ollama 默认的上下文很短，要在服务端调大（例如启动前设置环境变量 `OLLAMA_CONTEXT_LENGTH=32768`），并把 `context_window` 写成同一个值。
- `config.example.yaml` 里的原生写法 `langchain_ollama:ChatOllama` 需要额外的 `langchain-ollama` 包，本仓库环境默认没有安装。
  离线时优先用上面的 `/v1` 写法；如果一定要用原生写法，必须在联网时安装好。

## 4. DeepResearch 对模型的要求

1. **必须支持工具调用（function calling）。** 研究员靠工具查资料，规划和写作角色靠 `read_file` 读取 Skill 文件。
   不支持工具调用的模型无法完成研究。
   **只支持流式返回的服务也可以用**：研究的每一次直接调用（笔记整理、请求改写、模型探测）都走流式并聚合结果，
   只有在服务拒绝流式或流式没有内容时才退回普通请求。
2. **上下文至少 32k，建议 64k 以上。** 联网验收时，研究员单次输入最多约 4.4 万 Token。
3. **单次输出至少 4096，建议 8192。** 报告章节和整理后的数据都比较长。
4. **最好返回 Token 用量（usage）。** 不返回时，指标页的 Token 和费用为空，预算只能按估算扣减。
5. **能稳定用中文回答，能按要求输出 JSON 文本。** 不需要 JSON mode。

## 5. 研究的各个环节用哪个模型

| 环节 | 用哪个模型（从左往右，找到第一个就用） |
| --- | --- |
| 请求改写（对话 → 研究请求） | `rewrite_model` → `default_model` → 研究 `models` 第一个 |
| 规划、研究、写作（Agent 执行） | `skills.<名字>.model` → `default_model` → 旧版绑定的子 Agent 模型 → 研究 `models` 第一个 |
| 输出整理（把回答整理成数据） | `extraction_model` → 角色的模型（同上一行） |
| 上下文压缩（研究角色） | `compaction.model` → 角色自己的模型 |
| 单次输出上限 | 取模型的 `max_tokens` 和研究配置 `max_output_tokens` 中较小的值 |
| 思考模式 | 研究角色和输出整理总是关闭思考（`supports_thinking: true` 的模型会收到关闭开关） |
| 宿主聊天的压缩与标题 | `summarization.model_name`、`title.model_name`，与研究无关 |

建议所有研究环节先用同一个模型。等研究能跑通了，再考虑给输出整理（`extraction_model`）换一个更稳的模型。

## 6. 模型较弱时怎么调

| 现象 | 调整 | 在哪里改 |
| --- | --- | --- |
| 整理失败、`RESULT_CONTRACT` | `output_retries` 调到 3 或 4；`extraction_model` 用最稳的模型 | 研究配置 |
| 报告章节反复修复、质量差 | `max_report_sections` 调到 4 或 5；`max_synthesis_repairs` 保持 1 | 研究配置 |
| 超时、`NATIVE_AGENT_TIMEOUT` | 研究配置的 `skills.*.timeout_seconds` 调到 1800；研究模型的 `timeout_seconds` 调到 900 | 研究配置 |
| 显存不够、请求排队 | `max_concurrency` 调到 1；`subagent_runtime.max_running` 不能小于它 | 研究配置、`config.yaml` |
| 上下文超长 | 研究配置的 `compaction.trigger_fraction` 调到 0.4–0.5；模型要填 `context_window`；必要时调低 `sandbox.read_file_output_max_chars` | 研究配置（沙箱项在 `config.yaml`） |
| 一次研究太久 | `budget_ceiling.max_units` 调到 3 或 4；`max_iterations` 调到 0 或 1 | 研究配置 |
| 计划里要求了没配置的来源，研究单元报 `TOOL_DENIED` | 只有一类来源时设 `require_dual_source: false`，系统会自动去掉没有工具的来源要求 | 研究配置 |
| 进度说明或报告不是中文 | 先确认 `researcher_output` 提示词里的语言要求没被改掉；仍不稳定时换中文能力更好的模型 | 设置页“提示词”、研究配置 |

## 7. 验证模型配置

先确认配置能加载、研究角色齐全，再对模型发两次真实请求（一次普通回复，一次工具调用）：

```bash
cd backend
uv run --no-sync python -m deepresearch.doctor --config ../deepresearch.local.yaml --probe-model local-model
```

设置页“模型 → 测试连接”做的是同一件事，并且用的是表单里当前（未保存）的配置。

输出里 `model_probe` 各字段的含义：

| 字段 | 通过的标准 |
| --- | --- |
| `ok` | `true`。为 `false` 时命令以退出码 1 结束 |
| `error` | 不出现。出现 `APIConnectionError` 表示连不上模型服务，检查地址和端口 |
| `plain_reply.text` | 有正常文字，没有大段 `<think>` 内容 |
| `tool_call.tools` | 包含 `lookup`。为空表示模型或服务端没有开启工具调用 |
| `usage.total_tokens` | 是数字。为 `null` 表示服务端没返回用量 |
| `warnings` | 空列表。有内容时逐条处理 |
| `plain_reply.seconds`、`tool_call.seconds` | 响应秒数。短提示词就要几十秒的话，研究会非常慢 |

联网环境用 DeepSeek 实测的输出供对照：`ok: true`，普通回复 0.4 秒，工具调用 0.5 秒，`warnings` 为空。
