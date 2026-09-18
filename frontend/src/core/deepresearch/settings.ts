import { firstText } from "./presentation";
import type {
  EditableSettings,
  ModelSpec,
  ProviderSpec,
  RoleSpec,
  SettingsView,
  SourceSpec,
} from "./types";

export type SettingsSection =
  | "models"
  | "roles"
  | "prompts"
  | "sources"
  | "mcp"
  | "runtime"
  | "secrets";

export const SECTION_LABELS: Record<SettingsSection, string> = {
  models: "模型",
  roles: "研究角色",
  prompts: "提示词",
  sources: "数据源与搜索",
  mcp: "MCP 服务",
  runtime: "运行参数",
  secrets: "密钥与历史",
};

/** Which settings fields each page section edits. */
export const SECTION_FIELDS: Record<
  SettingsSection,
  (keyof EditableSettings)[]
> = {
  models: [
    "models",
    "default_model",
    "rewrite_model",
    "extraction_model",
    "pricing",
  ],
  roles: ["skills"],
  prompts: ["prompts"],
  sources: [
    "sources",
    "source_fallback",
    "require_dual_source",
    "cite_search_results",
  ],
  mcp: ["mcp_servers"],
  runtime: [
    "engine_tools",
    "compaction",
    "max_concurrency",
    "plan_countdown_seconds",
    "max_output_tokens",
    "output_retries",
    "allow_limited_report",
    "max_synthesis_repairs",
    "max_report_sections",
    "trace_capture_content",
    "llm_audit",
  ],
  secrets: [],
};

export const FIELD_LABELS: Record<keyof EditableSettings, string> = {
  compaction: "上下文压缩",
  skills: "研究角色",
  prompts: "提示词",
  models: "模型",
  default_model: "默认模型",
  rewrite_model: "请求改写模型",
  extraction_model: "笔记整理模型",
  mcp_servers: "MCP 服务",
  engine_tools: "引擎工具",
  sources: "数据源",
  source_fallback: "数据源默认顺序",
  require_dual_source: "内外部来源并存",
  max_concurrency: "同时研究的单元数",
  plan_countdown_seconds: "计划倒计时",
  max_output_tokens: "单次输出上限",
  output_retries: "整理失败重试次数",
  allow_limited_report: "缺口未补齐时仍写报告",
  cite_search_results: "引用搜索结果摘要",
  max_synthesis_repairs: "引用修复次数",
  max_report_sections: "报告最多章节数",
  trace_capture_content: "Trace 记录内容",
  llm_audit: "记录完整的 LLM 调用",
  pricing: "费用单价",
};

/** Human names for saved setting fields; unknown (future) fields keep their key. */
export function fieldLabels(fields: string[]) {
  return fields
    .map((field) => FIELD_LABELS[field as keyof EditableSettings] ?? field)
    .join("、");
}

export const FIXED_ROLES = ["deepresearch", "report-synthesis"] as const;
export const ROLE_TITLES: Record<string, string> = {
  deepresearch: "规划",
  "report-synthesis": "报告撰写",
};

function stable(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(
          Object.entries(item as Record<string, unknown>).sort(([a], [b]) =>
            a.localeCompare(b),
          ),
        )
      : item,
  );
}

export function sameValue(left: unknown, right: unknown) {
  return stable(left) === stable(right);
}

/** Sections whose fields differ between the draft and a reference copy. */
export function changedSections(
  draft: EditableSettings,
  reference: EditableSettings,
): SettingsSection[] {
  return (Object.keys(SECTION_FIELDS) as SettingsSection[]).filter((section) =>
    SECTION_FIELDS[section].some(
      (field) => !sameValue(draft[field], reference[field]),
    ),
  );
}

/** A copy with one change applied, so React state stays immutable. */
export function edit(
  settings: EditableSettings,
  change: (draft: EditableSettings) => void,
): EditableSettings {
  const draft = structuredClone(settings);
  change(draft);
  return draft;
}

export function uniqueName(base: string, taken: Iterable<string>) {
  const used = new Set(taken);
  if (!used.has(base)) return base;
  for (let index = 2; ; index++) {
    const candidate = `${base}-${index}`;
    if (!used.has(candidate)) return candidate;
  }
}

export const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/;
export const TOOL_NAME = /^[A-Za-z0-9_-]{1,64}$/;

/** A credential reference is empty, $ENV_NAME or secret:NAME; literal keys are rejected. */
export function referenceProblem(value: string | null | undefined) {
  if (!value) return null;
  if (/^\$[A-Za-z_][A-Za-z0-9_]*$/.test(value)) return null;
  if (/^secret:[A-Za-z0-9_.-]{1,80}$/.test(value)) return null;
  return "请填写环境变量引用（$变量名）或已保存的密钥（secret:名字），不要直接粘贴密钥";
}

