import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";

import { ResearchSettings } from "@/components/deepresearch/research-settings";
import { SidebarProvider } from "@/components/ui/sidebar";
import { edit, newProvider } from "@/core/deepresearch/settings";
import type {
  EditableSettings,
  McpToolsResult,
  SettingsView,
} from "@/core/deepresearch/types";
import { I18nProvider } from "@/core/i18n/context";

import {
  makeSettings,
  makeSettingsView,
} from "../../core/deepresearch/fixtures";

const api = rs.hoisted(() => ({
  view: null as SettingsView | null,
  saved: [] as { version: number; settings: EditableSettings }[],
  saveResult: null as SettingsView | null,
  saveError: null as (Error & { status?: number; detail?: unknown }) | null,
  secretResult: null as SettingsView | null,
  restoreResult: null as SettingsView | null,
  history: [] as {
    version: number;
    updated_at: string;
    updated_by: string | null;
    fields: string[];
  }[],
  mcpTools: null as McpToolsResult | null,
}));

rs.mock("next/navigation", () => ({
  useRouter: () => ({ push: rs.fn(), replace: rs.fn(), refresh: rs.fn() }),
  usePathname: () => "/workspace/deepresearch/settings",
}));

rs.mock("@/core/deepresearch/api", () => ({
  researchApi: () => ({
    root: "/api/deepresearch",
    settings: () => Promise.resolve(api.view!),
    saveSettings: (version: number, settings: EditableSettings) => {
      api.saved.push({ version, settings });
      if (api.saveError) return Promise.reject(api.saveError);
      return Promise.resolve(
        api.saveResult ?? { ...api.view!, version: version + 1, settings },
      );
    },
    resetSettings: () => Promise.resolve(api.view!),
    restoreSettings: () => Promise.resolve(api.restoreResult ?? api.view!),
    settingsHistory: () => Promise.resolve({ items: api.history }),
    saveSecret: () => Promise.resolve(api.secretResult ?? api.view!),
    testModel: (model: { name: string }) =>
      Promise.resolve({ model: model.name, ok: true }),
    testProvider: () => Promise.resolve({ ok: true, ms: 10, count: 0 }),
    mcpTools: () => Promise.resolve(api.mcpTools ?? { ok: true, tools: [] }),
    providerHealth: () => Promise.resolve({ providers: [] }),
  }),
}));

function open(view: SettingsView = makeSettingsView()) {
  api.view = view;
  api.saved = [];
  api.saveResult = null;
  api.saveError = null;
  api.secretResult = null;
  api.restoreResult = null;
  api.history = [];
  api.mcpTools = null;
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <I18nProvider initialLocale="zh-CN">
      <SidebarProvider>
        <QueryClientProvider client={client}>
          <ResearchSettings />
        </QueryClientProvider>
      </SidebarProvider>
    </I18nProvider>,
  );
}

afterEach(cleanup);

