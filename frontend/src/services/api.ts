/**
 * Axios 实例配置
 *
 * 参考: process-model.md#二、Token鉴权流程 - 401响应时前端处理
 *
 * 安全要求:
 * - Token 存储在 httpOnly Cookie 中（由后端设置/清除）
 * - 使用 credentials: 'include' 自动携带 Cookie
 * - 前端无需手动管理 Token
 */
import axios, {
  AxiosError,
  AxiosInstance,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from 'axios';

import { ApiError, ApiResponse } from '@/types';

// API 基础 URL
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || '/api/v1';

/**
 * 创建 Axios 实例
 */
const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000, // 30秒超时
  withCredentials: true, // 携带 httpOnly Cookie
  headers: {
    'Content-Type': 'application/json',
  },
});

/**
 * 请求拦截器
 */
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    // Token 存储在 httpOnly Cookie 中，无需手动设置 Authorization 头
    // Cookie 会自动随请求发送
    return config;
  },
  (error: AxiosError) => {
    return Promise.reject(error);
  }
);

/**
 * 响应拦截器
 */
apiClient.interceptors.response.use(
  (response: AxiosResponse) => {
    return response;
  },
  (error: AxiosError<ApiError>) => {
    // 处理 401 未授权错误
    if (error.response?.status === 401) {
      // Token 由 httpOnly Cookie 管理，前端无法直接操作
      // 后端会在登出时清除 Cookie
      // 跳转到登录页，保存当前路径用于登录后返回
      if (typeof window !== 'undefined') {
        const currentPath = window.location.pathname;
        // 避免在登录页循环跳转
        if (currentPath !== '/login') {
          window.location.href = `/login?redirect=${encodeURIComponent(currentPath)}`;
        }
      }
    }

    // 处理 429 频率限制
    if (error.response?.status === 429) {
      const retryAfter = error.response.data?.retryAfter || 60;
      console.warn(`Rate limited. Retry after ${retryAfter} seconds.`);
    }

    return Promise.reject(error);
  }
);

/**
 * 封装 GET 请求
 */
export async function get<T>(url: string, params?: object): Promise<ApiResponse<T>> {
  const response = await apiClient.get<ApiResponse<T>>(url, { params });
  return response.data;
}

/**
 * 封装 POST 请求
 */
export async function post<T>(url: string, data?: object): Promise<ApiResponse<T>> {
  const response = await apiClient.post<ApiResponse<T>>(url, data);
  return response.data;
}

/**
 * 封装 PUT 请求
 */
export async function put<T>(url: string, data?: object): Promise<ApiResponse<T>> {
  const response = await apiClient.put<ApiResponse<T>>(url, data);
  return response.data;
}

/**
 * 封装 DELETE 请求
 */
export async function del<T>(url: string): Promise<ApiResponse<T>> {
  const response = await apiClient.delete<ApiResponse<T>>(url);
  return response.data;
}

/**
 * 封装 SSE 流式请求
 *
 * 用于聊天流式响应
 */
export function createSSEConnection(
  url: string,
  options?: {
    method?: 'GET' | 'POST';
    body?: object;
    onMessage?: (data: unknown) => void;
    onError?: (error: Error) => void;
    onClose?: () => void;
  }
): { abort: () => void } {
  const controller = new AbortController();

  const fetchSSE = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}${url}`, {
        method: options?.method || 'GET',
        credentials: 'include', // 携带 httpOnly Cookie
        headers: {
          'Content-Type': 'application/json',
        },
        body: options?.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
      });

      if (!response.ok) {
        // 处理 401
        if (response.status === 401) {
          if (typeof window !== 'undefined') {
            window.location.href = `/login?redirect=${encodeURIComponent(window.location.pathname)}`;
          }
          return;
        }
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('Response body is not readable');
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          options?.onClose?.();
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              options?.onMessage?.(data);
            } catch {
              // 忽略解析错误
            }
          }
        }
      }
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        options?.onError?.(error as Error);
      }
    }
  };

  fetchSSE();

  return {
    abort: () => controller.abort(),
  };
}

export default apiClient;
