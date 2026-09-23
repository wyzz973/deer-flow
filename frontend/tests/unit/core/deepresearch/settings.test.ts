import { describe, expect, it } from "@rstest/core";

import {
  allowlistChoices,
  changedSections,
  draftProblems,
  edit,
  editNode,
  fieldLabels,
  hasReadSource,
  literalCredentials,
  mcpBindings,
  mergeSecrets,
  newProvider,
  newRole,
  newSource,
  nodeSpec,
  providerTypesFor,
  referencedAs,
  referenceProblem,
  removeModel,
  removeSource,
  renameMcpServer,
  renameModel,
  renameSource,
  renameSourceTool,
  rowKey,
  sameValue,
  SECTION_FIELDS,
  uniqueName,
} from "@/core/deepresearch/settings";
import type { EditableSettings } from "@/core/deepresearch/types";

import { makeSettings, makeSettingsView } from "./fixtures";

describe("draft editing", () => {
  it("never mutates the settings it edits", () => {
    const settings = makeSettings();
    const next = edit(settings, (draft) => {
      draft.models[0]!.name = "renamed";
      draft.sources[0]!.providers.push(
        newProvider("serper", draft.sources[0]!.providers),
      );
    });
    expect(settings.models[0]!.name).toBe("flash");
    expect(settings.sources[0]!.providers).toHaveLength(2);
    expect(next.models[0]!.name).toBe("renamed");
    expect(next.sources[0]!.providers).toHaveLength(3);
  });

  it("reports which page sections a draft changed, ignoring key order", () => {
    const settings = makeSettings();
    expect(changedSections(settings, makeSettings())).toEqual([]);
    expect(
      changedSections(
        edit(settings, (draft) => (draft.prompts.plan = "NEW")),
        settings,
      ),
    ).toEqual(["prompts"]);
    expect(
      changedSections(
        edit(settings, (draft) => {
          draft.compaction.keep_fraction = 0.3;
          draft.models[0]!.max_tokens = 4096;
        }),
        settings,
      ),
    ).toEqual(["models", "runtime"]);
    expect(sameValue({ a: 1, b: 2 }, { b: 2, a: 1 })).toBe(true);
    expect(sameValue([1, 2], [2, 1])).toBe(false);
  });

  it("gives new items names that do not collide", () => {
    expect(uniqueName("model", ["model", "model-2"])).toBe("model-3");
    const source = newSource("read", makeSettings().sources);
    expect(source.role).toBe("read");
    expect(source.providers[0]!.type).toBe("direct");
    expect(newRole().enabled).toBe(true);
  });
});

describe("credential references", () => {
  it("accepts only environment or saved-secret references", () => {
    expect(referenceProblem(null)).toBeNull();
    expect(referenceProblem("$TAVILY_API_KEY")).toBeNull();
    expect(referenceProblem("secret:bocha-key")).toBeNull();
    expect(referenceProblem("sk-literal-key")).toContain("不要直接粘贴密钥");
  });
});

describe("draftProblems", () => {
  const view = makeSettingsView();

  it("passes a valid draft", () => {
    expect(draftProblems(makeSettings(), view)).toEqual([]);
  });

  it("catches references that no longer resolve", () => {
    const problems = draftProblems(
      edit(makeSettings(), (draft) => {
        draft.models[0]!.name = "renamed";
      }),
      view,
    );
    expect(problems.join("\n")).toContain("默认模型");
    expect(problems.join("\n")).toContain("整理模型");
  });

  it("catches duplicate tool names, empty provider lists and unusable provider types", () => {
    const problems = draftProblems(
      edit(makeSettings(), (draft) => {
        draft.sources.push({
          ...draft.sources[0]!,
          name: "second",
          providers: [],
        });
        draft.sources[0]!.providers[0]!.type = "jina_reader";
      }),
      view,
    );
    expect(
      problems.some((item) => item.includes("工具名“web_search”重复")),
    ).toBe(true);
    expect(problems.some((item) => item.includes("至少需要一个供应商"))).toBe(
      true,
    );
    expect(problems.some((item) => item.includes("不能用于当前作用"))).toBe(
      true,
    );
  });

  it("requires a configured MCP server for an MCP provider and a literal-free key", () => {
    const problems = draftProblems(
      edit(makeSettings(), (draft) => {
        draft.sources[0]!.providers[0] = {
          ...newProvider("mcp", []),
          server: "missing",
          tool: "search",
        };
        draft.models[0]!.api_key = "sk-oops";
      }),
      view,
    );
    expect(problems.some((item) => item.includes("已配置的 MCP 服务"))).toBe(
      true,
    );
    expect(problems.some((item) => item.includes("不要直接粘贴密钥"))).toBe(
      true,
    );
  });

  it("requires an enabled researcher and both origins when dual sourcing is on", () => {
    const disabled = draftProblems(
      edit(makeSettings(), (draft) => {
        draft.skills["technical-route"]!.enabled = false;
      }),
      view,
    );
    expect(disabled).toContain("至少需要启用一个研究员角色");
    const dual = draftProblems(
      edit(makeSettings(), (draft) => {
        draft.require_dual_source = true;
      }),
      view,
    );
    expect(dual.join("\n")).toContain("同时覆盖内部和外部资料");
  });

  it("flags a fallback entry whose source was renamed away", () => {
    const problems = draftProblems(
      edit(makeSettings(), (draft) => {
        draft.sources[0]!.name = "renamed";
      }),
      view,
    );
    expect(problems.some((item) => item.includes("默认顺序中的数据源"))).toBe(
      true,
    );
  });
});

