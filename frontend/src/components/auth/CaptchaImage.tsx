/**
 * 验证码组件
 *
 * 参考:
 * - behavior-model.md#1.1 获取验证码（B_AUTH_001）
 * - rule-model.md#R_CAPTCHA_003 验证码自动刷新规则
 *
 * 功能:
 * - 显示验证码图片
 * - 点击刷新
 * - 自动刷新（110秒间隔，在2分钟过期前刷新）
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { getCaptcha } from '@/services/authService';
import { CaptchaResponse } from '@/types';

interface CaptchaImageProps {
  onCaptchaChange: (captchaId: string) => void;
  className?: string;
}

// 自动刷新间隔（毫秒）
// 参考: rule-model.md#R_CAPTCHA_003 - 验证码2分钟过期，前端110秒刷新
const AUTO_REFRESH_INTERVAL = 110 * 1000;

export function CaptchaImage({ onCaptchaChange, className = '' }: CaptchaImageProps) {
  const [captcha, setCaptcha] = useState<CaptchaResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refreshTimerRef = useRef<NodeJS.Timeout | null>(null);

  /**
   * 刷新验证码
   */
  const refreshCaptcha = useCallback(async () => {
    if (loading) return;

    setLoading(true);
    setError(null);

    try {
      const result = await getCaptcha();
      setCaptcha(result);
      onCaptchaChange(result.captcha_id);
    } catch (err) {
      setError('获取验证码失败，点击重试');
      console.error('Failed to get captcha:', err);
    } finally {
      setLoading(false);
    }
  }, [loading, onCaptchaChange]);

  /**
   * 设置自动刷新定时器
   */
  const setupAutoRefresh = useCallback(() => {
    // 清除现有定时器
    if (refreshTimerRef.current) {
      clearInterval(refreshTimerRef.current);
    }

    // 设置新定时器
    // [R_CAPTCHA_003] 验证码过期前10秒（110秒间隔）自动刷新
    refreshTimerRef.current = setInterval(() => {
      refreshCaptcha();
    }, AUTO_REFRESH_INTERVAL);
  }, [refreshCaptcha]);

  /**
   * 组件挂载时获取验证码
   *
   * 故意使用空依赖数组，只在组件首次挂载时执行
   * 刷新逻辑由 handleClick 和定时器控制
   */
  useEffect(() => {
    refreshCaptcha();
    setupAutoRefresh();

    return () => {
      if (refreshTimerRef.current) {
        clearInterval(refreshTimerRef.current);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * 手动刷新时重置定时器
   */
  const handleClick = () => {
    refreshCaptcha();
    setupAutoRefresh();
  };

  return (
    <div
      className={`relative inline-block cursor-pointer ${className}`}
      onClick={handleClick}
      title="点击刷新验证码"
    >
      {loading ? (
        <div className="flex items-center justify-center w-[120px] h-[40px] bg-gray-100 rounded">
          <svg
            className="animate-spin h-5 w-5 text-blue-500"
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
        </div>
      ) : error ? (
        <div className="flex items-center justify-center w-[120px] h-[40px] bg-red-50 rounded text-red-500 text-xs text-center px-2">
          {error}
        </div>
      ) : captcha ? (
        <img
          src={captcha.captcha_image}
          alt="验证码"
          className="w-[120px] h-[40px] rounded border border-gray-200"
        />
      ) : (
        <div className="flex items-center justify-center w-[120px] h-[40px] bg-gray-100 rounded text-gray-400 text-xs">
          加载中...
        </div>
      )}

      {/* 刷新提示 */}
      <div className="absolute inset-0 flex items-center justify-center bg-black bg-opacity-0 hover:bg-opacity-10 transition-all rounded">
        <span className="text-transparent hover:text-white text-xs">点击刷新</span>
      </div>
    </div>
  );
}