describe("ResearchSettings", () => {
  it("saves the edited draft against the version it was loaded with", async () => {
    open();
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.click(screen.getByRole("button", { name: "运行参数" }));
    const sections = await screen.findByLabelText("同时研究的单元数");
    fireEvent.change(sections, { target: { value: "2" } });
    fireEvent.click(await screen.findByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.saved).toHaveLength(1));
    expect(api.saved[0]!.version).toBe(1);
    expect(api.saved[0]!.settings.max_concurrency).toBe(2);
    // The save bar disappears once the draft matches the saved version again.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "保存" })).toBeNull(),
    );
  });

  it("refuses to save a draft the server would reject, and lists why", async () => {
    open();
    await screen.findByRole("heading", { name: "模型" });
    const name = screen.getByLabelText("名称");
    fireEvent.change(name, { target: { value: "renamed" } });
    const save = await screen.findByRole("button", { name: "保存" });
    expect((save as HTMLButtonElement).disabled).toBe(false);
    // Renaming a model also renames every reference, so the draft stays valid.
    fireEvent.change(screen.getByLabelText("服务端模型名"), {
      target: { value: "" },
    });
    await waitFor(() =>
      expect(screen.getByText(/个问题需要处理后才能保存/)).toBeTruthy(),
    );
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "保存" }).disabled,
    ).toBe(true);
    expect(api.saved).toHaveLength(0);
  });

  it("discards edits back to the loaded settings", async () => {
    open();
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.change(screen.getByLabelText("显示名称"), {
      target: { value: "改过的名字" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "放弃修改" }));
    await waitFor(() =>
      expect(screen.getByLabelText<HTMLInputElement>("显示名称").value).toBe(
        "",
      ),
    );
  });

  it("offers to load the newer version when another admin saved first", async () => {
    open();
    await screen.findByRole("heading", { name: "模型" });
    api.saveError = Object.assign(new Error("版本冲突"), {
      status: 409,
      detail: { code: "PROFILE_VERSION", message: "设置已被其他人修改" },
    });
    fireEvent.change(screen.getByLabelText("显示名称"), {
      target: { value: "x" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "保存" }));
    expect(
      await screen.findByRole("button", { name: "载入最新版本" }),
    ).toBeTruthy();
  });

  it("is read-only for a non-administrator", async () => {
    open(makeSettingsView(makeSettings(), { editable: false }));
    expect(await screen.findByText(/只有管理员可以修改研究设置/)).toBeTruthy();
    expect(screen.getByLabelText<HTMLInputElement>("显示名称").disabled).toBe(
      true,
    );
  });

  it("shows the prompt defaults and marks an edited prompt", async () => {
    open();
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.click(screen.getByRole("button", { name: "提示词" }));
    const plan = await screen.findByLabelText("提示词 研究计划");
    const editor = within(plan).getByLabelText<HTMLTextAreaElement>("内容");
    expect(editor.value).toBe("PLAN");
    fireEvent.change(editor, { target: { value: "PLAN 改过" } });
    expect(await within(plan).findByText("已修改")).toBeTruthy();
    fireEvent.click(within(plan).getByRole("button", { name: "恢复默认" }));
    await waitFor(() =>
      expect(
        within(plan).getByLabelText<HTMLTextAreaElement>("内容").value,
      ).toBe("PLAN"),
    );
  });
});

function twoModels() {
  return edit(makeSettings(), (draft) => {
    draft.models = [
      { name: "model", provider: "deepseek", model: "m1", api_key: "$K" },
      { name: "model-2", provider: "deepseek", model: "m2", api_key: "$K" },
    ];
    draft.default_model = "model";
    draft.extraction_model = "model";
    draft.skills["technical-route"]!.model = "model-2";
    draft.pricing = {
      model: { input_per_million: 1, output_per_million: 2, currency: "CNY" },
    };
  });
}

async function save() {
  fireEvent.click(await screen.findByRole("button", { name: "保存" }));
  await waitFor(() => expect(api.saved).toHaveLength(1));
  return api.saved[0]!;
}

