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
import type { EditableSettings, SettingsView } from "@/core/deepresearch/types";
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
    restoreSettings: () => Promise.resolve(api.view!),
    settingsHistory: () => Promise.resolve({ items: [] }),
    saveSecret: () => Promise.resolve(api.view!),
    testModel: () => Promise.resolve({ model: "flash", ok: true }),
    testProvider: () => Promise.resolve({ ok: true, ms: 10, count: 0 }),
    mcpTools: () => Promise.resolve({ ok: true, tools: [] }),
    providerHealth: () => Promise.resolve({ providers: [] }),
  }),
}));

function open(view: SettingsView = makeSettingsView()) {
  api.view = view;
  api.saved = [];
  api.saveResult = null;
  api.saveError = null;
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
