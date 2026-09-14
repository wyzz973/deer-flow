import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/deepresearch",
  timeout: 60_000,
  use: { baseURL: "http://127.0.0.1:3000", trace: "retain-on-failure" },
  reporter: [["list"], ["html", { outputFolder: "playwright-deepresearch-report", open: "never" }]],
  webServer: [
    { command: "python -m uvicorn deepresearch.demo:app --app-dir ../backend --host 127.0.0.1 --port 8022", url: "http://127.0.0.1:8022/docs", timeout: 60_000, reuseExistingServer: false },
    { command: "pnpm exec next dev --hostname 127.0.0.1 --port 3000", url: "http://127.0.0.1:3000/deepresearch-demo", timeout: 180_000, reuseExistingServer: false, env: { SKIP_ENV_VALIDATION: "1" } },
  ],
});
