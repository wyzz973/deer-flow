# 扩展研究角度与数据源

字段说明见 [RESEARCH_CONFIGURATION.md](RESEARCH_CONFIGURATION.md)。以下所有内容都可以在设置页
（`/workspace/deepresearch/settings`，管理员）里改，保存后对之后新建的研究生效。

## 新增研究角度

在研究配置的 `skills` 里加一个角色，不需要在 DeerFlow 里建 Custom Agent：

```yaml
skills:
  product-benchmark:
    name: 产品对标研究员
    description: 产品能力、场景与落地约束对标   # 规划模型据此决定把哪些研究单元分给它
    path: skills/custom/product-benchmark/SKILL.md   # 或直接写 methodology 正文
    model: null            # null 用 default_model
    tools: null            # null = 全部数据源 + 默认引擎工具；列表 = 白名单
    max_turns: null        # 引擎图递归步数，含中间件节点，不等于对话轮数
    timeout_seconds: 600
```

- 方法论只写方法、关注范围和证据纪律。Skill 文件的 YAML 头信息会被自动去掉，不会发给模型。
- 不要要求数据源返回统一 JSON，也不要让研究员提前生成全局 `E001` 编号：跨单元编号在研究结束后由引用目录统一分配。
  研究时引用工具回执或 call ID，并写清结论与未解决问题。
- `agent:` 是旧版写法（绑定 DeerFlow 子 Agent 并继承它的提示词/工具/模型），仍可读，新配置不要用。

## 新增数据源

一个数据源就是研究员看到的一个工具。优先用**供应商链**：模型看到的参数固定，背后按顺序尝试多个供应商，
失败自动切换（限流、额度、鉴权、超时、服务异常会让该供应商冷却）。

```yaml
sources:
  - name: internal-docs
    tool: knowledge_search      # 模型看到的名字，自己取
    role: data                  # search=只用于发现；read=打开原文；data=返回可引用记录
    origin: internal
    level: L1
    providers:
      - id: kb
        type: ragflow           # 或 mcp / http / lightrag …
        base_url: http://10.0.0.8:9380
        api_key: secret:ragflow-key
        options: {dataset_ids: [kb-1]}
```

- 自定义 HTTP 接口用 `type: http`，模板变量 `{query}`、`{max_results}`、`{url}`、`{time_range}`。
- MCP 工具用 `type: mcp`（`server` + `tool`，参数按工具 schema 自动对应），服务写在研究自己的 `mcp_servers` 里。
- 想保留某个 MCP 工具自己的参数 schema，就把数据源写成 `kind: mcp`（不填 `providers`）。
- 返回格式不固定没关系：系统会从 JSON、Markdown 链接、`Title/URL` 文本块或 HTML 中识别标题、链接与正文。
- 凭据只写 `$环境变量` 或 `secret:名字`，绝不放进模型可见的参数里。设置页可以单独测试每个供应商并查看健康状态。
- 旧字段 `query_arg`、`fixed_args`、`results_path`、`fields`、`response_mode` 已无执行效果，可以删掉。

## 新增供应商类型

`providers.py` 里每个预设是一个函数：接收 `Request(role, query, url, max_results, time_range)`，
返回 `Outcome(records/document/raw)`，失败时抛 `ProviderError(kind, ...)`（kind 决定是冷却该供应商还是只换下一个）。
新增预设时同时更新 `PRESETS` 元数据（设置页目录靠它显示类型、是否需要密钥、默认地址），并在
`tests/deepresearch/test_source_providers.py` 加一条用假 HTTP 服务的测试。

## 领域契约与自定义 Runner

`AgentRunner` 仍提供 `rewrite`/`plan`/`research`/`synthesize`，供显式的领域扩展使用。
自定义实现必须保持原生授权与请求级凭据边界、预算、真实调用可追溯性、取消清理，以及失败不回退演示的约定。

优先复用 `native.py` / `structured.py` / `observations.py` / `channels.py`。只有公司宿主接口不同才适配宿主 API；
不要另建 Agent loop、身份系统或 Skill loader。未实现的日期/语义证明必须作为限制公开。