describe("catalog helpers", () => {
  const view = makeSettingsView();

  it("offers only providers that can serve the source's role, plus generic ones", () => {
    expect(providerTypesFor("search", view).map((item) => item.type)).toEqual([
      "tavily",
      "duckduckgo",
      "mcp",
      "http",
    ]);
    expect(providerTypesFor("read", view).map((item) => item.type)).toEqual([
      "jina_reader",
      "mcp",
      "http",
    ]);
  });

  it("lists source tools and engine tools for a role allowlist", () => {
    expect(
      allowlistChoices(makeSettings(), ["read_file"]).map((item) => item.name),
    ).toEqual(["web_search", "read_file"]);
  });

  it("names saved fields in Chinese and leaves unknown ones alone", () => {
    expect(fieldLabels(["max_concurrency", "sources", "compaction"])).toBe(
      "同时研究的单元数、数据源、上下文压缩",
    );
    expect(fieldLabels(["future_field"])).toBe("future_field");
  });
});

/** Type a name the way a text field does: one draft per keystroke. */
function typed(
  settings: EditableSettings,
  values: string[],
  rename: (draft: EditableSettings, value: string) => void,
) {
  return values.reduce(
    (current, value) => edit(current, (draft) => rename(draft, value)),
    settings,
  );
}

function twoModels() {
  return edit(makeSettings(), (draft) => {
    draft.models = [
      { name: "model", provider: "deepseek", model: "m1", api_key: "$K" },
      { name: "model-2", provider: "deepseek", model: "m2", api_key: "$K" },
    ];
    draft.default_model = "model";
    draft.extraction_model = "model";
    draft.rewrite_model = "model-2";
    draft.compaction.model = "model-2";
    draft.skills["technical-route"]!.model = "model-2";
    draft.nodes = { plan: { ...nodeSpec(draft, "plan"), model: "model-2" } };
    draft.pricing = {
      model: { input_per_million: 1, output_per_million: 2, currency: "CNY" },
      "model-2": {
        input_per_million: 3,
        output_per_million: 4,
        currency: "CNY",
      },
    };
  });
}

describe("node tuning", () => {
  it("writes a node only once a field differs from inheritance", () => {
    const settings = makeSettings();
    expect(nodeSpec(settings, "plan").temperature).toBeNull();
    const tuned = edit(settings, (draft) =>
      editNode(draft, "plan", (node) => (node.temperature = 0.2)),
    );
    expect(tuned.nodes.plan).toEqual({
      enabled: true,
      model: null,
      temperature: 0.2,
      top_p: null,
      max_tokens: null,
      timeout_seconds: null,
      output_retries: null,
      thinking: null,
      json_mode: false,
      extra_body: {},
    });
    expect(changedSections(tuned, settings)).toEqual(["nodes"]);
  });

  it("drops the key when every field is back to inheritance", () => {
    const tuned = edit(makeSettings(), (draft) => {
      editNode(draft, "plan", (node) => (node.temperature = 0.2));
      editNode(draft, "summary", (node) => (node.enabled = false));
    });
    const cleared = edit(tuned, (draft) => {
      editNode(draft, "plan", (node) => (node.temperature = null));
      editNode(draft, "summary", (node) => (node.enabled = true));
    });
    expect(cleared.nodes).toEqual({});
    expect(changedSections(cleared, makeSettings())).toEqual([]);
  });

  it("rejects an unknown node model and a required node that is switched off", () => {
    const problems = draftProblems(
      edit(makeSettings(), (draft) => {
        editNode(draft, "plan", (node) => {
          node.model = "gone";
          node.enabled = false;
        });
        editNode(draft, "rewrite", (node) => (node.enabled = false));
      }),
      makeSettingsView(),
    );
    expect(problems).toContain("节点“研究计划”的模型“gone”不在模型列表中");
    expect(
      problems.some((item) => item.includes("节点“研究计划”不能关闭")),
    ).toBe(true);
    expect(problems.some((item) => item.includes("请求改写”不能关闭"))).toBe(
      false,
    );
  });
});

