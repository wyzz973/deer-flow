# 扩展研究角度与工具

## 新增研究角度

复用公司现有 Custom Agent 和原生 Skill 注册机制，再在研究 registry 增加对应条目：

```yaml
skills:
  product-benchmark:
    agent: product-researcher
    path: skills/custom/product-benchmark/SKILL.md
    description: 产品能力、场景与落地约束对标
    max_turns: 20
    timeout_seconds: 300
```

Agent 的模型、tools、skills、disallowed_tools 和运行参数沿用 DeerFlow。
将方法论注册到宿主 Skill 系统，供原生发现/激活；registry path 用于显式方法说明和配置指纹，不会自动安装脚本或提升工具权限。

研究 Skill 描述方法、关注范围和证据纪律即可。不要要求每个 MCP 返回统一 JSON，也不要要求研究 Agent 提前生成全局 E001/raw_id。研究时引用原生 receipts/工具 call ID，明确结论和未解决问题；跨单元编号由研究结束后的引用目录统一。

## 新增 MCP 来源

先在普通 DeerFlow 中配置并测试 MCP。研究只选择已经可用的工具：

```yaml
sources:
  - name: internal-docs
    origin: internal
    server: company-docs
    tool: company-docs_search
  - name: external-web
    origin: external
    server: company-web
    tool: company-web_search
```

工具名必须精确匹配宿主缓存，服务身份来自宿主 source metadata。复杂参数、返回格式、OAuth、HTTP/SSE/stdio、超时和连接池沿用原生工具链。不要新增 make_search 包装器或按业务返回写适配器。

旧 query_arg/fixed_args/results_path/fields/response_mode 已无执行效果；从公司本地配置删去即可。固定业务筛选可以放在方法论或宿主工具描述中，但不能把凭据放入模型可见参数。

## 领域契约与自定义 Runner

AgentRunner 仍提供 plan/research/synthesize 三个方法，供显式的领域扩展使用。
自定义实现必须保持原生授权和请求级凭据边界、预算、真实调用可追溯性、取消清理以及失败不回退演示的约定。

优先复用 native.py/structured.py/observations.py。只有公司宿主接口不同才适配宿主 API；不要另建 Agent loop、MCP client、身份系统或 Skill loader。未实现的日期/语义证明必须作为限制公开。
