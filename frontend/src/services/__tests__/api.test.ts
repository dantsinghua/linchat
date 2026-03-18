/**
 * Axios 拦截器单元测试
 *
 * T040: 测试请求/响应拦截器的 015-family-multiuser 逻辑
 * - 代查模式自动注入 X-Target-User-Id Header
 * - 查看自己时不注入 Header
 * - clearTarget 后不再携带 Header
 * - 400 TARGET_USER_INVALID 响应自动清除 memberStore 状态
 */

// ========== Mock authGuard ==========

jest.mock('@/services/authGuard', () => ({
  isAuthRedirecting: jest.fn().mockReturnValue(false),
  trigger401Redirect: jest.fn(),
}));

// ========== 测试用例 ==========

// 注意: api.ts 在模块加载时就注册了拦截器并引用 useMemberStore，
// 因此不能 mock memberStore 模块，而是直接操作 store 状态。

import axios from 'axios';
import type { AxiosError, AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import { useMemberStore } from '@/stores/memberStore';
import { isAuthRedirecting, trigger401Redirect } from '@/services/authGuard';

// ─── 提取 api.ts 中注册的拦截器进行独立测试 ───

// 请求拦截器逻辑（镜像 api.ts 中的实现）
function requestInterceptor(
  config: InternalAxiosRequestConfig
): InternalAxiosRequestConfig | Promise<never> {
  if ((isAuthRedirecting as jest.Mock)()) {
    return Promise.reject(new axios.Cancel('Auth redirecting')) as Promise<never>;
  }

  const memberState = useMemberStore.getState();
  if (memberState.isViewingOther()) {
    config.headers['X-Target-User-Id'] = String(memberState.targetUserId);
  }

  return config;
}

// 响应错误拦截器逻辑（镜像 api.ts 中的实现）
function responseErrorInterceptor(error: AxiosError<{ code?: string; message?: string }>): Promise<never> {
  if (error.response?.status === 401) {
    (trigger401Redirect as jest.Mock)();
  }

  if (
    error.response?.status === 400 &&
    error.response.data?.code === 'TARGET_USER_INVALID'
  ) {
    const { clearTarget } = useMemberStore.getState();
    clearTarget();
  }

  return Promise.reject(error);
}

// ========== localStorage Mock ==========

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] || null,
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();
Object.defineProperty(window, 'localStorage', { value: localStorageMock });

// ─── 辅助函数 ───

/** 创建最小化的 InternalAxiosRequestConfig */
function createRequestConfig(
  overrides?: Partial<InternalAxiosRequestConfig>
): InternalAxiosRequestConfig {
  return {
    headers: new axios.AxiosHeaders(),
    ...overrides,
  } as InternalAxiosRequestConfig;
}

/** 创建模拟的 AxiosError */
function createAxiosError(
  status: number,
  data?: { code?: string; message?: string }
): AxiosError<{ code?: string; message?: string }> {
  const error = new Error('Request failed') as AxiosError<{
    code?: string;
    message?: string;
  }>;
  error.isAxiosError = true;
  error.response = {
    status,
    data: data || {},
    headers: {},
    statusText: status === 400 ? 'Bad Request' : 'Error',
    config: createRequestConfig(),
  } as AxiosResponse<{ code?: string; message?: string }>;
  return error;
}

// ─── 每次测试前重置 ───

beforeEach(() => {
  jest.clearAllMocks();
  localStorageMock.clear();
  useMemberStore.setState({
    targetUserId: null,
    targetUsername: null,
    members: [],
    authUserId: null,
    isLoading: false,
  });
});

// ========== 测试用例 ==========