describe("renaming", () => {
  it("renames a model in every setting that names it", () => {
    const next = typed(twoModels(), ["model-", "fast"], (draft, value) =>
      renameModel(draft, 1, value),
    );
    expect(next.models.map((model) => model.name)).toEqual(["model", "fast"]);
    expect(next.rewrite_model).toBe("fast");
    expect(next.compaction.model).toBe("fast");
    expect(next.skills["technical-route"]!.model).toBe("fast");
    expect(next.nodes.plan!.model).toBe("fast");
    expect(Object.keys(next.pricing).sort()).toEqual(["fast", "model"]);
    expect(next.pricing.fast!.input_per_million).toBe(3);
    expect(draftProblems(next, makeSettingsView())).toEqual([]);
  });

  it("never takes over another model's references while typing through its name", () => {
    // "model-2" -> "model-" -> "model" (the other model's name) -> "mode" -> "fast"
    const next = typed(
      twoModels(),
      ["model-", "model", "mode", "fast"],
      (draft, value) => renameModel(draft, 1, value),
    );
    expect(next.default_model).toBe("model");
    expect(next.extraction_model).toBe("model");
    expect(next.pricing.model!.input_per_million).toBe(1);
    expect(next.rewrite_model).toBe("fast");
    expect(next.compaction.model).toBe("fast");
    expect(next.nodes.plan!.model).toBe("fast");
    expect(next.pricing.fast!.input_per_million).toBe(3);
  });

  it("keeps references at the last usable name while the name collides or is empty", () => {
    const colliding = typed(twoModels(), ["model"], (draft, value) =>
      renameModel(draft, 1, value),
    );
    expect(colliding.rewrite_model).toBe("model-2");
    expect(referencedAs(colliding.models[1]!, "name")).toBe("model-2");
    expect(draftProblems(colliding, makeSettingsView())).toContain(
      "模型名称重复",
    );
    const empty = typed(twoModels(), ["", "x"], (draft, value) =>
      renameModel(draft, 1, value),
    );
    expect(empty.rewrite_model).toBe("x");
  });

  it("removing a model lets its references inherit, even mid-rename", () => {
    const next = edit(
      typed(twoModels(), ["model"], (draft, value) =>
        renameModel(draft, 1, value),
      ),
      (draft) => removeModel(draft, 1),
    );
    expect(next.models.map((model) => model.name)).toEqual(["model"]);
    expect(next.rewrite_model).toBeNull();
    expect(next.compaction.model).toBeNull();
    expect(next.skills["technical-route"]!.model).toBeNull();
    expect(next.nodes).toEqual({});
    expect(Object.keys(next.pricing)).toEqual(["model"]);
    expect(next.default_model).toBe("model");
  });

  it("renames a source in the default order without taking another source's place", () => {
    const settings = edit(makeSettings(), (draft) => {
      draft.sources.push({
        ...newSource("read", draft.sources),
        name: "public-web",
      });
      draft.source_fallback = ["public-web-search", "public-web"];
    });
    const next = typed(
      settings,
      ["public-web-searc", "public-web", "public", "search"],
      (draft, value) => renameSource(draft, 0, value),
    );
    expect(next.source_fallback).toEqual(["search", "public-web"]);
    const removed = edit(next, (draft) => removeSource(draft, 0));
    expect(removed.source_fallback).toEqual(["public-web"]);
  });

  it("renames a source tool in role allowlists, leaving engine tools alone", () => {
    const settings = edit(makeSettings(), (draft) => {
      draft.skills["technical-route"]!.tools = ["web_search", "read_file"];
    });
    const next = typed(
      settings,
      ["read_file", "read_fil", "search"],
      (draft, value) => renameSourceTool(draft, 0, value, ["read_file"]),
    );
    expect(next.sources[0]!.tool).toBe("search");
    expect(next.skills["technical-route"]!.tools).toEqual([
      "search",
      "read_file",
    ]);
  });

  it("renames an MCP server with the sources and providers bound to it", () => {
    const settings = edit(makeSettings(), (draft) => {
      draft.mcp_servers = {
        first: { transport: "http", url: "http://a/mcp" },
        kb: { transport: "http", url: "http://b/mcp" },
      };
      draft.sources[0]!.providers[0] = {
        ...newProvider("mcp", []),
        server: "kb",
        tool: "search",
      };
      draft.sources.push({
        ...newSource("data", draft.sources),
        kind: "mcp",
        providers: [],
        server: "kb",
      });
    });
    // The card is keyed by the server, so a rename keeps it mounted.
    const card = rowKey(settings.mcp_servers.kb!);
    const next = edit(settings, (draft) => {
      expect(renameMcpServer(draft, "kb", "first")).toBe(false);
      expect(renameMcpServer(draft, "kb", "docs")).toBe(true);
    });
    expect(Object.keys(next.mcp_servers)).toEqual(["first", "docs"]);
    expect(next.sources[0]!.providers[0]!.server).toBe("docs");
    expect(next.sources[1]!.server).toBe("docs");
    expect(rowKey(next.mcp_servers.docs!)).toBe(card);
  });
});

