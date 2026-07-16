/**
 * 真实后端登录端到端测试（非测试模式固定验证码）
 *
 * 流程：拦截 /auth/captcha 响应取 captcha_id → 从 linchat-redis 读验证码明文
 * → 填表登录 → 断言跳转 /linchat/chat 且显示用户名 → 截图存证（附加到报告）。
 *
 * 前置：本机可 docker exec linchat-redis；前后端服务运行中。
 * 运行：npx playwright test tests/e2e/login-real.spec.ts --project=chromium
 */
import { execSync } from 'child_process';
import { test, expect } from '@playwright/test';

test.use({ channel: 'chrome' }); // 用系统 Chrome，免下载 playwright 浏览器

const USER = process.env.LINCHAT_USER || 'dantsinghua';
const PASS = process.env.LINCHAT_PASS || '!9871229Qing';

test('真实登录跳转聊天页并截图确认', async ({ page }, testInfo) => {
  test.setTimeout(120_000);

  // 页面加载即拉取验证码：拦截器必须先于导航装好，避免点击竞态
  const captchaResp = page.waitForResponse(
    r => r.url().includes('/auth/captcha') && r.status() === 200,
    { timeout: 60_000 }
  );
  await page.goto('/linchat/login', { timeout: 60_000 });
  const captchaId = (await (await captchaResp).json()).data.captcha_id;

  // 验证码明文在 Redis，120 秒时效
  const code = execSync(
    `docker exec linchat-redis redis-cli -a redis_linchat_123 GET "auth:captcha:${captchaId}" 2>/dev/null`
  ).toString().trim();
  expect(code, 'Redis 中应存在 4 位验证码').toMatch(/^[A-Za-z0-9]{4}$/);

  await page.getByRole('textbox', { name: '用户名' }).fill(USER);
  await page.getByRole('textbox', { name: '密码' }).fill(PASS);
  await page.getByRole('textbox', { name: '验证码' }).fill(code);
  await page.getByRole('button', { name: '登录' }).click();

  // 成功标志：URL 变为 /linchat/chat 且页面显示用户名
  await page.waitForURL('**/linchat/chat', { timeout: 30000 });
  await expect(page.getByText(USER).first()).toBeVisible({ timeout: 10000 });

  // 截图存证并附加到测试报告
  const shot = testInfo.outputPath('login-success.png');
  await page.screenshot({ path: shot });
  await testInfo.attach('login-success', { path: shot, contentType: 'image/png' });
});
