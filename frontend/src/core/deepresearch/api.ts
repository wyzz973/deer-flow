import type { Capabilities, Run } from "./types";

// Defaults to the same-origin authenticated DeerFlow gateway.
// The separate demo page explicitly passes http://127.0.0.1:8022.
export function researchApi(base = "") {
  const root = `${base.replace(/\/$/, "")}/api/deepresearch`;
  async function response(path: string, body?: unknown, extraHeaders?: Record<string, string>) {
    const csrf = typeof document === "undefined" ? undefined : document.cookie.split("; ").find((p) => p.startsWith("csrf_token="))?.slice(11);
    const headers: Record<string, string> = { ...extraHeaders };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      if (!base && csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf);
    }
    const res = await fetch(root + path, { method: body === undefined ? "GET" : "POST", credentials: "include", headers, body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store" });
    if (!res.ok) {
      const error = await res.json().catch(() => null) as { detail?: unknown } | null;
      const detail = error?.detail;
      const message = typeof detail === "string" ? detail : detail && typeof detail === "object" && "message" in detail ? String(detail.message) : `请求失败 (${res.status})`;
      throw new Error(res.status === 401 ? "请先登录 DeerFlow，再打开研究工作台。" : message);
    }
    return res;
  }
  async function json<T>(path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
    return await (await response(path, body, headers)).json() as T;
  }
  return {
    root,
    capabilities: () => json<Capabilities>("/capabilities"),
    list: () => json<Run[]>(""),
    get: (id: string) => json<Run>(`/${encodeURIComponent(id)}`),
    create: (body: unknown, key: string) => json<Run>("", body, { "Idempotency-Key": key }),
    action: (id: string, action: string, body: unknown = {}) => json<Run>(`/${encodeURIComponent(id)}/${action}`, body),
    async download(id: string, format: "md" | "html" | "docx") {
      const res = await response(`/${encodeURIComponent(id)}/report?format=${format}`);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a"); a.href = url; a.download = `research-${id}.${format}`; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  };
}
