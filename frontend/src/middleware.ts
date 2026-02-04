import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

/**
 * 路由保护中间件
 *
 * 参考:
 * - process-model.md#一、用户登录流程（P_AUTH_001）- 步骤2-3 Token检查和跳转
 * - process-model.md#二、Token鉴权流程（P_AUTH_002）- 401响应处理
 *
 * 功能:
 * - 未登录用户访问受保护页面时跳转到登录页
 * - 已登录用户访问登录页时跳转到聊天页
 *
 * 注意:
 * - Token 存储在 httpOnly Cookie 中，中间件可以读取
 * - Cookie 名称: linchat_token（与后端 TOKEN_COOKIE_NAME 保持一致）
 */

// basePath 配置 (与 next.config.js 保持一致)
const basePath = '/linchat';

// Token Cookie 名称（与后端保持一致）
const TOKEN_COOKIE_NAME = 'linchat_token';

// 需要认证的路由前缀
const protectedRoutes = ['/chat'];

// 公开路由（已登录用户访问会跳转到聊天页）
const publicOnlyRoutes = ['/login'];

// 完全公开的路由（无需任何处理）
const publicRoutes = ['/401', '/api'];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // 移除 basePath 前缀进行路由匹配
  const pathWithoutBase = pathname.startsWith(basePath)
    ? pathname.slice(basePath.length) || '/'
    : pathname;

  // 完全公开路由，不做处理
  if (publicRoutes.some((route) => pathWithoutBase.startsWith(route))) {
    return NextResponse.next();
  }

  // 检查 Token Cookie
  // 参考: behavior-model.md#1.3 Token鉴权验证
  const token = request.cookies.get(TOKEN_COOKIE_NAME)?.value;
  const isAuthenticated = !!token;

  // 受保护路由检查
  // 未登录用户尝试访问受保护页面，跳转到登录页
  if (protectedRoutes.some((route) => pathWithoutBase.startsWith(route))) {
    if (!isAuthenticated) {
      const loginUrl = new URL(`${basePath}/login`, request.url);
      loginUrl.searchParams.set('redirect', pathWithoutBase);
      return NextResponse.redirect(loginUrl);
    }
  }

  // 公开路由检查
  // 已登录用户访问登录页，跳转到聊天页
  // 但如果带有 redirect 参数，说明是被 401 重定向来的，允许访问登录页
  if (publicOnlyRoutes.some((route) => pathWithoutBase.startsWith(route))) {
    const hasRedirect = request.nextUrl.searchParams.has('redirect');
    if (isAuthenticated && !hasRedirect) {
      return NextResponse.redirect(new URL(`${basePath}/chat`, request.url));
    }
  }

  // 根路径处理
  if (pathWithoutBase === '/') {
    if (isAuthenticated) {
      return NextResponse.redirect(new URL(`${basePath}/chat`, request.url));
    } else {
      return NextResponse.redirect(new URL(`${basePath}/login`, request.url));
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * 匹配所有路径除了:
     * - api (API 路由)
     * - _next/static (静态文件)
     * - _next/image (图片优化)
     * - favicon.ico (网站图标)
     */
    '/((?!api|_next/static|_next/image|favicon.ico).*)',
  ],
};
