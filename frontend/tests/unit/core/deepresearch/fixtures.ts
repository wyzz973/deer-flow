import type {
  Capabilities,
  EditableSettings,
  ResearchActivity,
  Run,
  SettingsView,
} from "@/core/deepresearch/types";

export function makeRun(
  runId = "run",
  status = "AWAITING_PLAN_CONFIRMATION",
): Run {
  const budget = {
    max_iterations: 2,
    max_units: 3,
    max_tool_calls: 60,
    max_elapsed_seconds: 900,
    max_model_tokens: 120000,
  };
  const plan: NonNullable<Run["plan"]> = {
    goal: "Compare databases",
    constraints: [],
    expected_output: "report",
    plan_version: 1,
    research_units: [
      {
        id: "U1",
        skill: "technical-route",
        objective: "Compare concurrency",
        priority: 1,
        depends_on: [],
        parent_gap_id: null,
        source_strategy: {
          source_names: [],
          required_origins: ["external"],
          max_results: 4,
          not_before: null,
        },
      },
    ],
  };
  return {
    run_id: runId,
    thread_id: `thread-${runId}`,
    query: "Compare databases",
    status,
    created_at: "2026-09-16T00:00:00Z",
    updated_at: "2026-09-16T00:00:00Z",
    plan,
    units: plan.research_units,
    unit_statuses: {},
    evidence_count: 0,
    iteration: 0,
    gaps: [],
    usage: {
      tool_calls: 0,
      model_tokens: 0,
      reported_model_tokens: 0,
      elapsed_seconds: 0,
    },
    report: null,
    error: null,
    demo: true,
    budget,
    conversation: [],
    server_time: "2026-09-16T00:00:00Z",
    auto_start_at: "2026-09-16T00:00:45Z",
  };
}

export function capabilities(): Capabilities {
  return {
    mode: "demo",
    ready: true,
    skills: {},
    sources: [],
    budget_ceiling: makeRun().budget,
    plan_countdown_seconds: 45,
  };
}

export function activity(
  status = "AWAITING_PLAN_CONFIRMATION",
): ResearchActivity {
  return {
    status,
    started_at: null,
    finished_at: null,
    elapsed_seconds: null,
    counts: { searches: 0, pages_read: 0, steps: 1, steps_done: 0 },
    current: null,
    items: [],
  };
}

export function makeSettings(): EditableSettings {
  return {
    skills: {
      deepresearch: {
        description: "规划",
        methodology: "计划规则",
        enabled: true,
        timeout_seconds: 600,
      },
      "report-synthesis": {
        description: "写作",
        methodology: "写作规则",
        enabled: true,
        timeout_seconds: 600,
      },
      "technical-route": {
        name: "技术路线",
        description: "技术方案与取舍",
        methodology: "方法",
        model: null,
        tools: null,
        enabled: true,
        timeout_seconds: 600,
      },
    },
    prompts: {
      plan: "PLAN",
      research: "RESEARCH",
      research_records: "RESEARCH RECORDS",
      conversion_records: "CONVERSION RECORDS",
      compaction: "{messages}",
    },
    models: [
      {
        name: "flash",
        provider: "deepseek",
        model: "deepseek-v4-flash",
        api_key: "$DEEPSEEK_API_KEY",
        context_window: 128000,
        max_tokens: 8192,
      },
    ],
    default_model: "flash",
    rewrite_model: null,
    extraction_model: "flash",
    mcp_servers: {},
    engine_tools: ["read_file"],
    sources: [
      {
        name: "public-web-search",
        tool: "web_search",
        role: "search",
        origin: "external",
        kind: "channel",
        enabled: true,
        level: "L4",
        priority: 100,
        publisher: "web",
        description: "",
        providers: [
          {
            id: "tavily",
            type: "tavily",
            enabled: true,
            api_key: "$TAVILY_API_KEY",
            timeout_seconds: 30,
          },
          {
            id: "duckduckgo",
            type: "duckduckgo",
            enabled: true,
            timeout_seconds: 30,
          },
        ],
      },
    ],
    source_fallback: ["public-web-search"],
    require_dual_source: false,
    max_concurrency: 3,
    plan_countdown_seconds: 45,
    max_output_tokens: 8192,
    max_searches_per_unit: 30,
    max_seconds_per_unit: null,
    max_findings_per_unit: 12,
    report_time_reserve_seconds: null,
    plan_min_units: 3,
    plan_max_units: 6,
    supplement_gap_codes: [
      "coverage",
      "unsupported",
      "missing-internal",
      "missing-external",
      "date",
      "open-questions",
    ],
    writer_concurrency: null,
    nodes: {},
    output_retries: 2,
    allow_limited_report: true,
    cite_search_results: false,
    max_synthesis_repairs: 1,
    max_report_sections: 8,
    report_length_scale: 1,
    trace_capture_content: true,
    llm_audit: true,
    compaction: {
      enabled: true,
      trigger_fraction: 0.6,
      fallback_trigger_tokens: 48000,
      keep_fraction: 0.4,
      max_summary_input_tokens: 24000,
      model: null,
    },
    pricing: {},
  };
}

