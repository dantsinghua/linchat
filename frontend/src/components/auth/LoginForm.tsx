/**
 * 登录表单组件
 *
 * 参考:
 * - process-model.md#一、用户登录流程（P_AUTH_001）
 * - process-model.md#异常处理 - 登录异常场景和前端处理
 */
'use client';

import { FormEvent, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { CaptchaImage } from './CaptchaImage';
import { login } from '@/services/authService';

interface LoginFormProps {
  onLoginSuccess?: () => void;
  redirectUrl?: string;
}

export function LoginForm({ onLoginSuccess, redirectUrl = '/chat' }: LoginFormProps) {
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [captchaCode, setCaptchaCode] = useState('');
  const [captchaId, setCaptchaId] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // 验证码刷新 key，用于强制重新挂载 CaptchaImage 组件
  const [captchaRefreshKey, setCaptchaRefreshKey] = useState(0);
  // 防抖：记录上次提交时间
  // 参考: spec.md Edge Cases - 快速重复点击 → 前端防抖处理（300ms间隔）
  const lastSubmitTimeRef = useRef<number>(0);
  const DEBOUNCE_INTERVAL = 300; // 毫秒

  /**
   * 处理验证码变更
   */
  const handleCaptchaChange = (newCaptchaId: string) => {
    setCaptchaId(newCaptchaId);
    setCaptchaCode(''); // 清空已输入的验证码
  };

  /**
   * 表单验证
   */
  const validateForm = (): boolean => {
    if (!username.trim()) {
      setError('请输入用户名');
      return false;
    }
    if (!password) {
      setError('请输入密码');
      return false;
    }
    if (!captchaCode.trim()) {
      setError('请输入验证码');
      return false;
    }
    if (captchaCode.length !== 4) {
      setError('验证码格式错误');
      return false;
    }
    if (!captchaId) {
      setError('验证码已失效，请刷新');
      return false;
    }
    return true;
  };

  /**
   * 处理登录提交
   *
   * 包含 300ms 防抖处理，防止快速重复点击
   */
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();

    // 防抖检查：300ms 内不允许重复提交
    const now = Date.now();
    if (now - lastSubmitTimeRef.current < DEBOUNCE_INTERVAL) {
      return;
    }
    lastSubmitTimeRef.current = now;

    setError(null);

    if (!validateForm()) {
      return;
    }

    setLoading(true);

    try {
      await login(username.trim(), password, captchaId, captchaCode.trim());

      // 登录成功
      onLoginSuccess?.();

      // 跳转到目标页面
      router.push(redirectUrl);
    } catch (err) {
      // 登录失败
      const message = err instanceof Error ? err.message : '登录失败，请重试';
      setError(message);

      // 刷新验证码（通过 key 强制重新挂载 CaptchaImage 组件）
      // 参考: process-model.md 异常处理 - 登录失败后刷新验证码
      setCaptchaRefreshKey((prev) => prev + 1);
      setCaptchaCode('');
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* 错误提示 */}
      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-red-600 text-sm">
          {error}
        </div>
      )}

      {/* 用户名 */}
      <div>
        <label
          htmlFor="username"
          className="block text-sm font-medium text-gray-700 mb-1"
        >
          用户名
        </label>
        <input
          id="username"
          type="text"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
          placeholder="请输入用户名"
          disabled={loading}
          autoComplete="username"
        />
      </div>

      {/* 密码 */}
      <div>
        <label
          htmlFor="password"
          className="block text-sm font-medium text-gray-700 mb-1"
        >
          密码
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
          placeholder="请输入密码"
          disabled={loading}
          autoComplete="current-password"
        />
      </div>

      {/* 验证码 */}
      <div>
        <label
          htmlFor="captcha"
          className="block text-sm font-medium text-gray-700 mb-1"
        >
          验证码
        </label>
        <div className="flex items-center gap-3">
          <input
            id="captcha"
            type="text"
            value={captchaCode}
            onChange={(e) => setCaptchaCode(e.target.value.toUpperCase())}
            maxLength={4}
            className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all uppercase"
            placeholder="请输入验证码"
            disabled={loading}
            autoComplete="off"
          />
          <CaptchaImage
            key={captchaRefreshKey}
            onCaptchaChange={handleCaptchaChange}
          />
        </div>
      </div>

      {/* 登录按钮 */}
      <button
        type="submit"
        disabled={loading}
        className="w-full py-3 px-4 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white font-medium rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
      >
        {loading ? (
          <span className="flex items-center justify-center gap-2">
            <svg
              className="animate-spin h-5 w-5"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
              />
            </svg>
            登录中...
          </span>
        ) : (
          '登录'
        )}
      </button>
    </form>
  );
}
