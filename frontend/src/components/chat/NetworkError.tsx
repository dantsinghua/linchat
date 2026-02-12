/**
 * 网络错误组件
 *
 * 参考:
 * - spec.md Edge Cases - 网络中断时发送消息场景
 * - tasks.md T049b - 网络中断错误提示组件
 *
 * 功能：
 * - 显示网络错误提示
 * - 保留用户输入内容
 * - 提供重试机制
 * - 统一管理网络错误状态
 */
'use client';

import { memo, useCallback, useEffect, useState } from 'react';

interface NetworkErrorProps {
  /** 错误信息 */
  error: string | null;
  /** 清除错误回调 */
  onClear?: () => void;
  /** 重试回调 */
  onRetry?: () => void;
  /** 是否显示重试按钮 */
  showRetry?: boolean;
  /** 自动消失时间（毫秒），0 表示不自动消失 */
  autoHideDuration?: number;
  /** 自定义样式类 */
  className?: string;
  /** Gateway 模型切换倒计时（秒） */
  gatewayRetryAfter?: number;
  /** 倒计时结束/清零回调 */
  onRetryAfterDone?: () => void;
}

/**
 * 网络错误类型定义
 */
export type NetworkErrorType =
  | 'connection'      // 连接失败
  | 'timeout'         // 超时
  | 'rate_limit'      // 频率限制
  | 'content_filter'  // 内容过滤
  | 'quota_exceeded'  // 配额用尽
  | 'unknown';        // 未知错误

/**
 * 根据错误信息判断错误类型
 */
export function getNetworkErrorType(error: string): NetworkErrorType {
  const errorLower = error.toLowerCase();

  if (errorLower.includes('连接') || errorLower.includes('connection') || errorLower.includes('network')) {
    return 'connection';
  }
  if (errorLower.includes('超时') || errorLower.includes('timeout')) {
    return 'timeout';
  }
  if (errorLower.includes('频繁') || errorLower.includes('rate') || errorLower.includes('limit')) {
    return 'rate_limit';
  }
  if (errorLower.includes('敏感') || errorLower.includes('content') || errorLower.includes('filter')) {
    return 'content_filter';
  }
  if (errorLower.includes('配额') || errorLower.includes('quota') || errorLower.includes('exceeded')) {
    return 'quota_exceeded';
  }

  return 'unknown';
}

/**
 * 获取用户友好的错误提示信息
 *
 * 参考: constitution.md#4.3 - LLM异常用户提示
 */
export function getErrorMessage(error: string): string {
  const errorType = getNetworkErrorType(error);

  switch (errorType) {
    case 'connection':
      return 'AI 服务暂时无法连接，请稍后重试';
    case 'timeout':
      return 'AI 响应超时，请稍后重试';
    case 'rate_limit':
      return '请求过于频繁，请稍后重试';
    case 'content_filter':
      return '消息包含敏感内容，请修改后重试';
    case 'quota_exceeded':
      return '服务配额用尽，请联系管理员';
    default:
      return error || '发送失败，请重试';
  }
}

/**
 * 判断错误是否可重试
 */
export function isRetryableError(error: string): boolean {
  const errorType = getNetworkErrorType(error);
  // 内容过滤和配额用尽不可重试
  return errorType !== 'content_filter' && errorType !== 'quota_exceeded';
}

/**
 * 网络错误横幅组件
 *
 * 显示在聊天区域顶部的错误提示条
 */
