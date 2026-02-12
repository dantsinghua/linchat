/**
 * 认证 Hook
 *
 * 功能: 登录状态管理、SSE 单点登录事件监听
 * 使用 fetch SSE 替代 EventSource，避免浏览器自动重连导致 401 请求风暴。
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { trigger401Redirect, resetAuthGuard, isAuthRedirecting } from '@/services/authGuard';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || '/api/v1';

interface SSEEvent {
  type: 'logout' | 'message' | 'heartbeat' | 'connected' | 'context_status' | 'doc_parse_progress';
  reason?: 'SSO_CONFLICT' | 'TOKEN_EXPIRED' | 'ADMIN_KICK';
  message?: string;
  [key: string]: unknown;
}

interface UserInfo {
  user_id: number;
  username: string;
  type: 'admin' | 'user';
}

// SSO 登出原因 → 提示文案 + 延迟
const LOGOUT_REASONS: Record<string, { msg: string; delay: number }> = {
  SSO_CONFLICT:  { msg: '您已在其他设备登录', delay: 3000 },
  TOKEN_EXPIRED: { msg: '登录已过期，请重新登录', delay: 2000 },
  ADMIN_KICK:    { msg: '您已被管理员踢出', delay: 3000 },
};

export function useAuth() {
  const router = useRouter();
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null);
  const [user, setUser] = useState<UserInfo | null>(null);
  const sseAbortRef = useRef<AbortController | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const isAuthenticatedRef = useRef<boolean | null>(null);

  const disconnectSSE = useCallback(() => {
    sseAbortRef.current?.abort();
    sseAbortRef.current = null;
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }, []);

  const scheduleReconnect = useCallback(() => {
    if (!isAuthenticatedRef.current) return;
    reconnectTimeoutRef.current = setTimeout(() => {
      if (isAuthenticatedRef.current) connectSSE();
    }, 5000);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSSEEvent = useCallback(
    (data: SSEEvent) => {
      // 上下文监控事件 — 通过 CustomEvent 分发给监控面板
      if (data.type === 'context_status') {
        window.dispatchEvent(
          new CustomEvent('context_status', { detail: data })
        );
        return;
      }

      // 文档解析进度事件 (T043a) — 分发给 useDocParse Hook
      if (data.type === 'doc_parse_progress') {
        window.dispatchEvent(
          new CustomEvent('doc_parse_progress', { detail: data })
        );
        return;
      }

      if (data.type !== 'logout' || !data.reason) return;
      const cfg = LOGOUT_REASONS[data.reason];
      if (!cfg) return;

      disconnectSSE();
      setTimeout(() => {
        setIsAuthenticated(false);
        router.push('/login');
      }, cfg.delay);
    },
    [disconnectSSE, router]
  );

  const connectSSE = useCallback(() => {
    if (isAuthRedirecting()) return;
    disconnectSSE();
    const controller = new AbortController();
    sseAbortRef.current = controller;

    (async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/events`, {
          credentials: 'include',
          signal: controller.signal,
        });

        if (!response.ok) {
          if (response.status === 401) { trigger401Redirect(); return; }
          throw new Error(`SSE HTTP error: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) return;

        const decoder = new TextDecoder();
        let buffer = '';
        let currentEventType = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEventType = line.slice(7).trim();
            } else if (line.startsWith('data: ')) {
              try {
                const data: SSEEvent = JSON.parse(line.slice(6));
                if (currentEventType && ['logout', 'heartbeat', 'message', 'connected', 'context_status', 'doc_parse_progress'].includes(currentEventType)) {
                  data.type = currentEventType as SSEEvent['type'];
                }
                handleSSEEvent(data);
              } catch { /* 忽略解析错误 */ }
              currentEventType = '';
            } else if (line === '') {
              currentEventType = '';
            }
          }
        }

        scheduleReconnect();
      } catch (error) {
        if ((error as Error).name === 'AbortError') return;
        console.error('SSE connection error:', error);
        scheduleReconnect();
      }
    })();
  }, [disconnectSSE, handleSSEEvent, scheduleReconnect]);

  const checkAuth = useCallback(async () => {
    if (isAuthRedirecting()) return false;
    try {
      const response = await fetch(`${API_BASE_URL}/auth/me`, {
        credentials: 'include',
      });

      if (response.ok) {
        const data = await response.json();
        if (data.code === 'SUCCESS' && data.data) {
          setUser({
            user_id: data.data.user_id,
            username: data.data.username,
            type: data.data.type || 'user',
          });
        }
        setIsAuthenticated(true);
        return true;
      }

      if (response.status === 401) trigger401Redirect();
      setIsAuthenticated(false);
      setUser(null);
      return false;
    } catch {
      setIsAuthenticated(false);
      setUser(null);
      return false;
    }
  }, []);

  const onLoginSuccess = useCallback(() => {
    resetAuthGuard();
    setIsAuthenticated(true);
    connectSSE();
  }, [connectSSE]);

  const logout = useCallback(async () => {
    disconnectSSE();
    setIsAuthenticated(false);
    setUser(null);

    try {
      await fetch(`${API_BASE_URL}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      });
    } catch { /* 忽略 */ }

    router.push('/login');
  }, [disconnectSSE, router]);

  useEffect(() => {
    isAuthenticatedRef.current = isAuthenticated;
  }, [isAuthenticated]);

  useEffect(() => {
    checkAuth().then((ok) => { if (ok) connectSSE(); });
    return () => disconnectSSE();
  }, [checkAuth, connectSSE, disconnectSSE]);

  return { isAuthenticated, user, checkAuth, onLoginSuccess, logout };
}