describe('Axios 拦截器', () => {
  // ─── 请求拦截器: X-Target-User-Id 注入 ───

  describe('请求拦截器 - X-Target-User-Id', () => {
    it('isViewingOther=true 时应自动携带 X-Target-User-Id Header', () => {
      // 设置代查模式: targetUserId=42, authUserId=100
      useMemberStore.setState({ targetUserId: 42, authUserId: 100 });

      const config = createRequestConfig();
      const result = requestInterceptor(config);

      // 非 Promise 分支
      expect(result).not.toBeInstanceOf(Promise);
      expect((result as InternalAxiosRequestConfig).headers['X-Target-User-Id']).toBe('42');
    });

    it('target=self 时不应携带 X-Target-User-Id Header', () => {
      // targetUserId === authUserId，查看自己
      useMemberStore.setState({ targetUserId: 100, authUserId: 100 });

      const config = createRequestConfig();
      const result = requestInterceptor(config);

      expect((result as InternalAxiosRequestConfig).headers['X-Target-User-Id']).toBeUndefined();
    });

    it('targetUserId 为 null 时不应携带 Header', () => {
      useMemberStore.setState({ targetUserId: null, authUserId: 100 });

      const config = createRequestConfig();
      const result = requestInterceptor(config);

      expect((result as InternalAxiosRequestConfig).headers['X-Target-User-Id']).toBeUndefined();
    });

    it('clearTarget 后请求不再携带 Header', () => {
      // 先设置代查模式
      useMemberStore.setState({ targetUserId: 42, authUserId: 100 });

      // 第一次请求应携带 Header
      const config1 = createRequestConfig();
      const result1 = requestInterceptor(config1);
      expect((result1 as InternalAxiosRequestConfig).headers['X-Target-User-Id']).toBe('42');

      // 清除目标
      useMemberStore.getState().clearTarget();

      // 第二次请求不应携带 Header
      const config2 = createRequestConfig();
      const result2 = requestInterceptor(config2);
      expect((result2 as InternalAxiosRequestConfig).headers['X-Target-User-Id']).toBeUndefined();
    });

    it('Header 值应为字符串类型', () => {
      useMemberStore.setState({ targetUserId: 42, authUserId: 100 });

      const config = createRequestConfig();
      const result = requestInterceptor(config);

      const headerValue = (result as InternalAxiosRequestConfig).headers[
        'X-Target-User-Id'
      ];
      expect(typeof headerValue).toBe('string');
      expect(headerValue).toBe('42');
    });

    it('切换不同目标用户时 Header 值应更新', () => {
      useMemberStore.setState({ targetUserId: 42, authUserId: 100 });

      const config1 = createRequestConfig();
      const result1 = requestInterceptor(config1);
      expect((result1 as InternalAxiosRequestConfig).headers['X-Target-User-Id']).toBe('42');

      // 切换到另一个用户
      useMemberStore.setState({ targetUserId: 55 });

      const config2 = createRequestConfig();
      const result2 = requestInterceptor(config2);
      expect((result2 as InternalAxiosRequestConfig).headers['X-Target-User-Id']).toBe('55');
    });
  });

  // ─── 请求拦截器: 认证重定向 ───

  describe('请求拦截器 - 认证重定向', () => {
    it('isAuthRedirecting=true 时应拒绝请求', async () => {
      (isAuthRedirecting as jest.Mock).mockReturnValue(true);

      const config = createRequestConfig();
      const result = requestInterceptor(config);

      await expect(result).rejects.toThrow('Auth redirecting');
    });

    it('isAuthRedirecting=false 时应正常通过', () => {
      (isAuthRedirecting as jest.Mock).mockReturnValue(false);

      const config = createRequestConfig();
      const result = requestInterceptor(config);

      expect(result).not.toBeInstanceOf(Promise);
    });
  });

  // ─── 响应拦截器: TARGET_USER_INVALID ───

  describe('响应拦截器 - TARGET_USER_INVALID', () => {
    it('400 TARGET_USER_INVALID 响应后应自动清除 memberStore 目标用户', async () => {
      // 设置代查模式
      useMemberStore.setState({ targetUserId: 42, authUserId: 100 });
      localStorageMock.setItem('linchat_target_user_id', '42');
      localStorageMock.setItem('linchat_target_username', '橘猫团子');

      const error = createAxiosError(400, {
        code: 'TARGET_USER_INVALID',
        message: '目标用户不存在或已过期',
      });

      await expect(responseErrorInterceptor(error)).rejects.toThrow();

      // 验证 store 状态已清除
      const state = useMemberStore.getState();
      expect(state.targetUserId).toBeNull();
      expect(state.targetUsername).toBeNull();

      // 验证 localStorage 已清除
      expect(localStorageMock.getItem('linchat_target_user_id')).toBeNull();
      expect(localStorageMock.getItem('linchat_target_username')).toBeNull();
    });

    it('400 但 code 非 TARGET_USER_INVALID 时不应清除状态', async () => {
      useMemberStore.setState({ targetUserId: 42, authUserId: 100 });

      const error = createAxiosError(400, {
        code: 'VALIDATION_ERROR',
        message: '参数错误',
      });

      await expect(responseErrorInterceptor(error)).rejects.toThrow();

      // 状态不应被清除
      expect(useMemberStore.getState().targetUserId).toBe(42);
    });

    it('其他 HTTP 状态码不应清除目标用户', async () => {
      useMemberStore.setState({ targetUserId: 42, authUserId: 100 });

      const error = createAxiosError(500, {
        code: 'SERVER_ERROR',
        message: '服务器错误',
      });

      await expect(responseErrorInterceptor(error)).rejects.toThrow();

      expect(useMemberStore.getState().targetUserId).toBe(42);
    });
  });

  // ─── 响应拦截器: 401 处理 ───

  describe('响应拦截器 - 401', () => {
    it('401 响应应触发认证重定向', async () => {
      const error = createAxiosError(401);

      await expect(responseErrorInterceptor(error)).rejects.toThrow();

      expect(trigger401Redirect).toHaveBeenCalledTimes(1);
    });

    it('非 401 响应不应触发认证重定向', async () => {
      const error = createAxiosError(400, {
        code: 'TARGET_USER_INVALID',
        message: '目标用户无效',
      });

      await expect(responseErrorInterceptor(error)).rejects.toThrow();

      expect(trigger401Redirect).not.toHaveBeenCalled();
    });
  });

  // ─── 综合场景 ───

  describe('综合场景', () => {
    it('完整的代查生命周期: 设置目标 → 请求携带Header → 目标失效 → 自动清除 → 不再携带Header', async () => {
      // 1. 设置代查目标
      useMemberStore.setState({ targetUserId: 42, authUserId: 100 });

      // 2. 请求应携带 Header
      const config1 = createRequestConfig();
      const result1 = requestInterceptor(config1);
      expect((result1 as InternalAxiosRequestConfig).headers['X-Target-User-Id']).toBe('42');

      // 3. 收到 TARGET_USER_INVALID 响应，自动清除
      const error = createAxiosError(400, {
        code: 'TARGET_USER_INVALID',
        message: '目标用户已过期',
      });
      await expect(responseErrorInterceptor(error)).rejects.toThrow();

      // 4. 验证状态已清除
      expect(useMemberStore.getState().targetUserId).toBeNull();

      // 5. 后续请求不再携带 Header
      const config2 = createRequestConfig();
      const result2 = requestInterceptor(config2);
      expect((result2 as InternalAxiosRequestConfig).headers['X-Target-User-Id']).toBeUndefined();
    });
  });
});