export const NetworkError = memo(function NetworkError({
  error,
  onClear,
  onRetry,
  showRetry = true,
  autoHideDuration = 0,
  className = '',
  gatewayRetryAfter = 0,
  onRetryAfterDone,
}: NetworkErrorProps) {
  const [visible, setVisible] = useState(false);
  const [countdown, setCountdown] = useState(0);

  // 错误出现时显示
  useEffect(() => {
    if (error) {
      setVisible(true);
    }
  }, [error]);

  // T067a: Gateway 模型切换倒计时
  useEffect(() => {
    if (gatewayRetryAfter > 0) {
      setCountdown(gatewayRetryAfter);
      const timer = setInterval(() => {
        setCountdown((prev) => {
          if (prev <= 1) {
            clearInterval(timer);
            onRetryAfterDone?.();
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
      return () => clearInterval(timer);
    }
    setCountdown(0);
    return undefined;
  }, [gatewayRetryAfter, onRetryAfterDone]);

  // 自动消失
  useEffect(() => {
    if (error && autoHideDuration > 0) {
      const timer = setTimeout(() => {
        setVisible(false);
        onClear?.();
      }, autoHideDuration);

      return () => clearTimeout(timer);
    }
    return undefined;
  }, [error, autoHideDuration, onClear]);

  // 手动关闭
  const handleClose = useCallback(() => {
    setVisible(false);
    onClear?.();
  }, [onClear]);

  // 重试
  const handleRetry = useCallback(() => {
    setVisible(false);
    onClear?.();
    onRetry?.();
  }, [onClear, onRetry]);

  if (!error || !visible) {
    return null;
  }

  const friendlyMessage = getErrorMessage(error);
  const canRetry = isRetryableError(error);
  const isCountingDown = countdown > 0;

  return (
    <div
      className={`border-b border-red-200 bg-red-50 px-4 py-3 dark:border-red-800 dark:bg-red-900/20 ${className}`}
      role="alert"
    >
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-red-700 dark:text-red-400">
          {/* 错误图标 */}
          <svg
            className="h-5 w-5 flex-shrink-0"
            fill="currentColor"
            viewBox="0 0 20 20"
          >
            <path
              fillRule="evenodd"
              d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
              clipRule="evenodd"
            />
          </svg>
          <span className="text-sm">
            {friendlyMessage}
            {isCountingDown && (
              <span className="ml-1.5 font-medium">
                （模型切换中，约 {countdown} 秒后可重试）
              </span>
            )}
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* 重试按钮：倒计时中禁用，倒计时结束后可点击 */}
          {showRetry && canRetry && onRetry && (
            <button
              onClick={handleRetry}
              disabled={isCountingDown}
              className={`rounded px-3 py-1 text-sm font-medium transition-colors ${
                isCountingDown
                  ? 'cursor-not-allowed text-red-300 dark:text-red-600'
                  : 'text-red-700 hover:bg-red-100 dark:text-red-400 dark:hover:bg-red-800/50'
              }`}
            >
              重试
            </button>
          )}

          {/* 关闭按钮 */}
          <button
            onClick={handleClose}
            className="rounded p-1 text-red-500 transition-colors hover:bg-red-100 dark:hover:bg-red-800/50"
            aria-label="关闭"
          >
            <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 20 20">
              <path
                fillRule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
});

/**
 * 内联网络错误组件
 *
 * 用于在输入框附近显示的小型错误提示
 */
interface InlineNetworkErrorProps {
  error: string | null;
  onClear?: () => void;
  className?: string;
}

export const InlineNetworkError = memo(function InlineNetworkError({
  error,
  onClear,
  className = '',
}: InlineNetworkErrorProps) {
  if (!error) {
    return null;
  }

  const friendlyMessage = getErrorMessage(error);

  return (
    <div
      className={`flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-900/20 dark:text-red-400 ${className}`}
      role="alert"
    >
      <svg className="h-4 w-4 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
        <path
          fillRule="evenodd"
          d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z"
          clipRule="evenodd"
        />
      </svg>
      <span className="flex-1">{friendlyMessage}</span>
      {onClear && (
        <button
          onClick={onClear}
          className="ml-2 text-red-500 hover:text-red-700"
          aria-label="关闭"
        >
          <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 20 20">
            <path
              fillRule="evenodd"
              d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
              clipRule="evenodd"
            />
          </svg>
        </button>
      )}
    </div>
  );
});

/**
 * 网络错误 Hook
 *
 * 提供网络错误状态管理
 */
export function useNetworkError() {
  const [error, setError] = useState<string | null>(null);
  const [lastFailedContent, setLastFailedContent] = useState<string | null>(null);

  const setNetworkError = useCallback((err: string, failedContent?: string) => {
    setError(err);
    if (failedContent) {
      setLastFailedContent(failedContent);
    }
  }, []);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  const clearFailedContent = useCallback(() => {
    setLastFailedContent(null);
  }, []);

  const clearAll = useCallback(() => {
    setError(null);
    setLastFailedContent(null);
  }, []);

  return {
    error,
    lastFailedContent,
    setNetworkError,
    clearError,
    clearFailedContent,
    clearAll,
  };
}