describe("row keys", () => {
  it("stay with an item across edits, removal and reordering, and never reach the payload", () => {
    const settings = edit(makeSettings(), (draft) => {
      draft.models.push({ name: "second", provider: "deepseek", model: "m" });
    });
    const [first, second] = settings.models.map(rowKey);
    expect(first).not.toBe(second);
    const edited = edit(settings, (draft) => (draft.models[1]!.model = "m2"));
    expect(edited.models.map(rowKey)).toEqual([first, second]);
    const removed = edit(edited, (draft) => removeModel(draft, 0));
    expect(removed.models.map(rowKey)).toEqual([second]);
    const providers = settings.sources[0]!.providers.map(rowKey);
    const moved = edit(settings, (draft) =>
      draft.sources[0]!.providers.reverse(),
    );
    expect(moved.sources[0]!.providers.map(rowKey)).toEqual(
      [...providers].reverse(),
    );
    expect(JSON.stringify(removed)).not.toContain("row-");
  });
});

describe("sources that are switched off", () => {
  const view = makeSettingsView();

  it("needs an enabled source, and enabled sources of both origins for dual sourcing", () => {
    const none = draftProblems(
      edit(makeSettings(), (draft) => (draft.sources[0]!.enabled = false)),
      view,
    );
    expect(none).toContain("真实研究至少需要一个启用的数据源");
    const dual = edit(makeSettings(), (draft) => {
      draft.require_dual_source = true;
      const internal = newSource("data", draft.sources);
      internal.providers[0]!.url = "https://kb.example.com/search?q={query}";
      draft.sources.push(internal);
    });
    expect(draftProblems(dual, view)).toEqual([]);
    const internalOff = draftProblems(
      edit(dual, (draft) => (draft.sources[1]!.enabled = false)),
      view,
    );
    expect(internalOff.join("\n")).toContain("同时覆盖内部和外部资料");
  });

  it("knows when no enabled tool can open original text", () => {
    expect(hasReadSource(makeSettings())).toBe(false);
    const withReader = edit(makeSettings(), (draft) =>
      draft.sources.push(newSource("read", draft.sources)),
    );
    expect(hasReadSource(withReader)).toBe(true);
    expect(
      hasReadSource(
        edit(withReader, (draft) => (draft.sources[1]!.enabled = false)),
      ),
    ).toBe(false);
  });
});

describe("MCP tool allowlist", () => {
  const bound = () =>
    edit(makeSettings(), (draft) => {
      draft.mcp_servers = {
        kb: {
          transport: "http",
          url: "http://kb/mcp",
          allowed_tools: null,
        },
      };
      draft.sources[0]!.providers[0] = {
        ...newProvider("mcp", []),
        server: "kb",
        tool: "search_docs",
      };
      draft.sources.push({
        ...newSource("data", draft.sources),
        name: "kb",
        tool: "kb_lookup",
        kind: "mcp",
        providers: [],
        server: "kb",
        mcp_tool: "lookup",
      });
    });

  it("lists the MCP tool behind every source and provider", () => {
    expect(mcpBindings(bound()).map((item) => item.tool)).toEqual([
      "search_docs",
      "lookup",
    ]);
    const unnamed = edit(
      bound(),
      (draft) => (draft.sources[1]!.mcp_tool = null),
    );
    expect(mcpBindings(unnamed)[1]!.tool).toBe("kb_lookup");
  });

  it("allows everything without an allowlist and rejects tools outside one", () => {
    const view = makeSettingsView();
    expect(draftProblems(bound(), view)).toEqual([]);
    const limited = edit(
      bound(),
      (draft) => (draft.mcp_servers.kb!.allowed_tools = ["lookup"]),
    );
    const problems = draftProblems(limited, view);
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("供应商“public-web-search/mcp”");
    expect(problems[0]).toContain("“search_docs”不在服务“kb”的工具白名单中");
    const both = edit(limited, (draft) =>
      draft.mcp_servers.kb!.allowed_tools!.push("search_docs"),
    );
    expect(draftProblems(both, view)).toEqual([]);
  });
});