export function makeSettingsView(
  settings: EditableSettings = makeSettings(),
  overrides: Partial<SettingsView> = {},
): SettingsView {
  return {
    version: 1,
    editable: true,
    error: null,
    settings,
    defaults: makeSettings(),
    overridden: [],
    updated_at: "2026-09-18T00:00:00Z",
    operator: {
      runner: "deerflow",
      max_active_runs: 8,
      favicons: true,
      budget_ceiling: makeRun().budget,
      native_tools: null,
    },
    catalog: {
      model_providers: [
        {
          id: "deepseek",
          label: "DeepSeek",
          hint: "",
          base_url: "https://api.deepseek.com",
          use: null,
        },
        { id: "custom", label: "自定义", hint: "", base_url: null, use: null },
      ],
      source_providers: [
        {
          type: "tavily",
          label: "Tavily",
          role: "search",
          requires_key: true,
          key_env: "TAVILY_API_KEY",
          base_url: null,
          docs: null,
        },
        {
          type: "duckduckgo",
          label: "DuckDuckGo",
          role: "search",
          requires_key: false,
          key_env: null,
          base_url: null,
          docs: null,
        },
        {
          type: "jina_reader",
          label: "Jina Reader",
          role: "read",
          requires_key: false,
          key_env: "JINA_API_KEY",
          base_url: null,
          docs: null,
        },
        {
          type: "mcp",
          label: "MCP 工具",
          role: null,
          requires_key: false,
          key_env: null,
          base_url: null,
          docs: null,
        },
        {
          type: "http",
          label: "自定义 HTTP",
          role: null,
          requires_key: false,
          key_env: null,
          base_url: null,
          docs: null,
        },
      ],
      engine_tools: [{ name: "read_file", description: "读取文件" }],
      fixed_roles: ["deepresearch", "report-synthesis"],
      nodes: [
        {
          name: "rewrite",
          label: "请求改写",
          description: "把对话改写成完整的研究请求。",
          model_fallback: "rewrite_model → 规划角色模型 → 默认模型",
          advice: "输出 JSON：温度 0–0.3。",
          optional: true,
        },
        {
          name: "plan",
          label: "研究计划",
          description: "把研究请求拆成研究步骤。",
          model_fallback: "规划角色模型 → 默认模型",
          advice: "输出 JSON：温度 0–0.2。",
          optional: false,
        },
        {
          name: "section",
          label: "章节写作",
          description: "并行写各章正文。",
          model_fallback: "写作角色模型 → 默认模型",
          advice: "长文本：温度 0.4–0.7。",
          optional: false,
        },
      ],
      gap_codes: [
        { code: "coverage", description: "某个研究目标完全没有可引用的发现" },
        { code: "unsupported", description: "存在没有绑定证据的结论" },
        { code: "missing-internal", description: "没有取得内部证据" },
        { code: "missing-external", description: "没有取得外部证据" },
        { code: "date", description: "没有满足时效要求的来源" },
        {
          code: "open-questions",
          description: "研究员认为还有可以继续查的问题",
        },
      ],
      prompts: [
        {
          key: "plan",
          stage: "规划",
          label: "研究计划",
          description: "",
          default: "PLAN",
        },
        {
          key: "research",
          stage: "研究",
          label: "研究方法",
          description: "",
          default: "RESEARCH",
        },
        {
          key: "research_records",
          stage: "研究",
          label: "研究方法（无原文）",
          description: "",
          default: "RESEARCH RECORDS",
        },
        {
          key: "conversion_records",
          stage: "研究",
          label: "笔记整理（无原文）",
          description: "",
          default: "CONVERSION RECORDS",
        },
      ],
    },
    secrets: {
      saved: [],
      references: { $DEEPSEEK_API_KEY: "set", $TAVILY_API_KEY: "missing" },
    },
    health: [],
    ...overrides,
  };
}
