/**
 * 登录到聊天端到端测试
 *
 * 测试场景:
 * - SC-007 认证拦截验证：未登录访问聊天页自动跳转登录页
 * - SC-008 消息持久化验证：发送消息后刷新页面，验证消息仍存在
 * - SC-009 用户数据隔离验证：用户A发送消息，用户B登录后验证看不到用户A的消息
 * - SC-001 登录流程耗时测试：< 30秒
 *
 * 注意：运行此测试需要完整的后端服务（PostgreSQL、Redis、Django）
 */
import { test, expect, type Page } from '@playwright/test';

// 测试配置
const BASE_URL = process.env.BASE_URL || 'http://localhost:3784';
const TEST_USER_A = { username: 'admin', password: '!9871229Qing' };
const TEST_USER_B = { username: 'testuser', password: 'testpass123' };

/**
 * 工具函数：登录
 */
async function login(page: Page, username: string, password: string) {
  await page.goto(`${BASE_URL}/linchat/login`);

  // 等待验证码加载
  await page.waitForSelector('img[alt="验证码"]', { timeout: 10000 });

  // 填写登录表单
  await page.fill('input[name="username"], [aria-label*="用户名"]', username);
  await page.fill('input[name="password"], input[type="password"]', password);

  // 获取验证码（需要手动输入或使用测试模式的固定验证码）
  // 在测试环境中，可以配置后端接受固定验证码如 "1234"
  await page.fill('input[name="captcha"], [aria-label*="验证码"]', '1234');

  // 点击登录
  await page.click('button[type="submit"], button:has-text("登录")');

  // 等待跳转到聊天页
  await page.waitForURL(`${BASE_URL}/linchat/chat`, { timeout: 30000 });
}

/**
 * 工具函数：登出
 */
async function logout(page: Page) {
  // 点击登出按钮（如果存在）
  const logoutBtn = page.locator('button:has-text("退出"), button:has-text("登出")');
  if (await logoutBtn.isVisible()) {
    await logoutBtn.click();
    await page.waitForURL(`${BASE_URL}/linchat/login`, { timeout: 10000 });
  }
}

/**
 * 工具函数：发送消息
 */
async function sendMessage(page: Page, content: string) {
  // 填写消息
  await page.fill('textarea, input[placeholder*="消息"]', content);

  // 发送
  await page.click('button[title="发送消息"], button:has-text("发送")');

  // 等待消息显示
  await page.waitForSelector(`text=${content}`, { timeout: 30000 });
}

test.describe('SC-007 认证拦截验证', () => {
  test('未登录访问聊天页应自动跳转登录页', async ({ page }) => {
    // 直接访问聊天页
    await page.goto(`${BASE_URL}/linchat/chat`);

    // 应该被重定向到登录页
    await expect(page).toHaveURL(/.*login/, { timeout: 10000 });
  });

  test('未登录访问根路径应跳转登录页', async ({ page }) => {
    await page.goto(`${BASE_URL}/linchat/`);
    await expect(page).toHaveURL(/.*login/, { timeout: 10000 });
  });
});

test.describe('SC-001 登录流程耗时测试', () => {
  test('登录流程应在30秒内完成', async ({ page }) => {
    const startTime = Date.now();

    await page.goto(`${BASE_URL}/linchat/login`);

    // 等待页面加载
    await page.waitForSelector('img[alt="验证码"]', { timeout: 10000 });

    // 记录开始登录的时间点
    const loginStartTime = Date.now();

    // 填写登录表单
    await page.fill('input[name="username"], [aria-label*="用户名"]', TEST_USER_A.username);
    await page.fill('input[name="password"], input[type="password"]', TEST_USER_A.password);
    await page.fill('input[name="captcha"], [aria-label*="验证码"]', '1234');

    // 点击登录
    await page.click('button[type="submit"], button:has-text("登录")');

    // 等待跳转到聊天页
    await page.waitForURL(`${BASE_URL}/linchat/chat`, { timeout: 30000 });

    const endTime = Date.now();
    const totalTime = endTime - startTime;
    const loginTime = endTime - loginStartTime;

    console.log(`总耗时: ${totalTime}ms, 登录操作耗时: ${loginTime}ms`);

    // SC-001: 登录流程完成时间 < 30秒
    expect(loginTime).toBeLessThan(30000);
  });
});

