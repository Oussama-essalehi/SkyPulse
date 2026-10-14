import { defineConfig } from "@playwright/test";

/**
 * Browser tests against a running stack (CI: replay mode; locally: `make up-demo`).
 *   SKYRADAR_URL=http://localhost:3000 npm run test:e2e
 */
export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.e2e.ts",
  timeout: 90_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.SKYRADAR_URL ?? "http://localhost:3000",
    viewport: { width: 1440, height: 900 },
    timezoneId: "Europe/Paris",
    locale: "fr-FR",
    colorScheme: "dark", // the site follows the system theme on a first visit
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    // Behind a corporate / sandbox proxy (map tiles), keep the stack itself direct.
    proxy: process.env.HTTPS_PROXY
      ? { server: process.env.HTTPS_PROXY, bypass: "localhost,127.0.0.1,192.0.2.2" }
      : undefined,
    launchOptions: {
      // WebGL without a GPU (CI runners, containers): software rendering.
      args: [
        "--use-gl=swiftshader",
        "--enable-webgl",
        "--ignore-gpu-blocklist",
        "--enable-unsafe-swiftshader",
      ],
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
    },
  },
});
