# 本地 Skill、Agent 与 MCP 扩展

## 一、优先沿用已有方法论

你原有的行业趋势和技术路线 Skill 不需要改名。将 `deepresearch.local.yaml` 的 `skills.<id>.path` 指向其实际文件；`agent` 对应原 `subagents.custom_agents` 的 key。方法论可以变，**运行时契约不可变**。

新适配器通过 DeerFlow SDK 创建独立执行图，读取现有 Custom Agent 的 system_prompt/model/tools/skills/max_turns/timeout，再注入 registry 指定的 Skill 文本。它不是让 Lead 再调用 `task()` 二次选择角色，也不使用宿主的 Skill 自动发现来改变本次路由。

Skill 当前以方法论文本加载；不自动执行目录中的 scripts，也不会默默授权 Bash、任意网络请求或嵌套 Agent。普通 DeerFlow 聊天和工具链不受影响。需要原生沙箱/文件/脚本能力时，通过下文 AgentRunner 接口接入宿主执行器，保留你的既有中间件和工具策略，不直接把工具函数裸挂到模型。

## 二、给现有子 Skill 的最小调整指令

> 保留原研究方法论与领域知识，删除固定行业、固定网站、固定章节和最终 Markdown/数字引用要求。任务目标、可用来源及预算由运行时注入。每条发现用 claim/raw_evidence_refs/confidence/high_risk；raw_evidence_refs 必须是工具实际返回的 raw_id。证据不足列入 open_questions，不编造来源。内部、外部检索互补，补研只处理指定缺口。研究 Agent 不负责报告综合、全局 Evidence ID 或最终引用编号。

现有子 Skill 若坚持输出旧字段，应在自定义 runner 中做显式 adapter，而不是在任意 JSON 中“猜测”字段。公共 ResearchResult 是后端验证的边界。真实 runner 内部让模型输出 ResearchAnalysis（findings/open_questions/confidence），RawEvidence 元数据由 MCP 结果注入，禁止模型自行重写。

## 三、新增一个产品对标 Skill

参考 `examples/deepresearch/skills/product-benchmark/SKILL.md`。把它复制到你维护的方法论目录，在配置中新增：

```yaml
skills:
  product-benchmark:
    agent: product-researcher
    path: skills/custom/product-benchmark/SKILL.md
    description: 产品能力、场景与落地约束对标
    max_turns: 12
    timeout_seconds: 180
```

同时合并新的 Custom Agent：

```yaml
subagents:
  custom_agents:
    product-researcher:
      description: Research product capabilities against user requirements.
      system_prompt: Work only on the assigned objective and return the runtime JSON contract.
      skills: [product-benchmark]
      tools: [your-internal-mcp_search, your-external-mcp_search]
      model: inherit
      max_turns: 12
      timeout_seconds: 180
```

Skill ID 是配置项而不是硬编码 Enum。Planner 的 available_skills 和前端选择器自动更新。禁止用户在 HTTP 请求中提交任意磁盘路径、Python 类或新工具名。

如果 Skill frontmatter 声明 `allowed-tools`，它会进一步收紧搜索工具权限：与 Custom Agent tools、disallowed_tools、研究 sources 取交集。请使用**真实暴露工具名**，而不是内部 wrapper 的 `research_<source>` 名。空列表表示没有检索权限，会明确失败，不会绕过白名单。

## 四、适配自己的 MCP 返回值

默认要求结构化 JSON，例如：

```json
{"results":[{"title":"某文档","url":"https://docs.example.org/a","document_id":"internal-123","snippet":"可引用的内容摘录","published_at":"2026-08-01T00:00:00Z"}]}
```

支持 MCP structuredContent 或 content 中的 JSON 文本。内部无公开 URL 时须有 document_id/source_uri；外部必须有合法 HTTP(S) URL。空标题、空摘录、无法定位或非法 URL 的命中不会进入证据池；不会把无结构自然语言强行当证据。

假设返回值为 `data.items`，每项字段为 `name/link/summary/id/date`，只改配置：

```yaml
results_path: data.items
fields:
  title: name
  url: link
  snippet: summary
  source_uri: id
  published_at: date
```

`fixed_args` 存放固定业务过滤参数；`query_arg` 是查询参数名。只能放非敏感业务参数。需要 query 以外的复杂、动态入参，扩展 `make_search` 或自定义 runner；不要把认证字段放进模型工具 schema。

來源优先级：单元/请求注入的 source_names > source_priority_file 的 YAML 名称列表 > source_fallback。它们控制优先级，不把另一 origin 删除。每个必需 origin 的第一优先来源由代码并行调用；其他白名单来源可由 Agent 为缺口追加检索。

来源等级和发布方当前来自管理员的逻辑 source 配置，不是模型自报。对覆盖全网的搜索服务，默认统一 L4 和保守发布方分组，**不能把搜索服务名视为已核实的独立原始发布者**。需要精细来源质量时，把站点过滤配置为多个逻辑 source，或在可信适配器中基于受校验的域名规则分类；内部转载与外部原文也不能冒充独立交叉验证。

## 五、替换执行器，不重写工作流

设置 `runner_factory: my_company.research:create_runner`，工厂签名 `create_runner(settings, store)`，返回实现以下异步方法的对象：

```python
async def plan(run, proposed=None) -> ResearchPlan: ...
async def research(run, unit, dependencies) -> ResearchResult: ...
async def synthesize(run, plan, findings, pool, errors=()) -> StructuredReport: ...
```

这些方法是扩展接口声明，不是默认实现中的 TODO。默认 DemoRunner、DeerFlowRunner 均有完整方法实现。

生产自定义 runner 必须继续：用 runtime.context 传凭据；在 MCP/模型调用前使用 store.reserve；只允许已授权工具；保存成功工具调用缓存；将工具结果而不是模型编写的 URL/标题转成 RawEvidence；让 synthesize 无搜索权限。声明 `runner: deerflow` 保留真实模式校验与前端标识。

结构校验通过不代表证据语义支撑成立。需要 SemanticCritic 时，在 validator 节点中增加受预算控制的独立评审，再返回 ResearchGap；当前实现不冒充已实现语义事实核验。

## 六、版本与回归

启动时冻结 Skill 文本，记录配置+文本指纹。运行中的任务不会因编辑磁盘文件而改变方法论。重启后指纹不一致的历史任务仍可查看，但不能在新配置下直接恢复；恢复原配置或新建任务。

宿主模型/Custom Agent 配置仍由宿主管理：它们的版本和原 MCP/数据源 ACL 也应在你的部署流程中固定。当前指纹不宣称对外部服务内容、模型权重或宿主全部配置做版本锁定。

每次修改至少跑单元/集成测试，并使用 `examples/deepresearch/evals/cases.json` 的问题作授权环境回归。该问题集是待运行的测试输入，不是已验证的真实报告质量分数。
