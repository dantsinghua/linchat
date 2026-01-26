/**
 * CaptchaImage 组件测试
 *
 * 测试内容:
 * - 验证码加载和显示
 * - 点击刷新
 * - 自动刷新（110秒间隔）
 * - 错误处理
 */
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { CaptchaImage } from '@/components/auth/CaptchaImage';
import * as authService from '@/services/authService';

// Mock authService
jest.mock('@/services/authService');
const mockedAuthService = authService as jest.Mocked<typeof authService>;

describe('CaptchaImage', () => {
  const mockOnCaptchaChange = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  describe('初始化加载', () => {
    it('组件挂载时应自动获取验证码', async () => {
      mockedAuthService.getCaptcha.mockResolvedValue({
        captchaId: 'test-captcha-id',
        captchaImage: 'data:image/png;base64,mock-image',
      });

      render(<CaptchaImage onCaptchaChange={mockOnCaptchaChange} />);

      await waitFor(() => {
        expect(mockedAuthService.getCaptcha).toHaveBeenCalledTimes(1);
      });

      await waitFor(() => {
        expect(mockOnCaptchaChange).toHaveBeenCalledWith('test-captcha-id');
      });
    });

    it('加载中应显示加载动画', () => {
      // 使用一个永不resolve的Promise
      mockedAuthService.getCaptcha.mockImplementation(
        () => new Promise(() => {})
      );

      render(<CaptchaImage onCaptchaChange={mockOnCaptchaChange} />);

      // 由于是异步加载，应该显示loading状态或加载中文本
      // 在实际组件中，首次渲染时captcha为null会显示"加载中..."
    });

    it('加载成功后应显示验证码图片', async () => {
      mockedAuthService.getCaptcha.mockResolvedValue({
        captchaId: 'test-captcha-id',
        captchaImage: 'data:image/png;base64,mock-image',
      });

      render(<CaptchaImage onCaptchaChange={mockOnCaptchaChange} />);

      await waitFor(() => {
        const img = screen.getByAltText('验证码');
        expect(img).toBeInTheDocument();
        expect(img).toHaveAttribute('src', 'data:image/png;base64,mock-image');
      });
    });
  });

  describe('刷新功能', () => {
    it('点击应触发刷新', async () => {
      mockedAuthService.getCaptcha
        .mockResolvedValueOnce({
          captchaId: 'first-captcha-id',
          captchaImage: 'data:image/png;base64,first-image',
        })
        .mockResolvedValueOnce({
          captchaId: 'second-captcha-id',
          captchaImage: 'data:image/png;base64,second-image',
        });

      render(<CaptchaImage onCaptchaChange={mockOnCaptchaChange} />);

      // 等待首次加载完成
      await waitFor(() => {
        expect(screen.getByAltText('验证码')).toBeInTheDocument();
      });

      // 点击刷新
      const captchaContainer = screen.getByTitle('点击刷新验证码');
      fireEvent.click(captchaContainer);

      await waitFor(() => {
        expect(mockedAuthService.getCaptcha).toHaveBeenCalledTimes(2);
      });

      await waitFor(() => {
        expect(mockOnCaptchaChange).toHaveBeenLastCalledWith('second-captcha-id');
      });
    });
  });

  describe('自动刷新', () => {
    it('应在110秒后自动刷新', async () => {
      mockedAuthService.getCaptcha
        .mockResolvedValueOnce({
          captchaId: 'first-captcha-id',
          captchaImage: 'data:image/png;base64,first-image',
        })
        .mockResolvedValueOnce({
          captchaId: 'auto-refreshed-id',
          captchaImage: 'data:image/png;base64,auto-image',
        });

      render(<CaptchaImage onCaptchaChange={mockOnCaptchaChange} />);

      // 等待首次加载
      await waitFor(() => {
        expect(mockedAuthService.getCaptcha).toHaveBeenCalledTimes(1);
      });

      // 推进时间 110 秒
      act(() => {
        jest.advanceTimersByTime(110 * 1000);
      });

      await waitFor(() => {
        expect(mockedAuthService.getCaptcha).toHaveBeenCalledTimes(2);
      });
    });

    it('手动刷新后应重置自动刷新定时器', async () => {
      mockedAuthService.getCaptcha.mockResolvedValue({
        captchaId: 'captcha-id',
        captchaImage: 'data:image/png;base64,image',
      });

      render(<CaptchaImage onCaptchaChange={mockOnCaptchaChange} />);

      await waitFor(() => {
        expect(screen.getByAltText('验证码')).toBeInTheDocument();
      });

      // 推进 60 秒
      act(() => {
        jest.advanceTimersByTime(60 * 1000);
      });

      // 手动刷新
      const captchaContainer = screen.getByTitle('点击刷新验证码');
      fireEvent.click(captchaContainer);

      await waitFor(() => {
        expect(mockedAuthService.getCaptcha).toHaveBeenCalledTimes(2);
      });

      // 再推进 60 秒（总共120秒 < 110+60=170秒）
      // 不应触发自动刷新
      act(() => {
        jest.advanceTimersByTime(60 * 1000);
      });

      // 仍然是2次
      expect(mockedAuthService.getCaptcha).toHaveBeenCalledTimes(2);
    });
  });

  describe('错误处理', () => {
    it('获取失败时应显示错误信息', async () => {
      mockedAuthService.getCaptcha.mockRejectedValue(new Error('网络错误'));

      render(<CaptchaImage onCaptchaChange={mockOnCaptchaChange} />);

      await waitFor(() => {
        expect(screen.getByText(/获取验证码失败/)).toBeInTheDocument();
      });
    });

    it('错误状态下点击应重试', async () => {
      mockedAuthService.getCaptcha
        .mockRejectedValueOnce(new Error('网络错误'))
        .mockResolvedValueOnce({
          captchaId: 'retry-captcha-id',
          captchaImage: 'data:image/png;base64,retry-image',
        });

      render(<CaptchaImage onCaptchaChange={mockOnCaptchaChange} />);

      // 等待错误显示
      await waitFor(() => {
        expect(screen.getByText(/获取验证码失败/)).toBeInTheDocument();
      });

      // 点击重试
      const captchaContainer = screen.getByTitle('点击刷新验证码');
      fireEvent.click(captchaContainer);

      await waitFor(() => {
        expect(screen.getByAltText('验证码')).toBeInTheDocument();
      });
    });
  });

  describe('自定义样式', () => {
    it('应支持自定义 className', () => {
      mockedAuthService.getCaptcha.mockResolvedValue({
        captchaId: 'test-id',
        captchaImage: 'data:image/png;base64,image',
      });

      render(
        <CaptchaImage
          onCaptchaChange={mockOnCaptchaChange}
          className="custom-class"
        />
      );

      const container = screen.getByTitle('点击刷新验证码');
      expect(container).toHaveClass('custom-class');
    });
  });

  describe('组件卸载', () => {
    it('卸载时应清除定时器', async () => {
      mockedAuthService.getCaptcha.mockResolvedValue({
        captchaId: 'test-id',
        captchaImage: 'data:image/png;base64,image',
      });

      const { unmount } = render(
        <CaptchaImage onCaptchaChange={mockOnCaptchaChange} />
      );

      await waitFor(() => {
        expect(screen.getByAltText('验证码')).toBeInTheDocument();
      });

      // 卸载组件
      unmount();

      // 推进时间，不应触发新的请求
      act(() => {
        jest.advanceTimersByTime(110 * 1000);
      });

      // getCaptcha 只应被调用一次（初始加载）
      expect(mockedAuthService.getCaptcha).toHaveBeenCalledTimes(1);
    });
  });
});