describe("pasted credentials", () => {
  it("accepts references and interpolation, and names literal credential values", () => {
    expect(
      literalCredentials({
        Authorization: "Bearer ${MCP_TOKEN}",
        Cookie: "sid=${secret:kb-cookie}; lang=zh",
        "X-Api-Key": "$KB_KEY",
        "X-Token": "secret:kb-token",
        Accept: "application/json",
        "X-Empty-Token": "",
      }),
    ).toEqual([]);
    expect(
      literalCredentials({
        Authorization: "Bearer sk-live-123",
        cookie: "sid=abcdef",
        Accept: "application/json",
      }),
    ).toEqual(["Authorization", "cookie"]);
  });

  it("blocks literal credentials in MCP headers, MCP env and HTTP provider headers", () => {
    const problems = draftProblems(
      edit(makeSettings(), (draft) => {
        draft.mcp_servers = {
          kb: {
            transport: "http",
            url: "http://kb/mcp",
            headers: { Authorization: "Bearer sk-live-123" },
          },
          local: {
            transport: "stdio",
            command: "uvx",
            env: { KB_PASSWORD: "hunter2", LANG: "zh_CN.UTF-8" },
          },
        };
        draft.sources[0]!.providers[0] = {
          ...newProvider("http", []),
          url: "https://api.example.com/search?q={query}",
          headers: { "X-Api-Key": "abc123" },
        };
      }),
      makeSettingsView(),
    );
    expect(problems).toEqual([
      "MCP 服务“kb”的请求头“Authorization”：请改用 $环境变量 或 secret:名字 引用，不要保存明文凭据",
      "MCP 服务“local”的环境变量“KB_PASSWORD”：请改用 $环境变量 或 secret:名字 引用，不要保存明文凭据",
      "供应商“public-web-search/http”的请求头“X-Api-Key”：请改用 $环境变量 或 secret:名字 引用，不要保存明文凭据",
    ]);
  });
});

describe("other draft rules", () => {
  it("keeps the planned step range in order", () => {
    const problems = draftProblems(
      edit(makeSettings(), (draft) => {
        draft.plan_min_units = 7;
        draft.plan_max_units = 6;
      }),
      makeSettingsView(),
    );
    expect(problems).toEqual([
      "计划的研究步骤数：最少步骤数不能大于最多步骤数",
    ]);
  });

  it("keeps the report length scale inside the server's range", () => {
    const scaled = (value: number | undefined) =>
      draftProblems(
        edit(makeSettings(), (draft) => (draft.report_length_scale = value)),
        makeSettingsView(),
      );
    expect(scaled(0.2)).toEqual([]);
    expect(scaled(0.45)).toEqual([]);
    expect(scaled(3)).toEqual([]);
    // A gateway from before the field sends nothing: the default applies.
    expect(scaled(undefined)).toEqual([]);
    for (const value of [0.1, 3.5, Number.NaN])
      expect(scaled(value)).toEqual(["报告长度系数需要在 0.2–3 之间"]);
    expect(SECTION_FIELDS.runtime).toContain("report_length_scale");
    expect(fieldLabels(["report_length_scale"])).toBe("报告长度系数");
  });

  it("a secrets answer changes the secret names and statuses, nothing else", () => {
    const mine = makeSettingsView(makeSettings(), { version: 1 });
    const theirs = makeSettingsView(
      edit(makeSettings(), (draft) => (draft.max_output_tokens = 1234)),
      {
        version: 7,
        secrets: { saved: ["k1"], references: { "secret:k1": "set" } },
      },
    );
    const merged = mergeSecrets(mine, theirs);
    expect(merged.version).toBe(1);
    expect(merged.settings).toBe(mine.settings);
    expect(merged.secrets.saved).toEqual(["k1"]);
  });
});
