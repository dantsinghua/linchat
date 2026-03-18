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
import { useChatStore } from '@/stores/chatStore';
import { useMemberStore } from '@/stores/memberStore';

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
  member_type: 'member' | 'guest';
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

      // 文档解析进度事件 — 写入 chatStore + 分发给 useDocParse Hook（012-doc-parse-progress）
      if (data.type === 'doc_parse_progress') {
        const progress = data.progress as { current?: number; total?: number } | undefined;
        const status = data.status as string;
        useChatStore.getState().setDocParseProgress({
          taskId: (data.task_id as string) || '',
          status: status as 'pending' | 'processing' | 'completed' | 'incomplete' | 'failed',
          current: progress?.current ?? 0,
          total: progress?.total ?? 0,
          fileName: (data.file_name as string) || '',
          suggestion: data.suggestion as string | undefined,
          errorMessage: data.error_message as string | undefined,
        });
        // 终态延迟清除（completed: 1.5s，其他终态: 3s）
        if (['completed', 'incomplete', 'failed'].includes(status)) {
          const taskId = data.task_id as string;
          const delay = status === 'completed' ? 1500 : 3000;
          setTimeout(() => {
            if (useChatStore.getState().docParseProgress?.taskId === taskId) {
              useChatStore.getState().setDocParseProgress(null);
            }
          }, delay);
        }
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
        const err = error as Error;
        // AbortError: 主动断开（disconnectSSE 调用）
        // TypeError "network error": 页面卸载/导航时浏览器中断流连接（Chromium 行为）
        if (err.name === 'AbortError') return;
        if (err.name === 'TypeError' && /network/i.test(err.message)) return;
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
          const memberType = data.data.member_type || 'guest';
          setUser({
            user_id: data.data.user_id,
            username: data.data.username,
            type: data.data.type || 'user',
            member_type: memberType,
          });

          // 015-family-multiuser: 初始化成员状态
          if (memberType === 'member') {
            const { setAuthUserId, loadMembers, restoreTargetFromStorage } = useMemberStore.getState();
            setAuthUserId(data.data.user_id);
            await loadMembers();
            restoreTargetFromStorage();
          } else {
            useMemberStore.getState().setAuthUserId(data.data.user_id);
          }
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
    // 015-family-multiuser: 清除成员切换状态
    useMemberStore.getState().clearTarget();
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
