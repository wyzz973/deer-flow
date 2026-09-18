import { describe, expect, it } from "@rstest/core";

import {
  allowlistChoices,
  changedSections,
  draftProblems,
  edit,
  fieldLabels,
  newProvider,
  newRole,
  newSource,
  providerTypesFor,
  referenceProblem,
  sameValue,
  uniqueName,
} from "@/core/deepresearch/settings";

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
    expect(dual.join("\n")).toContain("内部和外部数据源");
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