describe("ResearchSettings: drafts and the server version", () => {
  it("typing a model name through another model's name leaves that model's references alone", async () => {
    open(makeSettingsView(twoModels()));
    await screen.findByRole("heading", { name: "模型" });
    for (const value of ["model-", "model", "mode", "fast"])
      fireEvent.change(screen.getAllByLabelText("名称")[1]!, {
        target: { value },
      });
    const { settings } = await save();
    expect(settings.models.map((model) => model.name)).toEqual([
      "model",
      "fast",
    ]);
    expect(settings.default_model).toBe("model");
    expect(settings.extraction_model).toBe("model");
    expect(Object.keys(settings.pricing)).toEqual(["model"]);
    expect(settings.skills["technical-route"]!.model).toBe("fast");
  });

  it("names the request field and header that carry the conversation id, and clears them again", async () => {
    open(makeSettingsView(twoModels()));
    await screen.findByRole("heading", { name: "模型" });
    const params = screen.getAllByLabelText("会话标识参数（session_param）");
    const headers = screen.getAllByLabelText(
      "会话标识请求头（session_header）",
    );
    fireEvent.change(params[0]!, { target: { value: " prompt_cache_key " } });
    fireEvent.change(headers[0]!, { target: { value: "x-session-affinity" } });
    // Typed and removed again: nothing is sent, not an empty field name.
    fireEvent.change(params[1]!, { target: { value: "user" } });
    fireEvent.change(params[1]!, { target: { value: "" } });
    const { settings } = await save();
    expect(settings.models[0]).toMatchObject({
      session_param: "prompt_cache_key",
      session_header: "x-session-affinity",
    });
    expect(settings.models[1]!.session_param).toBeNull();
  });

  it("saving a secret with unsaved edits keeps the draft on the version it was based on", async () => {
    open();
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.click(screen.getByRole("button", { name: "运行参数" }));
    fireEvent.change(await screen.findByLabelText("同时研究的单元数"), {
      target: { value: "2" },
    });
    // Another administrator saved version 7 in the meantime.
    api.secretResult = makeSettingsView(
      edit(makeSettings(), (draft) => (draft.max_output_tokens = 1234)),
      { version: 7, secrets: { saved: ["k1"], references: {} } },
    );
    fireEvent.click(screen.getByRole("button", { name: /密钥与历史/ }));
    fireEvent.change(await screen.findByLabelText("密钥名字"), {
      target: { value: "k1" },
    });
    fireEvent.change(screen.getByLabelText("密钥值"), {
      target: { value: "v" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存密钥" }));
    expect(await screen.findByText("secret:k1")).toBeTruthy();
    expect(screen.getByText(/当前第 1 版/)).toBeTruthy();
    // The save still names version 1, so the server can answer 409.
    const saved = await save();
    expect(saved.version).toBe(1);
    expect(saved.settings.max_concurrency).toBe(2);
  });

  it("restoring a version replaces the form, including an edit that was typed back", async () => {
    open();
    api.history = [
      {
        version: 0,
        updated_at: "2026-09-01T00:00:00Z",
        updated_by: "admin",
        fields: ["max_concurrency"],
      },
    ];
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.click(screen.getByRole("button", { name: "运行参数" }));
    const input = await screen.findByLabelText("同时研究的单元数");
    fireEvent.change(input, { target: { value: "2" } });
    fireEvent.change(input, { target: { value: "3" } });
    expect(screen.queryByRole("button", { name: "保存" })).toBeNull();
    api.restoreResult = makeSettingsView(
      edit(makeSettings(), (draft) => (draft.max_concurrency = 5)),
      { version: 2 },
    );
    fireEvent.click(screen.getByRole("button", { name: /密钥与历史/ }));
    fireEvent.click(await screen.findByRole("button", { name: "恢复此版本" }));
    expect(await screen.findByText(/当前第 2 版/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "保存" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /运行参数/ }));
    expect(
      (await screen.findByLabelText<HTMLInputElement>("同时研究的单元数"))
        .value,
    ).toBe("5");
  });

  it("a test result stays on its own card when an earlier card is removed", async () => {
    open(makeSettingsView(twoModels()));
    await screen.findByRole("heading", { name: "模型" });
    const second = screen.getByLabelText("模型 model-2");
    fireEvent.click(within(second).getByRole("button", { name: "测试连接" }));
    expect(await within(second).findByText("可以用于研究")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "删除模型 model" }));
    await waitFor(() =>
      expect(screen.queryByLabelText("模型 model")).toBeNull(),
    );
    expect(
      within(screen.getByLabelText("模型 model-2")).getByText("可以用于研究"),
    ).toBeTruthy();
  });
});

