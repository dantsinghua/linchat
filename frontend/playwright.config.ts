import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright 端到端测试配置
 */
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    // 使用环境变量或默认值，支持本地和生产环境
    baseURL: process.env.BASE_URL || 'http://localhost:3784',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
  ],
  webServer: {
    command: 'npm run build && npm run start -- -p 3784',
    // basePath 是 /linchat，根路径 404 会被判定为"服务未就绪"而误触发 build
    url: 'http://localhost:3784/linchat/login',
    reuseExistingServer: !process.env.CI,
    timeout: 120000, // 构建可能需要较长时间
  },
});
