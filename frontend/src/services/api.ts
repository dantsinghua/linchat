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
import { isAuthRedirecting, trigger401Redirect } from '@/services/authGuard';

// ============ 工具函数 ============

/**
 * 将 snake_case 转换为 camelCase
 *
 * 后端返回 snake_case，前端使用 camelCase
 */
function snakeToCamel(str: string): string {
  return str.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase());
}

/**
 * 递归转换对象的键名从 snake_case 到 camelCase
 */
function transformKeysToCamelCase<T>(obj: unknown): T {
  if (obj === null || obj === undefined) {
    return obj as T;
  }

  if (Array.isArray(obj)) {
    return obj.map((item) => transformKeysToCamelCase(item)) as T;
  }

  if (typeof obj === 'object') {
    const transformed: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      const camelKey = snakeToCamel(key);
      transformed[camelKey] = transformKeysToCamelCase(value);
    }
    return transformed as T;
  }

  return obj as T;
}

// API 基础 URL
// 生产环境: /linchat/api/v1 (nginx 重写为 /api/v1)
// 开发环境: /api/v1 (直连后端)
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
    // 已在重定向中，直接拒绝后续请求
    if (isAuthRedirecting()) {
      return Promise.reject(new axios.Cancel('Auth redirecting'));
    }
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
    // 将响应数据从 snake_case 转换为 camelCase
    // 参考: 后端返回 user_id/expire_time，前端期望 userId/expireTime
    if (response.data) {
      response.data = transformKeysToCamelCase(response.data);
    }
    return response;
  },
  (error: AxiosError<ApiError>) => {
    // 处理 401 未授权错误
    if (error.response?.status === 401) {
      trigger401Redirect();
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
          trigger401Redirect();
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