export function newModel(existing: ModelSpec[]): ModelSpec {
  return {
    name: uniqueName(
      "model",
      existing.map((model) => model.name),
    ),
    display_name: "",
    provider: "openai",
    model: "",
    base_url: null,
    api_key: null,
    max_tokens: 8192,
    context_window: null,
    temperature: null,
    timeout_seconds: 600,
    max_retries: 2,
    supports_thinking: false,
    extra: {},
  };
}

export function newRole(): RoleSpec {
  return {
    name: "新研究员",
    description: "描述这个研究角度，规划时据此分配研究单元",
    methodology:
      "## 方法\n- 先用多个聚焦的查询搜索，优先官方和一手资料。\n- 打开权威页面阅读原文后再下结论。\n- 记录日期与状态。\n\n## 返回的研究笔记\n每条发现写明结论、打开过的网址、原文摘录、日期与状态。",
    system_prompt: "",
    model: null,
    tools: null,
    enabled: true,
    max_turns: null,
    timeout_seconds: 600,
  };
}

export function newProvider(
  type: string,
  existing: ProviderSpec[],
): ProviderSpec {
  return {
    id: uniqueName(
      type.replace(/_/g, "-"),
      existing.map((provider) => provider.id),
    ),
    type,
    enabled: true,
    api_key: null,
    base_url: null,
    options: {},
    method: "GET",
    url: type === "http" ? "https://" : null,
    headers: {},
    params: {},
    body: null,
    server: null,
    tool: null,
    arguments: {},
    timeout_seconds: 30,
    allow_private_network: false,
  };
}

const ROLE_DEFAULTS: Record<
  SourceSpec["role"],
  { tool: string; name: string; provider: string }
> = {
  search: { tool: "web_search", name: "web-search", provider: "duckduckgo" },
  read: { tool: "web_fetch", name: "web-read", provider: "direct" },
  data: { tool: "knowledge_search", name: "knowledge", provider: "http" },
};

export function newSource(
  role: SourceSpec["role"],
  existing: SourceSpec[],
): SourceSpec {
  const defaults = ROLE_DEFAULTS[role];
  return {
    name: uniqueName(
      defaults.name,
      existing.map((source) => source.name),
    ),
    tool: uniqueName(
      defaults.tool,
      existing.map((source) => source.tool),
    ).replace(/-/g, "_"),
    kind: "channel",
    role,
    origin: role === "data" ? "internal" : "external",
    level: role === "data" ? "L1" : "L4",
    priority: 100,
    publisher: "unclassified",
    description: "",
    providers: [newProvider(defaults.provider, [])],
  };
}

/** Model-facing tools a role may be allowed to use: provider sources, MCP sources and engine tools. */
export function allowlistChoices(
  settings: EditableSettings,
  engineTools: string[],
) {
  return [
    ...settings.sources.map((source) => ({
      name: source.tool,
      label: `${source.tool}（${source.name}）`,
    })),
    ...engineTools.map((name) => ({ name, label: `${name}（引擎）` })),
  ];
}

export function providerTypesFor(role: SourceSpec["role"], view: SettingsView) {
  return view.catalog.source_providers.filter(
    (provider) => provider.role === role || provider.role === null,
  );
}

