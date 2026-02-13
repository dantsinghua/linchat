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
  (response: AxiosResponse) => response,
  (error: AxiosError<ApiError>) => {
    // 处理 401 未授权错误
    if (error.response?.status === 401) {
      trigger401Redirect();
    }

    // 处理 429 频率限制
    if (error.response?.status === 429) {
      const retryAfter = error.response.data?.retry_after || 60;
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

export default apiClient;
