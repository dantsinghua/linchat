#!/usr/bin/env python3
"""
LinChat Playwright 自动登录脚本

用途：
- 从 Redis 缓存中获取验证码（而非通过 OCR 识别）
- 用于自动化测试时的登录流程

使用方法：
1. 作为模块导入：
   from scripts.playwright_login import get_captcha_from_redis, login_with_playwright

2. 作为命令行工具：
   python scripts/playwright_login.py --url http://localhost:8080/linchat/login
   python scripts/playwright_login.py --url http://www.greydan.xin/linchat/login

依赖：
- redis
- playwright

环境变量：
- REDIS_URL: Redis 连接字符串 (默认: redis://:redis_linchat_123@localhost:6379/0)
- LINCHAT_USERNAME: 登录用户名 (默认: dantsinghua)
- LINCHAT_PASSWORD: 登录密码 (默认: !9871229Qing)
"""

import argparse
import asyncio
import os
import time
from typing import Optional

import redis


# ========== 配置 ==========

REDIS_URL = os.environ.get("REDIS_URL", "redis://:redis_linchat_123@localhost:6379/0")
DEFAULT_USERNAME = os.environ.get("LINCHAT_USERNAME", "dantsinghua")
DEFAULT_PASSWORD = os.environ.get("LINCHAT_PASSWORD", "!9871229Qing")
DEFAULT_LOGIN_URL = "http://localhost:8080/linchat/login"
PUBLIC_LOGIN_URL = "http://www.greydan.xin/linchat/login"


# ========== Redis 操作 ==========

def get_redis_client() -> redis.Redis:
    """获取 Redis 客户端"""
    return redis.from_url(REDIS_URL, decode_responses=True)


def get_captcha_from_redis(captcha_id: str) -> Optional[str]:
    """
    从 Redis 获取验证码

    Args:
        captcha_id: 验证码 ID (UUID 格式)

    Returns:
        验证码文本 (4位大写字母/数字) 或 None
    """
    r = get_redis_client()
    key = f"auth:captcha:{captcha_id}"
    captcha_text = r.get(key)
    if captcha_text:
        print(f"[Redis] 获取验证码成功: {captcha_id} -> {captcha_text}")
    else:
        print(f"[Redis] 验证码不存在或已过期: {captcha_id}")
    return captcha_text


def get_latest_captcha() -> Optional[tuple[str, str, int]]:
    """
    获取 Redis 中最新的验证码（TTL 最大的）

    Returns:
        (captcha_id, captcha_text, ttl) 或 None
    """
    r = get_redis_client()
    keys = r.keys("auth:captcha:*")
    if not keys:
        return None

    # 获取 TTL 最大的验证码
    latest = max(keys, key=lambda k: r.ttl(k))
    captcha_id = latest.replace("auth:captcha:", "")
    captcha_text = r.get(latest)
    ttl = r.ttl(latest)

    return captcha_id, captcha_text, ttl


def list_captchas() -> list:
    """列出所有验证码（调试用）"""
    r = get_redis_client()
    keys = r.keys("auth:captcha:*")
    result = []
    for k in keys:
        captcha_id = k.replace("auth:captcha:", "")
        captcha_text = r.get(k)
        ttl = r.ttl(k)
        result.append({
            "captcha_id": captcha_id,
            "captcha_text": captcha_text,
            "ttl": ttl
        })
    return sorted(result, key=lambda x: x["ttl"], reverse=True)


# ========== Playwright 登录 (同步版本) ==========

def login_with_playwright(
    page,
    username: str = None,
    password: str = None,
    max_retries: int = 3
) -> dict:
    """
    使用 Playwright 自动登录 LinChat（同步版本）

    流程：
    1. 点击刷新验证码
    2. 从 Redis 获取最新验证码
    3. 填写表单并提交
    4. 检查登录结果

    Args:
        page: Playwright Page 对象
        username: 用户名 (默认: dantsinghua)
        password: 密码 (默认: !9871229Qing)
        max_retries: 最大重试次数

    Returns:
        {success: bool, message: str, url: str}
    """
    username = username or DEFAULT_USERNAME
    password = password or DEFAULT_PASSWORD

    result = {
        "success": False,
        "message": "",
        "url": ""
    }

    for attempt in range(max_retries):
        try:
            print(f"\n[Playwright] 登录尝试 {attempt + 1}/{max_retries}")

            # 1. 点击刷新验证码
            captcha_button = page.get_by_title("点击刷新验证码")
            if captcha_button:
                captcha_button.click()
                time.sleep(0.5)  # 等待验证码加载

            # 2. 从 Redis 获取最新验证码
            captcha_info = get_latest_captcha()
            if not captcha_info:
                result["message"] = "无法获取验证码"
                continue

            captcha_id, captcha_text, ttl = captcha_info
            print(f"[Playwright] 验证码: {captcha_text} (TTL: {ttl}s)")

            if ttl < 5:
                print("[Playwright] 验证码即将过期，重新获取")
                continue

            # 3. 填写表单
            page.get_by_role("textbox", name="用户名").fill(username)
            page.get_by_role("textbox", name="密码").fill(password)
            page.get_by_role("textbox", name="验证码").fill(captcha_text)

            # 4. 提交登录
            page.get_by_role("button", name="登录").click()
            time.sleep(2)  # 等待页面跳转

            # 5. 检查登录结果
            current_url = page.url
            if "/login" not in current_url:
                result["success"] = True
                result["message"] = "登录成功"
                result["url"] = current_url
                print(f"[Playwright] 登录成功，已跳转到: {current_url}")
                return result
            else:
                # 检查错误消息
                error_el = page.query_selector(".text-red-500, [role='alert']")
                error_msg = error_el.text_content() if error_el else "登录失败"
                result["message"] = error_msg
                print(f"[Playwright] 登录失败: {error_msg}")

        except Exception as e:
            result["message"] = f"登录出错: {str(e)}"
            print(f"[Playwright] 登录出错: {e}")

    return result