describe("ResearchSettings: input the draft cannot hold", () => {
  it("refuses to save while a number field shows an out-of-range value", async () => {
    open();
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.click(screen.getByRole("button", { name: "运行参数" }));
    fireEvent.change(await screen.findByLabelText("报告最多章节数"), {
      target: { value: "6" },
    });
    const units = screen.getByLabelText<HTMLInputElement>("同时研究的单元数");
    fireEvent.change(units, { target: { value: "99" } });
    fireEvent.blur(units);
    // The text stays, so what is on screen is what blocks the save.
    expect(units.value).toBe("99");
    const button = await screen.findByRole<HTMLButtonElement>("button", {
      name: "保存",
    });
    await waitFor(() => expect(button.disabled).toBe(true));
    expect(screen.getByText("同时研究的单元数：范围 1 – 8")).toBeTruthy();
    fireEvent.change(units, { target: { value: "2.5" } });
    expect(
      await screen.findByText("同时研究的单元数：请填写整数"),
    ).toBeTruthy();
    fireEvent.change(units, { target: { value: "4" } });
    await waitFor(() => expect(button.disabled).toBe(false));
    const saved = await save();
    expect(saved.settings.max_concurrency).toBe(4);
    expect(saved.settings.max_report_sections).toBe(6);
  });

  it("edits the report length scale as a decimal inside 0.2–3", async () => {
    open();
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.click(screen.getByRole("button", { name: "运行参数" }));
    const scale =
      await screen.findByLabelText<HTMLInputElement>("报告长度系数");
    expect(scale.value).toBe("1");
    fireEvent.change(scale, { target: { value: "0.45" } });
    const button = await screen.findByRole<HTMLButtonElement>("button", {
      name: "保存",
    });
    await waitFor(() => expect(button.disabled).toBe(false));
    // Out of the server's range: the text stays and blocks the save.
    fireEvent.change(scale, { target: { value: "5" } });
    expect(await screen.findByText("报告长度系数：范围 0.2 – 3")).toBeTruthy();
    await waitFor(() => expect(button.disabled).toBe(true));
    fireEvent.change(scale, { target: { value: "0.5" } });
    await waitFor(() => expect(button.disabled).toBe(false));
    const saved = await save();
    expect(saved.settings.report_length_scale).toBe(0.5);
  });

  it("refuses to save while a JSON field does not parse, and forgets it on discard", async () => {
    open();
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.change(screen.getByLabelText("显示名称"), {
      target: { value: "改过" },
    });
    fireEvent.change(screen.getByLabelText("额外参数（extra）"), {
      target: { value: "{ not json" },
    });
    const button = await screen.findByRole<HTMLButtonElement>("button", {
      name: "保存",
    });
    await waitFor(() => expect(button.disabled).toBe(true));
    expect(
      screen.getByText(/模型 flash · 额外参数（extra）：JSON 格式不正确/),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLTextAreaElement>("额外参数（extra）").value,
      ).toBe("{}"),
    );
  });
});

describe("ResearchSettings: node tuning", () => {
  it("writes a tuned node and removes it again when everything inherits", async () => {
    open();
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.click(screen.getByRole("button", { name: "节点调参" }));
    const plan = await screen.findByLabelText("节点 研究计划");
    expect(within(plan).getByText("全部继承")).toBeTruthy();
    expect(
      within(plan).getByText("未指定模型时使用：规划角色模型 → 默认模型"),
    ).toBeTruthy();
    // Only the two optional nodes can be switched off; JSON mode is a direct-call switch.
    expect(within(plan).queryByLabelText("启用节点 研究计划")).toBeNull();
    expect(within(plan).queryByLabelText(/JSON 模式/)).toBeNull();
    const rewrite = screen.getByLabelText("节点 请求改写");
    expect(within(rewrite).getByLabelText("启用节点 请求改写")).toBeTruthy();
    expect(within(rewrite).getByLabelText(/JSON 模式/)).toBeTruthy();

    fireEvent.change(within(plan).getByLabelText("温度"), {
      target: { value: "0.2" },
    });
    expect(await within(plan).findByText("已调整")).toBeTruthy();
    fireEvent.change(within(plan).getByLabelText("温度"), {
      target: { value: "" },
    });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "保存" })).toBeNull(),
    );

    fireEvent.change(within(plan).getByLabelText("温度"), {
      target: { value: "0.1" },
    });
    const saved = await save();
    expect(Object.keys(saved.settings.nodes)).toEqual(["plan"]);
    expect(saved.settings.nodes.plan).toMatchObject({
      enabled: true,
      model: null,
      temperature: 0.1,
      json_mode: false,
    });
  });
});

