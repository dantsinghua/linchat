import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

/**
 * 路由保护中间件
 *
 * 将在 T031 完善实现
 * 未登录用户访问受保护页面时跳转到登录页
 */

// basePath 配置 (与 next.config.js 保持一致)
const basePath = '/linchat';

// 需要认证的路由 (不含 basePath)
const protectedRoutes = ['/linchat/chat'];

// 公开路由（已登录用户访问会跳转到聊天页）
const publicRoutes = ['/linchat/login'];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // 移除 basePath 前缀进行路由匹配
  const pathWithoutBase = pathname.startsWith(basePath)
    ? pathname.slice(basePath.length) || '/'
    : pathname;

  // TODO: 检查 Token Cookie
  // const token = request.cookies.get('token')?.value;
  // const isAuthenticated = !!token;

  // 暂时跳过认证检查，将在 Phase 3 实现
  const isAuthenticated = false;

  // 受保护路由检查
  if (protectedRoutes.some((route) => pathWithoutBase.startsWith(route))) {
    if (!isAuthenticated) {
      const loginUrl = new URL(`${basePath}/login`, request.url);
      loginUrl.searchParams.set('redirect', pathWithoutBase);
      return NextResponse.redirect(loginUrl);
    }
  }

  // 公开路由检查（已登录用户跳转到聊天页）
  if (publicRoutes.some((route) => pathWithoutBase.startsWith(route))) {
    if (isAuthenticated) {
      return NextResponse.redirect(new URL(`${basePath}/chat`, request.url));
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
