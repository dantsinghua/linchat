/**
 * 全局 401 认证守卫
 *
 * 解决问题：Token 过期后多个并发请求同时收到 401，
 * 各自触发 window.location.href 导致请求风暴。
 *
 * 通过模块级标记确保只触发一次重定向。
 */

const BASE_PATH = '/linchat';

let _isRedirecting = false;

/**
 * 是否正在重定向到登录页
 */
export function isAuthRedirecting(): boolean {
  return _isRedirecting;
}

/**
 * 触发 401 重定向（幂等，多次调用只执行一次）
 */
export function trigger401Redirect(): void {
  if (_isRedirecting) return;
  if (typeof window === 'undefined') return;

  const currentPath = window.location.pathname;
  // 已经在登录页则不跳转
  if (currentPath.endsWith('/login')) return;

  _isRedirecting = true;

  const pathWithoutBase = currentPath.startsWith(BASE_PATH)
    ? currentPath.slice(BASE_PATH.length) || '/'
    : currentPath;

  window.location.href = `${BASE_PATH}/login?redirect=${encodeURIComponent(pathWithoutBase)}`;
}

/**
 * 重置守卫状态（登录成功后调用）
 */
export function resetAuthGuard(): void {
  _isRedirecting = false;
}
