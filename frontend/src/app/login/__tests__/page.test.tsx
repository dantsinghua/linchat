/**
 * 登录页错误处理测试 (T020)
 *
 * 015-family-multiuser:
 * - ACCOUNT_EXPIRED 错误码展示"账号已过期，请联系家庭成员"
 * - 正常登录错误（密码错误等）不展示过期提示
 *
 * 测试策略：直接测试 LoginForm 组件，mock authService.login 返回不同错误
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// ========== Mock 依赖 ==========

const mockLogin = jest.fn();
jest.mock('@/services/authService', () => ({
  login: (...args: unknown[]) => mockLogin(...args),
  getCaptcha: jest.fn().mockResolvedValue({
    captcha_id: 'test-captcha-id',
    captcha_image: 'data:image/png;base64,test',
  }),
}));

jest.mock('@/utils/crypto', () => ({
  sm4Encrypt: (text: string) => `encrypted_${text}`,
}));

jest.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({
    onLoginSuccess: jest.fn(),
  }),
}));

// Mock CaptchaImage 组件，使其自动触发 onCaptchaChange
jest.mock('@/components/auth/CaptchaImage', () => ({
  CaptchaImage: ({ onCaptchaChange }: { onCaptchaChange: (id: string) => void }) => {
    // 挂载时立即设置 captchaId
    setTimeout(() => onCaptchaChange('test-captcha-id'), 0);
    return <div data-testid="captcha-image">验证码</div>;
  },
}));

import { LoginForm } from '@/components/auth/LoginForm';

// ========== 辅助函数 ==========

/** 填写登录表单并提交 */
async function fillAndSubmitForm() {
  const usernameInput = screen.getByPlaceholderText('请输入用户名');
  const passwordInput = screen.getByPlaceholderText('请输入密码');
  const captchaInput = screen.getByPlaceholderText('请输入验证码');

  fireEvent.change(usernameInput, { target: { value: 'testuser' } });
  fireEvent.change(passwordInput, { target: { value: 'password123' } });

  // 等待 CaptchaImage 的 onCaptchaChange 被调用
  await waitFor(() => {
    expect(screen.getByTestId('captcha-image')).toBeInTheDocument();
  });

  fireEvent.change(captchaInput, { target: { value: 'ABCD' } });

  const submitButton = screen.getByRole('button', { name: /登录/i });
  fireEvent.click(submitButton);
}

// ========== 测试用例 ==========

beforeEach(() => {
  jest.clearAllMocks();
});

describe('登录页错误处理 (T020)', () => {
  describe('ACCOUNT_EXPIRED 错误', () => {
    it('应展示"账号已过期，请联系家庭成员"提示', async () => {
      // 模拟 ACCOUNT_EXPIRED 错误
      const expiredError = new Error('账号已过期，请联系家庭成员');
      // 模拟 authService.login 中的错误处理逻辑：
      // 当后端返回 code=ACCOUNT_EXPIRED 时，login 函数会抛出此消息
      mockLogin.mockRejectedValueOnce(expiredError);

      render(<LoginForm onLoginSuccess={jest.fn()} redirectUrl="/chat" />);

      await fillAndSubmitForm();

      await waitFor(() => {
        expect(screen.getByText('账号已过期，请联系家庭成员')).toBeInTheDocument();
      });
    });
  });

  describe('正常登录错误（密码错误）', () => {
    it('应展示后端返回的错误信息，不展示过期提示', async () => {
      mockLogin.mockRejectedValueOnce(new Error('用户名或密码错误'));

      render(<LoginForm onLoginSuccess={jest.fn()} redirectUrl="/chat" />);

      await fillAndSubmitForm();

      await waitFor(() => {
        expect(screen.getByText('用户名或密码错误')).toBeInTheDocument();
      });

      // 不应出现过期提示
      expect(screen.queryByText('账号已过期，请联系家庭成员')).not.toBeInTheDocument();
    });
  });

  describe('通用登录失败', () => {
    it('非 Error 实例的异常应展示默认错误信息', async () => {
      mockLogin.mockRejectedValueOnce('unknown error');

      render(<LoginForm onLoginSuccess={jest.fn()} redirectUrl="/chat" />);

      await fillAndSubmitForm();

      await waitFor(() => {
        expect(screen.getByText('登录失败，请重试')).toBeInTheDocument();
      });
    });
  });
});
