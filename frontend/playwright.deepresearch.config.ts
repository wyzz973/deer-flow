import { defineConfig } from "@playwright/test";

const frontendPort = process.env.DEEPRESEARCH_E2E_FRONTEND_PORT ?? "3000";
const frontendUrl = `http://127.0.0.1:${frontendPort}`;

export default defineConfig({
  testDir: "./tests/deepresearch",
  timeout: 60_000,
  use: { baseURL: frontendUrl, trace: "retain-on-failure" },
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-deepresearch-report", open: "never" }],
  ],
  webServer: [
    {
      command:
        "python -m uvicorn deepresearch.demo:app --app-dir ../backend --host 127.0.0.1 --port 8022",
      url: "http://127.0.0.1:8022/docs",
      timeout: 60_000,
      reuseExistingServer: false,
      env: {
        DEEPRESEARCH_DEMO_DATA_DIR: ".deerflow/deepresearch/e2e",
        DEEPRESEARCH_DEMO_FRONTEND_PORT: frontendPort,
      },
    },
    {
      command: `python ../scripts/pnpm.py exec next dev --hostname 127.0.0.1 --port ${frontendPort}`,
      url: `${frontendUrl}/deepresearch-demo`,
      timeout: 180_000,
      reuseExistingServer: process.env.DEEPRESEARCH_E2E_REUSE_FRONTEND === "1",
      env: {
        SKIP_ENV_VALIDATION: "1",
        DEER_FLOW_AUTH_DISABLED: "1",
        DEER_FLOW_ENV: "development",
        NEXT_PUBLIC_BACKEND_BASE_URL: "http://127.0.0.1:8022",
      },
    },
  ],
});
