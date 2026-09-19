import { firstText } from "./presentation";
import type {
  EditableSettings,
  ModelSpec,
  NodeName,
  NodeSpec,
  ProviderSpec,
  RoleSpec,
  SettingsView,
  SourceSpec,
} from "./types";

export type SettingsSection =
  | "models"
  | "nodes"
  | "roles"
  | "prompts"
  | "sources"
  | "mcp"
  | "runtime"
  | "secrets";

export const SECTION_LABELS: Record<SettingsSection, string> = {
  models: "模型",
  nodes: "节点调参",
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
  nodes: ["nodes"],
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
    "writer_concurrency",
    "plan_countdown_seconds",
    "plan_min_units",
    "plan_max_units",
    "max_output_tokens",
    "max_searches_per_unit",
    "max_seconds_per_unit",
    "max_findings_per_unit",
    "report_time_reserve_seconds",
    "supplement_gap_codes",
    "output_retries",
    "allow_limited_report",
    "max_synthesis_repairs",
    "max_report_sections",
    "report_length_scale",
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
  max_searches_per_unit: "每步检索次数上限",
  max_seconds_per_unit: "单个研究步骤的软时限",
  max_findings_per_unit: "每步发现条数上限",
  report_time_reserve_seconds: "写报告预留时间",
  plan_min_units: "计划最少步骤数",
  plan_max_units: "计划最多步骤数",
  supplement_gap_codes: "触发补研的缺口",
  writer_concurrency: "章节写作并发",
  nodes: "节点调参",
  output_retries: "整理失败重试次数",
  allow_limited_report: "缺口未补齐时仍写报告",
  cite_search_results: "引用搜索结果摘要",
  max_synthesis_repairs: "引用修复次数",
  max_report_sections: "报告最多章节数",
  report_length_scale: "报告长度系数",
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

/** Bounds the server enforces on `report_length_scale`; 1.0 when unset. */
export const REPORT_LENGTH_SCALE = { min: 0.2, max: 3, fallback: 1 } as const;

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

/** Page-local facts about a draft item that never reach the server: a stable
 * React key (models, sources and providers are arrays without ids), and the
 * name other settings still use for the item while its own name is being typed
 * through a value references cannot follow. Kept beside the draft, keyed by
 * object identity, so the saved payload stays exactly the settings. */
type RowMeta = { key: string; refs: Record<string, string> };
const ROW_META = new WeakMap<object, RowMeta>();
let rowCount = 0;

function rowMeta(item: object) {
  let meta = ROW_META.get(item);
  if (!meta) {
    meta = { key: `row-${++rowCount}`, refs: {} };
    ROW_META.set(item, meta);
  }
  return meta;
}

/** A React key that stays with the item when the list is reordered or shortened. */
export function rowKey(item: object) {
  return rowMeta(item).key;
}

/** Give a copy of the same shape the keys of the original: the clone behind
 * every edit, and the server's answer to a save (the same lists, saved). */
export function carryMeta(from: unknown, to: unknown) {
  if (!from || !to || typeof from !== "object" || typeof to !== "object")
    return;
  const meta = ROW_META.get(from);
  if (meta) ROW_META.set(to, { key: meta.key, refs: { ...meta.refs } });
  if (Array.isArray(from)) {
    if (Array.isArray(to))
      from.forEach((item, index) => carryMeta(item, to[index]));
    return;
  }
  for (const [key, value] of Object.entries(from))
    carryMeta(value, (to as Record<string, unknown>)[key]);
}

/** A copy with one change applied, so React state stays immutable. */
export function edit(
  settings: EditableSettings,
  change: (draft: EditableSettings) => void,
): EditableSettings {
  const draft = structuredClone(settings);
  // Before the change runs the copy has the same shape, so every item keeps
  // its key; moved, removed and added items then follow by identity.
  carryMeta(settings, draft);
  change(draft);
  return draft;
}

/** The name other settings currently use for an item: its own name, except
 * while a rename is passing through a value that references must not follow. */
export function referencedAs<T extends object>(
  item: T,
  field: keyof T & string,
): string {
  return ROW_META.get(item)?.refs[field] ?? String(item[field] ?? "");
}

/** Rename an item and everything that refers to it. A name that is empty or
 * belongs to another item is only a keystroke on the way to the final name:
 * following it would hand this item's references to that other item (and the
 * next keystroke would take the other item's references along). References
 * wait at the last name they could follow and catch up afterwards. */
function follow<T extends object>(
  item: T,
  field: keyof T & string,
  value: string,
  others: T[],
  remap: (from: string, to: string) => void,
  reserved: string[] = [],
) {
  const from = referencedAs(item, field);
  const taken =
    reserved.includes(value) ||
    others.some(
      (other) =>
        other !== item &&
        (String(other[field]) === value ||
          referencedAs(other, field) === value),
    );
  (item as Record<string, unknown>)[field] = value;
  const meta = rowMeta(item);
  if (!value || taken) {
    meta.refs[field] = from;
    return;
  }
  delete meta.refs[field];
  if (from !== value) remap(from, value);
}

const MODEL_FIELDS = [
  "default_model",
  "rewrite_model",
  "extraction_model",
] as const;

function remapModel(draft: EditableSettings, from: string, to: string | null) {
  for (const field of MODEL_FIELDS)
    if (draft[field] === from) draft[field] = to;
  if (draft.compaction.model === from) draft.compaction.model = to;
  for (const role of Object.values(draft.skills))
    if (role.model === from) role.model = to;
  for (const name of Object.keys(draft.nodes ?? {}) as NodeName[])
    editNode(draft, name, (node) => {
      if (node.model === from) node.model = to;
    });
  const price = draft.pricing[from];
  if (price) {
    delete draft.pricing[from];
    if (to) draft.pricing[to] = price;
  }
}

/** Rename a model together with every setting that names it (default, rewrite,
 * extraction and compaction models, role and node models, list prices). */
export function renameModel(
  draft: EditableSettings,
  index: number,
  value: string,
) {
  follow(draft.models[index]!, "name", value, draft.models, (from, to) =>
    remapModel(draft, from, to),
  );
}

/** Remove a model; settings that named it fall back to inheritance. */
export function removeModel(draft: EditableSettings, index: number) {
  const [model] = draft.models.splice(index, 1);
  if (model) remapModel(draft, referencedAs(model, "name"), null);
}

/** Rename a source together with its place in the default source order. */
export function renameSource(
  draft: EditableSettings,
  index: number,
  value: string,
) {
  follow(draft.sources[index]!, "name", value, draft.sources, (from, to) => {
    draft.source_fallback = draft.source_fallback.map((name) =>
      name === from ? to : name,
    );
  });
}

/** Rename a source's model-facing tool together with the role allowlists that
 * name it. Engine tool names are reserved: an allowlist entry such as
 * read_file belongs to the engine tool, not to a source typed through it. */
export function renameSourceTool(
  draft: EditableSettings,
  index: number,
  value: string,
  engineTools: string[] = [],
) {
  follow(
    draft.sources[index]!,
    "tool",
    value,
    draft.sources,
    (from, to) => {
      for (const role of Object.values(draft.skills))
        if (role.tools?.includes(from))
          role.tools = [
            ...new Set(role.tools.map((tool) => (tool === from ? to : tool))),
          ];
    },
    engineTools,
  );
}

export function removeSource(draft: EditableSettings, index: number) {
  const [source] = draft.sources.splice(index, 1);
  if (!source) return;
  const name = referencedAs(source, "name");
  draft.source_fallback = draft.source_fallback.filter((item) => item !== name);
}

/** Rename an MCP server (a key, so only a free, valid name) together with the
 * sources and providers bound to it; the order of servers is kept. */
export function renameMcpServer(
  draft: EditableSettings,
  from: string,
  to: string,
) {
  if (from === to || !draft.mcp_servers[from] || draft.mcp_servers[to])
    return false;
  draft.mcp_servers = Object.fromEntries(
    Object.entries(draft.mcp_servers).map(([name, server]) => [
      name === from ? to : name,
      server,
    ]),
  );
  for (const source of draft.sources) {
    if (source.server === from) source.server = to;
    for (const provider of source.providers)
      if (provider.server === from) provider.server = to;
  }
  return true;
}

/** A node without a key inherits everything. */
export const NODE_DEFAULTS: NodeSpec = {
  enabled: true,
  model: null,
  temperature: null,
  top_p: null,
  max_tokens: null,
  timeout_seconds: null,
  output_retries: null,
  json_mode: false,
  extra_body: {},
};
/** Direct calls; only these can ask the provider for a JSON object. */
export const JSON_MODE_NODES: readonly string[] = ["rewrite", "conversion"];
export const NODE_LABELS: Record<NodeName, string> = {
  rewrite: "请求改写",
  plan: "研究计划",
  research: "检索研究",
  conversion: "笔记整理",
  outline: "报告大纲",
  section: "章节写作",
  summary: "执行摘要",
  revision: "报告改写",
  follow_up: "追问分流",
};
/** Nodes research can do without (the catalog's `optional` when it is loaded). */
const OPTIONAL_NODES: readonly string[] = ["rewrite", "summary"];

export function nodeSpec(settings: EditableSettings, name: NodeName): NodeSpec {
  return { ...NODE_DEFAULTS, ...settings.nodes?.[name] };
}

/** Change one node. A node that is back to full inheritance loses its key, so
 * the saved overrides only ever list nodes that were actually tuned. */
export function editNode(
  draft: EditableSettings,
  name: NodeName,
  change: (node: NodeSpec) => void,
) {
  const node = structuredClone(nodeSpec(draft, name));
  change(node);
  draft.nodes ??= {};
  if (sameValue(node, NODE_DEFAULTS)) delete draft.nodes[name];
  else draft.nodes[name] = node;
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
    top_p: null,
    timeout_seconds: 600,
    max_retries: 2,
    stream_usage: null,
    max_tokens_param: null,
    session_param: null,
    session_header: null,
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
    enabled: true,
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

/** Sources research may use; a disabled one stays configured but is never offered. */
export function activeSources(settings: EditableSettings) {
  return settings.sources.filter((source) => source.enabled !== false);
}

/** Without an enabled read source, search results and records are the citable
 * evidence and researchers work from the "no original text" prompts. */
export function hasReadSource(settings: EditableSettings) {
  return activeSources(settings).some((source) => source.role === "read");
}

/** Every MCP tool a source or provider calls (mirrors Settings.mcp_bindings). */
export function mcpBindings(settings: EditableSettings) {
  const found: { label: string; server: string; tool: string }[] = [];
  for (const source of settings.sources) {
    if ((source.kind ?? "channel") === "mcp" && source.server)
      found.push({
        label: `数据源“${source.name}”`,
        server: source.server,
        tool: firstText(source.mcp_tool, source.tool),
      });
    for (const provider of source.providers)
      if (provider.type === "mcp" && provider.server && provider.tool)
        found.push({
          label: `供应商“${source.name}/${provider.id}”`,
          server: provider.server,
          tool: provider.tool,
        });
  }
  return found;
}

const CREDENTIAL_KEY = /authorization|cookie|token|secret|key|password/i;

/** Header and environment values may reference $ENV or secret:NAME, also inside
 * a string ("Bearer ${TOKEN}"). A credential-looking key whose value holds no
 * reference at all is a pasted credential, which must never be saved. */
export function literalCredentials(
  values: Record<string, unknown> | null | undefined,
) {
  return Object.entries(values ?? {})
    .filter(
      ([key, value]) =>
        CREDENTIAL_KEY.test(key) &&
        typeof value === "string" &&
        value.trim() !== "" &&
        !value.includes("$") &&
        !value.includes("secret:"),
    )
    .map(([key]) => key);
}

const LITERAL_CREDENTIAL =
  "请改用 $环境变量 或 secret:名字 引用，不要保存明文凭据";

/** Keep the draft and its pinned version; only the secret names and the
 * reference status come from a secrets call. Adopting the whole answer would
 * re-pin an unsaved draft to the newest version and defeat the 409 guard. */
export function mergeSecrets(
  view: SettingsView,
  next: Pick<SettingsView, "secrets">,
): SettingsView {
  return { ...view, secrets: next.secrets };
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
    ...Object.entries(settings.nodes ?? {}).map(
      ([name, node]) =>
        [
          `节点“${NODE_LABELS[name as NodeName] ?? name}”的模型`,
          node?.model ?? null,
        ] as [string, string | null],
    ),
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
    for (const [field, values] of [
      ["请求头", server.headers],
      ["环境变量", server.env],
    ] as const)
      for (const key of literalCredentials(values))
        problems.push(
          `MCP 服务“${name}”的${field}“${key}”：${LITERAL_CREDENTIAL}`,
        );
  }
  for (const binding of mcpBindings(settings)) {
    const allowedTools = servers[binding.server]?.allowed_tools;
    if (allowedTools && !allowedTools.includes(binding.tool))
      problems.push(
        `${binding.label}使用的 MCP 工具“${binding.tool}”不在服务“${binding.server}”的工具白名单中：把它加入白名单，或改用白名单内的工具`,
      );
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
      if (provider.type === "http")
        for (const key of literalCredentials(provider.headers))
          problems.push(`${label}的请求头“${key}”：${LITERAL_CREDENTIAL}`);
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
    // A disabled source is never offered to research, so it counts for neither rule.
    const active = activeSources(settings);
    if (!active.length)
      problems.push(
        settings.sources.length
          ? "真实研究至少需要一个启用的数据源"
          : "真实研究至少需要一个数据源",
      );
    const origins = new Set(active.map((source) => source.origin));
    if (
      active.length &&
      settings.require_dual_source &&
      !(origins.has("internal") && origins.has("external"))
    )
      problems.push(
        "已要求内外部来源并存：启用的数据源需要同时覆盖内部和外部资料，或在“数据源与搜索”中关闭这个要求",
      );
  }
  for (const [name, node] of Object.entries(settings.nodes ?? {})) {
    const optional =
      view?.catalog.nodes?.find((item) => item.name === name)?.optional ??
      OPTIONAL_NODES.includes(name);
    if (node?.enabled === false && !optional)
      problems.push(
        `节点“${NODE_LABELS[name as NodeName] ?? name}”不能关闭，只有请求改写和执行摘要可以关闭`,
      );
  }
  if (settings.plan_min_units > settings.plan_max_units)
    problems.push("计划的研究步骤数：最少步骤数不能大于最多步骤数");
  const scale = settings.report_length_scale;
  if (
    scale != null &&
    !(
      Number.isFinite(scale) &&
      scale >= REPORT_LENGTH_SCALE.min &&
      scale <= REPORT_LENGTH_SCALE.max
    )
  )
    problems.push(
      `报告长度系数需要在 ${REPORT_LENGTH_SCALE.min}–${REPORT_LENGTH_SCALE.max} 之间`,
    );
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
