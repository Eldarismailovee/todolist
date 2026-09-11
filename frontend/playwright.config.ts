import { defineConfig, devices } from '@playwright/test';

/**
 * Сквозной сценарий гоняется против настоящих бэкенда и Vite.
 * Браузеры Playwright не скачиваются: используется системный Chromium
 * (`CHROMIUM_PATH`), поэтому CI и локальный запуск одинаковы.
 */
const CHROMIUM = process.env.CHROMIUM_PATH ?? '/usr/bin/chromium';

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], launchOptions: { executablePath: CHROMIUM } },
    },
  ],
});
