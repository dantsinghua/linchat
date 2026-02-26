/**
 * 语音模式异常处理 Hook (T053)
 *
 * 统一管理语音交互过程中的异常状态：
 * - 麦克风权限被拒绝时提供友好提示
 * - 浏览器切换标签/最小化时暂停录音并提示
 * - 网络断开时显示重连状态
 */
'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';

/** Hook 配置选项 */
interface UseVoiceErrorHandlerOptions {
  /** WebSocket 是否已连接 */
  isConnected: boolean;
  /** 是否正在录音 */
  isRecording: boolean;
  /** 录音暂停回调 */
  onPauseRecording?: () => void;
  /** 录音恢复回调 */
  onResumeRecording?: () => void;
}

/** Hook 返回值 */
interface UseVoiceErrorHandlerReturn {
  /** 麦克风权限被拒 */
  isMicDenied: boolean;
  /** 浏览器不可见（切标签/最小化） */
  isPageHidden: boolean;
  /** 网络离线 */
  isOffline: boolean;
  /** 当前错误提示文本（如有） */
  errorMessage: string | null;
  /** 设置麦克风权限被拒状态 */
  setMicDenied: (denied: boolean) => void;
}

/** 错误提示文本常量 */
const ERROR_MSG_MIC_DENIED = '需要麦克风权限才能使用语音模式';
const ERROR_MSG_OFFLINE = '网络连接已断开，请检查网络设置';
const ERROR_MSG_PAGE_HIDDEN = '页面不可见，录音已暂停';

/**
 * 语音模式异常处理 Hook
 *
 * @param options - 配置选项
 * @returns 异常状态与错误提示
 */
export function useVoiceErrorHandler(
  options: UseVoiceErrorHandlerOptions,
): UseVoiceErrorHandlerReturn {
  const { isRecording, onPauseRecording, onResumeRecording } = options;

  const [isMicDenied, setIsMicDenied] = useState(false);
  const [isPageHidden, setIsPageHidden] = useState(false);
  const [isOffline, setIsOffline] = useState(false);

  /** 设置麦克风权限被拒状态 */
  const setMicDenied = useCallback((denied: boolean) => {
    setIsMicDenied(denied);
  }, []);

  // ─── 页面可见性检测 ───
  useEffect(() => {
    const handleVisibilityChange = () => {
      const hidden = document.visibilityState === 'hidden';
      setIsPageHidden(hidden);

      if (hidden && isRecording) {
        onPauseRecording?.();
      } else if (!hidden) {
        onResumeRecording?.();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [isRecording, onPauseRecording, onResumeRecording]);

  // ─── 网络状态检测 ───
  useEffect(() => {
    // 初始化时检测当前网络状态
    setIsOffline(!navigator.onLine);

    const handleOnline = () => {
      setIsOffline(false);
    };

    const handleOffline = () => {
      setIsOffline(true);
    };

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);

    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  // ─── 错误提示文本（优先级：mic denied > offline > page hidden） ───
  const errorMessage = useMemo((): string | null => {
    if (isMicDenied) {
      return ERROR_MSG_MIC_DENIED;
    }
    if (isOffline) {
      return ERROR_MSG_OFFLINE;
    }
    if (isPageHidden && isRecording) {
      return ERROR_MSG_PAGE_HIDDEN;
    }
    return null;
  }, [isMicDenied, isOffline, isPageHidden, isRecording]);

  return {
    isMicDenied,
    isPageHidden,
    isOffline,
    errorMessage,
    setMicDenied,
  };
}