# ========== Playwright 登录 (异步版本) ==========

async def login_with_playwright_async(
    page,
    username: str = None,
    password: str = None,
    max_retries: int = 3
) -> dict:
    """
    使用 Playwright 自动登录 LinChat（异步版本）

    Args:
        page: Playwright async Page 对象
        username: 用户名
        password: 密码
        max_retries: 最大重试次数

    Returns:
        {success: bool, message: str, url: str}
    """
    username = username or DEFAULT_USERNAME
    password = password or DEFAULT_PASSWORD

    result = {
        "success": False,
        "message": "",
        "url": ""
    }

    for attempt in range(max_retries):
        try:
            print(f"\n[Playwright] 登录尝试 {attempt + 1}/{max_retries}")

            # 1. 点击刷新验证码
            captcha_button = page.get_by_title("点击刷新验证码")
            await captcha_button.click()
            await asyncio.sleep(0.5)

            # 2. 从 Redis 获取最新验证码
            captcha_info = get_latest_captcha()
            if not captcha_info:
                result["message"] = "无法获取验证码"
                continue

            captcha_id, captcha_text, ttl = captcha_info
            print(f"[Playwright] 验证码: {captcha_text} (TTL: {ttl}s)")

            if ttl < 5:
                print("[Playwright] 验证码即将过期，重新获取")
                continue

            # 3. 填写表单
            await page.get_by_role("textbox", name="用户名").fill(username)
            await page.get_by_role("textbox", name="密码").fill(password)
            await page.get_by_role("textbox", name="验证码").fill(captcha_text)

            # 4. 提交登录
            await page.get_by_role("button", name="登录").click()
            await asyncio.sleep(2)

            # 5. 检查登录结果
            current_url = page.url
            if "/login" not in current_url:
                result["success"] = True
                result["message"] = "登录成功"
                result["url"] = current_url
                return result
            else:
                error_el = await page.query_selector(".text-red-500, [role='alert']")
                error_msg = await error_el.text_content() if error_el else "登录失败"
                result["message"] = error_msg

        except Exception as e:
            result["message"] = f"登录出错: {str(e)}"

    return result


# ========== 命令行接口 ==========

def main():
    parser = argparse.ArgumentParser(description="LinChat Playwright 自动登录工具")
    parser.add_argument("--url", default=DEFAULT_LOGIN_URL, help="登录页面 URL")
    parser.add_argument("--username", "-u", default=DEFAULT_USERNAME, help="用户名")
    parser.add_argument("--password", "-p", default=DEFAULT_PASSWORD, help="密码")
    parser.add_argument("--list-captchas", action="store_true", help="列出所有验证码")
    parser.add_argument("--get-captcha", metavar="ID", help="获取指定 ID 的验证码")
    parser.add_argument("--headless", action="store_true", help="无头模式运行")
    parser.add_argument("--public", action="store_true", help="使用公网 URL")

    args = parser.parse_args()

    if args.list_captchas:
        captchas = list_captchas()
        print(f"当前验证码数量: {len(captchas)}")
        for c in captchas:
            print(f"  {c['captcha_id']}: {c['captcha_text']} (TTL: {c['ttl']}s)")
        return

    if args.get_captcha:
        captcha_text = get_captcha_from_redis(args.get_captcha)
        if captcha_text:
            print(f"验证码: {captcha_text}")
        else:
            print("验证码不存在或已过期")
        return

    # 执行自动登录
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("请安装 playwright: pip install playwright && playwright install")
        return

    login_url = PUBLIC_LOGIN_URL if args.public else args.url

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()

        print(f"[Playwright] 打开登录页面: {login_url}")
        page.goto(login_url, wait_until="networkidle")

        result = login_with_playwright(page, args.username, args.password)

        print(f"\n========== 登录结果 ==========")
        print(f"成功: {result['success']}")
        print(f"消息: {result['message']}")
        print(f"URL: {result['url']}")

        if result["success"]:
            # 登录成功后保持浏览器打开 5 秒
            time.sleep(5)

        browser.close()


if __name__ == "__main__":
    main()