test.describe('SC-008 消息持久化验证', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, TEST_USER_A.username, TEST_USER_A.password);
  });

  test.afterEach(async ({ page }) => {
    await logout(page);
  });

  test('发送消息后刷新页面，消息应仍存在', async ({ page }) => {
    // 生成唯一消息内容
    const uniqueMessage = `测试消息_${Date.now()}`;

    // 发送消息
    await sendMessage(page, uniqueMessage);

    // 等待 AI 响应开始
    await page.waitForTimeout(2000);

    // 刷新页面
    await page.reload();

    // 等待页面加载
    await page.waitForSelector('.message-list, [data-testid="message-list"]', {
      timeout: 10000,
    });

    // 验证消息仍存在
    const messageElement = page.locator(`text=${uniqueMessage}`);
    await expect(messageElement).toBeVisible({ timeout: 10000 });
  });
});

test.describe('SC-009 用户数据隔离验证', () => {
  test.skip('用户A的消息对用户B不可见', async ({ browser }) => {
    // 注意：此测试需要两个用户账号，跳过如果只有 admin 账号

    // 创建两个独立的浏览器上下文
    const contextA = await browser.newContext();
    const contextB = await browser.newContext();

    const pageA = await contextA.newPage();
    const pageB = await contextB.newPage();

    try {
      // 用户 A 登录并发送消息
      await login(pageA, TEST_USER_A.username, TEST_USER_A.password);
      const uniqueMessage = `用户A的秘密消息_${Date.now()}`;
      await sendMessage(pageA, uniqueMessage);

      // 用户 B 登录
      await login(pageB, TEST_USER_B.username, TEST_USER_B.password);

      // 等待页面加载
      await pageB.waitForSelector('.message-list, [data-testid="message-list"]', {
        timeout: 10000,
      });

      // 验证用户 B 看不到用户 A 的消息
      const messageElement = pageB.locator(`text=${uniqueMessage}`);
      await expect(messageElement).not.toBeVisible({ timeout: 5000 });
    } finally {
      await contextA.close();
      await contextB.close();
    }
  });
});

test.describe('登录页面功能测试', () => {
  test('登录页面应正确渲染', async ({ page }) => {
    await page.goto(`${BASE_URL}/linchat/login`);

    // 验证页面元素
    await expect(page.locator('input[name="username"], [aria-label*="用户名"]')).toBeVisible();
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.locator('img[alt="验证码"]')).toBeVisible();
    await expect(page.locator('button[type="submit"], button:has-text("登录")')).toBeVisible();
  });

  test('空用户名应显示错误提示', async ({ page }) => {
    await page.goto(`${BASE_URL}/linchat/login`);

    // 只填写密码
    await page.fill('input[type="password"]', 'password123');
    await page.fill('input[name="captcha"], [aria-label*="验证码"]', '1234');

    // 点击登录
    await page.click('button[type="submit"], button:has-text("登录")');

    // 应该显示错误提示
    await expect(page.locator('text=请输入用户名')).toBeVisible({ timeout: 5000 });
  });

  test('点击验证码图片应刷新验证码', async ({ page }) => {
    await page.goto(`${BASE_URL}/linchat/login`);

    // 等待验证码加载
    const captchaImg = page.locator('img[alt="验证码"]');
    await expect(captchaImg).toBeVisible();

    // 获取初始验证码图片 src
    const initialSrc = await captchaImg.getAttribute('src');

    // 点击刷新
    await captchaImg.click();

    // 等待新验证码加载
    await page.waitForTimeout(500);

    // 验证码应该已更新（src 不同）
    const newSrc = await captchaImg.getAttribute('src');
    expect(newSrc).not.toBe(initialSrc);
  });
});

test.describe('聊天页面功能测试', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, TEST_USER_A.username, TEST_USER_A.password);
  });

  test.afterEach(async ({ page }) => {
    await logout(page);
  });

  test('聊天页面应正确渲染', async ({ page }) => {
    // 验证聊天界面元素
    await expect(page.locator('textarea, input[placeholder*="消息"]')).toBeVisible();
    await expect(page.locator('button[title="发送消息"], button:has-text("发送")')).toBeVisible();
  });

  test('空消息不应发送', async ({ page }) => {
    // 直接点击发送（不输入内容）
    const sendBtn = page.locator('button[title="发送消息"], button:has-text("发送")');

    // 发送按钮应该被禁用
    await expect(sendBtn).toBeDisabled();
  });

  test('超长消息应显示错误提示', async ({ page }) => {
    // 输入超过 4000 字符的消息
    const longMessage = 'a'.repeat(4001);
    await page.fill('textarea, input[placeholder*="消息"]', longMessage);

    // 应该显示超出限制的提示
    await expect(page.locator('text=超出字符限制')).toBeVisible();

    // 发送按钮应该被禁用
    const sendBtn = page.locator('button[title="发送消息"], button:has-text("发送")');
    await expect(sendBtn).toBeDisabled();
  });
});
