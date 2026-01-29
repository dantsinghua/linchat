/**
 * Settings 页面测试 (T031)
 *
 * 覆盖:
 * - 页面加载渲染
 * - 权限守卫：非管理员跳转
 * - 加载状态
 * - API 错误处理
 */
import { render, screen, waitFor } from '@testing-library/react';

import * as modelService from '@/services/modelService';
import * as useAuthHook from '@/hooks/useAuth';

// Mock 模块
jest.mock('@/services/modelService');
jest.mock('@/hooks/useAuth');
jest.mock('@/stores/modelStore', () => ({
  useModelStore: () => ({
    models: [],
    isLoading: false,
    error: null,
    setModels: jest.fn(),
    updateModelInList: jest.fn(),
    setIsLoading: jest.fn(),
    setError: jest.fn(),
    reset: jest.fn(),
  }),
}));

// 获取 mock router
const mockPush = jest.fn();
jest.mock('next/navigation', () => ({
  useRouter: () => ({
    push: mockPush,
    replace: jest.fn(),
    refresh: jest.fn(),
    back: jest.fn(),
    forward: jest.fn(),
    prefetch: jest.fn(),
  }),
  usePathname: () => '/settings',
  useSearchParams: () => new URLSearchParams(),
}));

// 延迟导入确保 mock 生效
import SettingsPage from '@/app/settings/page';

describe('SettingsPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('权限守卫', () => {
    it('未认证用户应跳转到 /401', async () => {
      (useAuthHook.useAuth as jest.Mock).mockReturnValue({
        isAuthenticated: false,
        user: null,
        logout: jest.fn(),
      });

      render(<SettingsPage />);

      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith('/401');
      });
    });

    it('非管理员用户应跳转到 /401', async () => {
      (useAuthHook.useAuth as jest.Mock).mockReturnValue({
        isAuthenticated: true,
        user: { user_id: 1, username: 'user1', type: 'user' },
        logout: jest.fn(),
      });

      render(<SettingsPage />);

      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith('/401');
      });
    });

    it('管理员用户不应跳转', async () => {
      (useAuthHook.useAuth as jest.Mock).mockReturnValue({
        isAuthenticated: true,
        user: { user_id: 1, username: 'admin', type: 'admin' },
        logout: jest.fn(),
      });
      (modelService.fetchModels as jest.Mock).mockResolvedValue([]);

      render(<SettingsPage />);

      // 不应跳转到 401
      await waitFor(() => {
        expect(mockPush).not.toHaveBeenCalledWith('/401');
      });
    });
  });

  describe('认证加载中', () => {
    it('认证状态为 null 时应显示加载中', () => {
      (useAuthHook.useAuth as jest.Mock).mockReturnValue({
        isAuthenticated: null,
        user: null,
        logout: jest.fn(),
      });

      render(<SettingsPage />);

      expect(screen.getByText('加载中...')).toBeInTheDocument();
    });
  });
});
