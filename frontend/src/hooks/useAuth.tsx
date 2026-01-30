/**
 * 认证 Hook
 *
 * 参考:
 * - process-model.md#一点五、单点登录SSE推送流程
 * - tasks.md#T015d
 *
 * 功能:
 * - 登录状态管理
 * - SSE 事件监听（单点登录登出事件）
 * - Token 刷新事件监听
 *
 * 重要: 使用 fetch SSE 替代原生 EventSource，
 * 避免浏览器自动重连导致 401 请求风暴。
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { trigger401Redirect, resetAuthGuard } from '@/services/authGuard';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || '/api/v1';

// SSE 事件类型
interface SSEEvent {
  type: 'logout' | 'message' | 'heartbeat' | 'connected';
  reason?: 'SSO_CONFLICT' | 'TOKEN_EXPIRED' | 'ADMIN_KICK';
  message?: string;
}

// Toast 消息类型
interface ToastMessage {
  id: string;
  message: string;
  type: 'info' | 'warning' | 'error';
  duration: number;
}

// 用户信息类型
interface UserInfo {
  user_id: number;
  username: string;
  type: 'admin' | 'user';
}

/**
 * 认证 Hook
 */
export function useAuth() {
  const router = useRouter();
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null);
  const [user, setUser] = useState<UserInfo | null>(null);
  const [toast, setToast] = useState<ToastMessage | null>(null);
  const sseAbortRef = useRef<AbortController | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  // 使用 ref 存储认证状态，避免闭包过期问题
  const isAuthenticatedRef = useRef<boolean | null>(null);

  /**
   * 显示 Toast 消息
   */
  const showToast = useCallback(
    (message: string, type: ToastMessage['type'] = 'info', duration: number = 3000) => {
      const id = Date.now().toString();
      setToast({ id, message, type, duration });

      // 自动清除
      setTimeout(() => {
        setToast((current) => (current?.id === id ? null : current));
      }, duration);
    },
    []
  );

  /**
   * 断开 SSE 连接
   */
  const disconnectSSE = useCallback(() => {
    if (sseAbortRef.current) {
      sseAbortRef.current.abort();
      sseAbortRef.current = null;
    }

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }, []);

  /**
   * 处理 SSE 事件
   */
  const handleSSEEvent = useCallback(
    (data: SSEEvent) => {
      switch (data.type) {
        case 'logout':
          // 先断开 SSE，防止断连后触发重连
          disconnectSSE();

          if (data.reason === 'SSO_CONFLICT') {
            showToast('您已在其他设备登录', 'warning', 3000);
            setTimeout(() => {
              setIsAuthenticated(false);
              router.push('/login');
            }, 3000);
          } else if (data.reason === 'TOKEN_EXPIRED') {
            showToast('登录已过期，请重新登录', 'warning', 2000);
            setTimeout(() => {
              setIsAuthenticated(false);
              router.push('/login');
            }, 2000);
          } else if (data.reason === 'ADMIN_KICK') {
            showToast('您已被管理员踢出', 'error', 3000);
            setTimeout(() => {
              setIsAuthenticated(false);
              router.push('/login');
            }, 3000);
          }
          break;

        case 'connected':
        case 'heartbeat':
          // 忽略
          break;

        default:
          break;
      }
    },
    [disconnectSSE, router, showToast]
  );

  /**
   * 建立 SSE 连接（使用 fetch + ReadableStream 替代 EventSource）
   *
   * 优势：401 时不会被浏览器自动重连，可完全控制重连逻辑。
   */
  const connectSSE = useCallback(() => {
    // 关闭现有连接
    disconnectSSE();

    const controller = new AbortController();
    sseAbortRef.current = controller;

    const runSSE = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/events`, {
          credentials: 'include',
          signal: controller.signal,
        });

        if (!response.ok) {
          if (response.status === 401) {
            trigger401Redirect();
            return;
          }
          throw new Error(`SSE HTTP error: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) return;

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          // 解析 SSE 格式：支持 event: 和 data: 字段
          let currentEventType = '';
          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEventType = line.slice(7).trim();
            } else if (line.startsWith('data: ')) {
              try {
                const data: SSEEvent = JSON.parse(line.slice(6));
                // 如果后端通过 event: 字段指定了类型，覆盖 data 中的 type
                if (currentEventType && (currentEventType === 'logout' || currentEventType === 'heartbeat' || currentEventType === 'message' || currentEventType === 'connected')) {
                  data.type = currentEventType as SSEEvent['type'];
                }
                handleSSEEvent(data);
              } catch {
                // 忽略解析错误
              }
              currentEventType = '';
            } else if (line === '') {
              // 空行表示事件分隔
              currentEventType = '';
            }
          }
        }

        // 流正常结束，尝试重连
        if (isAuthenticatedRef.current) {
          reconnectTimeoutRef.current = setTimeout(() => {
            if (isAuthenticatedRef.current) {
              connectSSE();
            }
          }, 5000);
        }
      } catch (error) {
        if ((error as Error).name === 'AbortError') return;

        console.error('SSE connection error:', error);
        // 非 401 错误，5 秒后重连
        if (isAuthenticatedRef.current) {
          reconnectTimeoutRef.current = setTimeout(() => {
            if (isAuthenticatedRef.current) {
              connectSSE();
            }
          }, 5000);
        }
      }
    };

    runSSE();
  }, [disconnectSSE, handleSSEEvent]);

  /**
   * 检查认证状态
   */
  const checkAuth = useCallback(async () => {
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
      } else {
        if (response.status === 401) {
          trigger401Redirect();
        }
        setIsAuthenticated(false);
        setUser(null);
        return false;
      }
    } catch {
      setIsAuthenticated(false);
      setUser(null);
      return false;
    }
  }, []);

  /**
   * 登录成功后调用
   */
  const onLoginSuccess = useCallback(() => {
    resetAuthGuard();
    setIsAuthenticated(true);
    connectSSE();
  }, [connectSSE]);

  /**
   * 登出
   */
  const logout = useCallback(async () => {
    // 先断开 SSE 并清状态，防止断连触发重连
    disconnectSSE();
    setIsAuthenticated(false);
    setUser(null);

    try {
      await fetch(`${API_BASE_URL}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      });
    } catch {
      // 忽略错误
    }

    router.push('/login');
  }, [disconnectSSE, router]);

  // 同步 isAuthenticated 到 ref，避免闭包过期问题
  useEffect(() => {
    isAuthenticatedRef.current = isAuthenticated;
  }, [isAuthenticated]);

  // 组件挂载时检查认证状态
  useEffect(() => {
    checkAuth().then((authenticated) => {
      if (authenticated) {
        connectSSE();
      }
    });

    return () => {
      disconnectSSE();
    };
  }, [checkAuth, connectSSE, disconnectSSE]);

  return {
    isAuthenticated,
    user,
    toast,
    checkAuth,
    onLoginSuccess,
    logout,
    showToast,
  };
}

/**
 * Toast 组件
 */
export function AuthToast({ toast }: { toast: ToastMessage | null }) {
  if (!toast) return null;

  const bgColor = {
    info: 'bg-blue-500',
    warning: 'bg-yellow-500',
    error: 'bg-red-500',
  }[toast.type];

  return (
    <div
      className={`fixed top-4 left-1/2 -translate-x-1/2 z-50 px-6 py-3 rounded-lg shadow-lg text-white ${bgColor} animate-fade-in`}
    >
      {toast.message}
    </div>
  );
}
