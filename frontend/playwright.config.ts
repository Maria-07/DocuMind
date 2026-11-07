import { defineConfig, devices } from "@playwright/test";

// End-to-end tests against a running stack: Next.js (E2E_BASE_URL, default :3000) proxying to the
// FastAPI backend, with the ingestion worker, PostgreSQL, Redis, Qdrant and an LLM configured.
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 240_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
});
