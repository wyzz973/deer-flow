import { fetch as gatewayFetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  Capabilities,
  EditableSettings,
  LlmCallDetail,
  LlmCallSummary,
  McpServerSpec,
  ModelProbe,
  ModelSpec,
  ProviderHealth,
  ProviderProbe,
  ResearchActivity,
  ResearchEvent,
  ResearchMetrics,
  ResearchSources,
  Run,
  SettingsView,
  SourceSpec,
} from "./types";

/** Site icon for a cited domain. The gateway fetches and caches it, so the
 * browser never contacts the cited site just to render a source list. */
export function faviconUrl(domain: string) {
  return `${getBackendBaseURL().replace(/\/$/, "")}/api/deepresearch/favicon?domain=${encodeURIComponent(domain)}`;
}

// Defaults to the same-origin authenticated DeerFlow gateway.
// The separate demo page explicitly passes http://127.0.0.1:8022.
export function researchApi(base = "") {
  const root = `${(base || getBackendBaseURL()).replace(/\/$/, "")}/api/deepresearch`;
  async function response(
    path: string,
    body?: unknown,
    extraHeaders?: Record<string, string>,
  ) {
    const headers: Record<string, string> = { ...extraHeaders };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    // Production shares the host's authentication, CSRF and login redirect.
    // The explicit loopback demo must not forward the host CSRF token.
    const request = base ? globalThis.fetch : gatewayFetch;
    const res = await request(root + path, {
      method: body === undefined ? "GET" : "POST",
      credentials: "include",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
    if (res.status === 404 && path === "/capabilities") {
      throw new Error(
        "此 DeerFlow 尚未启用 DeepResearch。请启用研究扩展并重启服务，然后刷新此页面。",
      );
    }
    if (!res.ok) {
      const error = (await res.json().catch(() => null)) as {
        detail?: unknown;
      } | null;
      const detail = error?.detail;
      const message =
        typeof detail === "string"
          ? detail
          : detail && typeof detail === "object" && "message" in detail
            ? String(detail.message)
            : `请求失败 (${res.status})`;
      const failure = new Error(
        res.status === 401 ? "请先登录 DeerFlow，再打开研究工作台。" : message,
      ) as Error & { status?: number; detail?: unknown };
      failure.status = res.status;
      failure.detail = detail;
      throw failure;
    }
    return res;
  }
  async function json<T>(
    path: string,
    body?: unknown,
    headers?: Record<string, string>,
  ): Promise<T> {
    return (await (await response(path, body, headers)).json()) as T;
  }
  async function snapshot(
    path: string,
    body?: unknown,
    headers?: Record<string, string>,
  ): Promise<Run> {
    const value = await json<Run>(path, body, headers);
    // Anchor to receipt, not component mount: cached plans may be remounted
    // long after this response without acquiring a fresh countdown interval.
    return { ...value, client_received_at: performance.now() };
  }
  return {
    root,
    capabilities: () => json<Capabilities>("/capabilities"),
    list: () => json<Run[]>(""),
    get: (id: string) => snapshot(`/${encodeURIComponent(id)}`),
    sources: (id: string) =>
      json<ResearchSources>(`/${encodeURIComponent(id)}/sources`),
    activity: (id: string) =>
      json<ResearchActivity>(`/${encodeURIComponent(id)}/activity`),
    message: (id: string, text: string, messageId: string, version?: number) =>
      snapshot(`/${encodeURIComponent(id)}/messages`, {
        text,
        client_message_id: messageId,
        plan_version: version,
      }),
    trace: (id: string, after = 0) =>
      json<{ items: ResearchEvent[]; next_cursor: number }>(
        `/${encodeURIComponent(id)}/trace?after=${after}`,
      ),
    async downloadTrace(id: string) {
      const res = await response(`/${encodeURIComponent(id)}/trace/export`);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `research-${id}-trace.jsonl`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
    metrics: (id: string) =>
      json<ResearchMetrics>(`/${encodeURIComponent(id)}/metrics`),
    llmCalls: (id: string) =>
      json<{ items: LlmCallSummary[] }>(`/${encodeURIComponent(id)}/llm-calls`),
    llmCall: (id: string, callId: string) =>
      json<LlmCallDetail>(
        `/${encodeURIComponent(id)}/llm-calls/${encodeURIComponent(callId)}`,
      ),
    async downloadLlmCalls(id: string) {
      const res = await response(`/${encodeURIComponent(id)}/llm-calls/export`);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `research-${id}-llm-calls.jsonl`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
    settings: () => json<SettingsView>("/settings"),
    saveSettings: (version: number, settings: EditableSettings) =>
      json<SettingsView>("/settings", { version, settings }),
    resetSettings: (version: number, fields?: string[]) =>
      json<SettingsView>("/settings/reset", { version, fields }),
    restoreSettings: (version: number, targetVersion: number) =>
      json<SettingsView>("/settings/restore", {
        version,
        target_version: targetVersion,
      }),
    settingsHistory: () =>
      json<{
        items: {
          version: number;
          updated_at: string;
          updated_by: string | null;
          fields: string[];
        }[];
      }>("/settings/history"),
    saveSecret: (name: string, value: string | null) =>
      json<SettingsView>("/settings/secrets", { name, value }),
    testModel: (model: ModelSpec) =>
      json<ModelProbe>("/settings/test-model", { model }),
    testProvider: (
      source: SourceSpec,
      providerId: string,
      probe: { query?: string; url?: string },
      mcpServers?: Record<string, McpServerSpec>,
    ) =>
      json<ProviderProbe>("/settings/test-provider", {
        source,
        provider_id: providerId,
        ...probe,
        mcp_servers: mcpServers,
      }),
    mcpTools: (name: string, server: McpServerSpec) =>
      json<{
        ok: boolean;
        error?: string;
        tools: {
          name: string;
          description: string;
          arguments: Record<string, unknown>;
        }[];
      }>("/settings/mcp-tools", { name, server }),
    providerHealth: () =>
      json<{ providers: ProviderHealth[] }>("/settings/health"),
    async downloadMetrics(id: string) {
      const res = await response(`/${encodeURIComponent(id)}/metrics/export`);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `research-${id}-metrics.jsonl`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
    create: (body: unknown, key: string) =>
      snapshot("", body, { "Idempotency-Key": key }),
    action: (id: string, action: string, body: unknown = {}) =>
      snapshot(`/${encodeURIComponent(id)}/${action}`, body),
    async download(
      id: string,
      format: "md" | "html" | "docx",
      version?: number,
    ) {
      const res = await response(
        `/${encodeURIComponent(id)}/report?format=${format}${version === undefined ? "" : `&version=${version}`}`,
      );
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `research-${id}.${format}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  };
}
