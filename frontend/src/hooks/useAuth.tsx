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
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

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

/**
 * 认证 Hook
 */
export function useAuth() {
  const router = useRouter();
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null);
  const [toast, setToast] = useState<ToastMessage | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);

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
   * 处理 SSE 事件
   */
  const handleSSEEvent = useCallback(
    (event: MessageEvent) => {
      try {
        const data: SSEEvent = JSON.parse(event.data);

        switch (data.type) {
          case 'logout':
            // 单点登录冲突处理
            if (data.reason === 'SSO_CONFLICT') {
              // 显示 Toast 提示 3 秒
              showToast('您已在其他设备登录', 'warning', 3000);

              // Toast 消失后跳转登录页
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
            // SSE 连接成功
            break;

          case 'heartbeat':
            // 心跳包，忽略
            break;

          default:
            // 未知事件类型，忽略
        }
      } catch {
        console.error('Failed to parse SSE event:', event.data);
      }
    },
    [router, showToast]
  );

  /**
   * 建立 SSE 连接
   *
   * 使用 addEventListener 监听具体事件类型，
   * 因为后端 event_service.py 发送的事件包含 event: 字段
   */
  const connectSSE = useCallback(() => {
    // 关闭现有连接
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    // 清除重连定时器
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }

    try {
      // 创建 SSE 连接
      // 注意：EventSource 不支持设置 credentials，需要确保同源或服务端正确配置 CORS
      const eventSource = new EventSource(`${API_BASE_URL}/events`, {
        withCredentials: true,
      });

      // 监听具体事件类型（后端使用 event: 字段区分事件类型）
      // logout 事件：单点登录冲突、Token 过期、管理员踢出
      eventSource.addEventListener('logout', handleSSEEvent);
      // heartbeat 事件：心跳包
      eventSource.addEventListener('heartbeat', handleSSEEvent);
      // message 事件：通用消息（包括 connected）
      eventSource.addEventListener('message', handleSSEEvent);
      // 默认事件（无 event: 字段的消息）
      eventSource.onmessage = handleSSEEvent;

      eventSource.onerror = (error) => {
        console.error('SSE connection error:', error);

        // 连接断开，尝试重连
        if (eventSource.readyState === EventSource.CLOSED) {
          eventSourceRef.current = null;

          // 5秒后重连
          reconnectTimeoutRef.current = setTimeout(() => {
            if (isAuthenticated) {
              connectSSE();
            }
          }, 5000);
        }
      };

      eventSource.onopen = () => {
        // SSE 连接已建立
      };

      eventSourceRef.current = eventSource;
    } catch (error) {
      console.error('Failed to create SSE connection:', error);
    }
  }, [handleSSEEvent, isAuthenticated]);

  /**
   * 断开 SSE 连接
   */
  const disconnectSSE = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }, []);

  /**
   * 检查认证状态
   */
  const checkAuth = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/auth/me`, {
        credentials: 'include',
      });

      if (response.ok) {
        setIsAuthenticated(true);
        return true;
      } else {
        setIsAuthenticated(false);
        return false;
      }
    } catch {
      setIsAuthenticated(false);
      return false;
    }
  }, []);

  /**
   * 登录成功后调用
   */
  const onLoginSuccess = useCallback(() => {
    setIsAuthenticated(true);
    connectSSE();
  }, [connectSSE]);

  /**
   * 登出
   */
  const logout = useCallback(async () => {
    try {
      await fetch(`${API_BASE_URL}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      });
    } catch {
      // 忽略错误
    } finally {
      setIsAuthenticated(false);
      disconnectSSE();
      router.push('/login');
    }
  }, [disconnectSSE, router]);

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