/** Problems the page can show before asking the server to validate. */
export function draftProblems(
  settings: EditableSettings,
  view?: SettingsView,
): string[] {
  const problems: string[] = [];
  const models = new Set(settings.models.map((model) => model.name));
  if (settings.models.length !== models.size) problems.push("模型名称重复");
  for (const model of settings.models) {
    if (!IDENTIFIER.test(model.name))
      problems.push(`模型名称“${model.name}”只能包含字母、数字、- 和 _`);
    if (!model.model.trim())
      problems.push(`模型“${model.name}”缺少服务端模型名`);
    if (model.provider === "custom" && !model.use?.trim())
      problems.push(`模型“${model.name}”是自定义类型，需要填写模型类（use）`);
    const reference = referenceProblem(model.api_key);
    if (reference) problems.push(`模型“${model.name}”：${reference}`);
  }
  const references: [string, string | null][] = [
    ["默认模型", settings.default_model],
    ["请求改写模型", settings.rewrite_model],
    ["整理模型", settings.extraction_model],
    ...Object.entries(settings.skills).map(
      ([name, role]) =>
        [
          `角色“${ROLE_TITLES[name] ?? firstText(role.name, name)}”的模型`,
          role.model ?? null,
        ] as [string, string | null],
    ),
  ];
  if (models.size)
    for (const [label, value] of references)
      if (value && !models.has(value))
        problems.push(`${label}“${value}”不在模型列表中`);
  for (const name of Object.keys(settings.skills))
    if (!IDENTIFIER.test(name))
      problems.push(`角色标识“${name}”只能包含字母、数字、- 和 _`);
  for (const name of FIXED_ROLES)
    if (!settings.skills[name])
      problems.push(`缺少固定角色“${ROLE_TITLES[name]}”`);
  const researchers = Object.entries(settings.skills).filter(
    ([name, role]) =>
      !(FIXED_ROLES as readonly string[]).includes(name) &&
      role.enabled !== false,
  );
  if (!researchers.length) problems.push("至少需要启用一个研究员角色");
  const servers = settings.mcp_servers;
  for (const [name, server] of Object.entries(servers)) {
    if (!IDENTIFIER.test(name))
      problems.push(`MCP 服务名称“${name}”只能包含字母、数字、- 和 _`);
    if (server.transport === "stdio" && !server.command?.trim())
      problems.push(`MCP 服务“${name}”需要填写命令`);
    if (server.transport !== "stdio" && !server.url?.trim())
      problems.push(`MCP 服务“${name}”需要填写服务地址`);
  }
  const names = new Set<string>();
  const tools = new Set<string>();
  const allowed = (role: SourceSpec["role"]) =>
    view
      ? new Set(providerTypesFor(role, view).map((provider) => provider.type))
      : null;
  for (const source of settings.sources) {
    const kind = source.kind ?? "channel";
    if (!IDENTIFIER.test(source.name))
      problems.push(`数据源名称“${source.name}”格式不正确`);
    if (names.has(source.name)) problems.push(`数据源名称“${source.name}”重复`);
    names.add(source.name);
    if (!TOOL_NAME.test(source.tool))
      problems.push(`数据源“${source.name}”的工具名只能包含字母、数字、- 和 _`);
    if (tools.has(source.tool)) problems.push(`工具名“${source.tool}”重复`);
    tools.add(source.tool);
    if (kind === "mcp" && !source.server)
      problems.push(`数据源“${source.name}”需要选择 MCP 服务`);
    if (kind !== "channel") continue;
    if (!source.providers.length)
      problems.push(`数据源“${source.name}”至少需要一个供应商`);
    const types = allowed(source.role);
    const ids = new Set<string>();
    for (const provider of source.providers) {
      const label = `供应商“${source.name}/${provider.id}”`;
      if (!IDENTIFIER.test(provider.id))
        problems.push(`${label}的 ID 只能包含字母、数字、- 和 _`);
      if (ids.has(provider.id))
        problems.push(`数据源“${source.name}”的供应商 ID “${provider.id}”重复`);
      ids.add(provider.id);
      if (types && !types.has(provider.type))
        problems.push(
          `${label}的类型 ${provider.type} 不能用于当前作用，请更换类型`,
        );
      const reference = referenceProblem(provider.api_key);
      if (reference) problems.push(`${label}：${reference}`);
      if (
        provider.type === "mcp" &&
        (!provider.server || !servers[provider.server])
      )
        problems.push(`${label}需要选择已配置的 MCP 服务`);
      if (provider.type === "mcp" && !provider.tool?.trim())
        problems.push(`${label}需要填写 MCP 工具原名`);
      if (
        provider.type === "http" &&
        !/^https?:\/\/[^/]/.test(provider.url ?? "")
      )
        problems.push(`${label}需要填写完整的接口地址`);
      if (
        ["searxng", "ragflow", "lightrag"].includes(provider.type) &&
        !provider.base_url?.trim()
      )
        problems.push(`${label}需要填写接口地址`);
    }
  }
  for (const name of settings.source_fallback)
    if (!names.has(name)) problems.push(`默认顺序中的数据源“${name}”不存在`);
  if (view?.operator.runner === "deerflow") {
    if (!settings.sources.length) problems.push("真实研究至少需要一个数据源");
    const origins = new Set(settings.sources.map((source) => source.origin));
    if (
      settings.require_dual_source &&
      !(origins.has("internal") && origins.has("external"))
    )
      problems.push(
        "已要求内外部来源并存：需要同时配置内部和外部数据源，或在“数据源与搜索”中关闭这个要求",
      );
  }
  if (
    settings.compaction.model &&
    models.size &&
    !models.has(settings.compaction.model)
  )
    problems.push(`压缩摘要模型“${settings.compaction.model}”不在模型列表中`);
  for (const [name, price] of Object.entries(settings.pricing))
    if (!price.currency || price.currency.length < 3)
      problems.push(`模型“${name}”的币种至少 3 个字母，例如 CNY`);
  return problems;
}