describe("ResearchSettings: sources and MCP servers", () => {
  it("switches a source off, explains it, and requires an enabled source", async () => {
    open();
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.click(screen.getByRole("button", { name: "数据源与搜索" }));
    expect(
      await screen.findByText(/当前没有可以打开原文的读取工具/),
    ).toBeTruthy();
    fireEvent.click(screen.getByLabelText("启用数据源 public-web-search"));
    expect(
      await screen.findByText("已停用（不会提供给规划与研究员）"),
    ).toBeTruthy();
    const button = await screen.findByRole<HTMLButtonElement>("button", {
      name: "保存",
    });
    expect(button.disabled).toBe(true);
    expect(screen.getByText("真实研究至少需要一个启用的数据源")).toBeTruthy();
  });

  function withMcpServer() {
    return makeSettingsView(
      edit(makeSettings(), (draft) => {
        draft.mcp_servers = {
          kb: {
            transport: "http",
            url: "http://kb/mcp",
            headers: { Cookie: "sid=${secret:kb-cookie}" },
            allowed_tools: null,
          },
        };
        draft.sources[0]!.providers[0] = {
          ...newProvider("mcp", []),
          server: "kb",
          tool: "search_docs",
        };
      }),
    );
  }

  it("turns the listed tools into an allowlist and blocks a tool in use that is left out", async () => {
    open(withMcpServer());
    api.mcpTools = {
      ok: true,
      tools: [
        { name: "search_docs", description: "", arguments: {}, allowed: true },
        { name: "delete_doc", description: "", arguments: {}, allowed: true },
      ],
    };
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.click(screen.getByRole("button", { name: "MCP 服务" }));
    expect(
      await screen.findByText(/未限制：数据源\/供应商可以引用/),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "连接并列出工具" }));
    const tool =
      await screen.findByLabelText<HTMLInputElement>("允许工具 delete_doc");
    expect(tool.disabled).toBe(true);
    expect(screen.getByText("使用中")).toBeTruthy();

    // Switching the allowlist on starts from what research already calls.
    fireEvent.click(screen.getByLabelText("只允许列出的工具"));
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLInputElement>("允许工具 search_docs").checked,
      ).toBe(true),
    );
    expect(
      screen.getByLabelText<HTMLInputElement>("允许工具 delete_doc").checked,
    ).toBe(false);

    fireEvent.click(screen.getByLabelText("允许工具 search_docs"));
    expect(
      await screen.findByText(/正在使用但不在白名单中：search_docs/),
    ).toBeTruthy();
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "保存" }).disabled,
    ).toBe(true);
    fireEvent.click(screen.getByLabelText("允许工具 search_docs"));
    fireEvent.click(screen.getByLabelText("允许工具 delete_doc"));
    const saved = await save();
    expect(saved.settings.mcp_servers.kb!.allowed_tools).toEqual([
      "search_docs",
      "delete_doc",
    ]);
  });

  it("says why the MCP server could not be reached", async () => {
    open(withMcpServer());
    api.mcpTools = {
      ok: false,
      error: "ExceptionGroup",
      kind: "auth",
      message:
        "MCP server kb: HTTP 401, the server rejected the credentials; check its headers, cookie or token",
      tools: [],
    };
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.click(screen.getByRole("button", { name: "MCP 服务" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "连接并列出工具" }),
    );
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("鉴权失败")).toBeTruthy();
    expect(within(alert).getByText(/HTTP 401/)).toBeTruthy();
  });

  it("renames an MCP server on blur together with the provider bound to it", async () => {
    open(withMcpServer());
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.click(screen.getByRole("button", { name: "MCP 服务" }));
    const name = await screen.findByLabelText<HTMLInputElement>("服务名称", {
      selector: "#mcp-name-kb",
    });
    fireEvent.change(name, { target: { value: "docs" } });
    fireEvent.blur(name);
    const saved = await save();
    expect(Object.keys(saved.settings.mcp_servers)).toEqual(["docs"]);
    expect(saved.settings.sources[0]!.providers[0]!.server).toBe("docs");
  });

  it("refuses a pasted credential in MCP headers", async () => {
    open(withMcpServer());
    await screen.findByRole("heading", { name: "模型" });
    fireEvent.click(screen.getByRole("button", { name: "MCP 服务" }));
    fireEvent.change(await screen.findByLabelText("请求头（headers）"), {
      target: { value: '{"Authorization": "Bearer sk-live-123"}' },
    });
    const button = await screen.findByRole<HTMLButtonElement>("button", {
      name: "保存",
    });
    expect(button.disabled).toBe(true);
    expect(
      screen.getByText(
        /请求头“Authorization”：请改用 \$环境变量 或 secret:名字 引用/,
      ),
    ).toBeTruthy();
  });
});
