'use client';

import { memo, useState, useCallback, useEffect, useRef } from 'react';

import { VoiceWaveform } from '@/components/voice/VoiceWaveform';
import type { VoiceSessionState, RecordingMode } from '@/types/voice';

interface VoiceModePanelProps {
  /** 当前状态 */
  sessionState: VoiceSessionState;
  /** 是否正在录音 */
  isRecording: boolean;
  /** 当前音量级别 */
  volumeLevel: number;
  /** 录音时长（秒） */
  duration: number;
  /** 当前 AI 回复内容 */
  currentResponse: string;
  /** 当前转写文本 */
  currentTranscription: string;
  /** 错误信息 */
  error: string | null;
  /** 录音模式 */
  recordingMode: RecordingMode;
  /** 关闭语音模式 */
  onClose: () => void;
  /** 开始录音 */
  onStartRecording: () => void;
  /** 停止录音 */
  onStopRecording: () => void;
  /** 取消当前响应（停止 AI 回复） */
  onCancelResponse: () => void;
}

/** 状态文字映射 */
const STATUS_TEXT: Record<VoiceSessionState, string> = {
  idle: '',
  configuring: '连接中...',
  listening: '等待说话...',
  recording: '录音中',
  processing: '处理中...',
  responding: 'AI 回复中...',
  interrupted: '已中断',
  error: '出错了',
};

/** 声纹注册提示 Cookie 名称 */
const SPEAKER_ENROLL_DISMISSED_KEY = 'linchat_speaker_enroll_dismissed';

/** 读取 Cookie 值 */
function getCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match?.[1] != null ? decodeURIComponent(match[1]) : null;
}

/** 设置 Cookie（path=/linchat） */
function setCookie(name: string, value: string, days: number): void {
  const expires = new Date(Date.now() + days * 864e5).toUTCString();
  document.cookie = `${name}=${encodeURIComponent(value)};expires=${expires};path=/linchat`;
}

/** 格式化录音时长为 M:SS */
function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export const VoiceModePanel = memo(function VoiceModePanel({
  sessionState,
  isRecording,
  volumeLevel,
  duration,
  currentResponse,
  currentTranscription,
  error,
  recordingMode,
  onClose,
  onStartRecording,
  onStopRecording,
  onCancelResponse,
}: VoiceModePanelProps) {
  const [isVisible, setIsVisible] = useState(false);
  const [showEnrollTip, setShowEnrollTip] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  // slide-up 入场动画
  useEffect(() => {
    const timer = requestAnimationFrame(() => {
      setIsVisible(true);
    });
    return () => cancelAnimationFrame(timer);
  }, []);

  // 首次进入时显示声纹注册提示
  useEffect(() => {
    try {
      const dismissed = getCookie(SPEAKER_ENROLL_DISMISSED_KEY);
      if (!dismissed) {
        setShowEnrollTip(true);
      }
    } catch {
      // Cookie 读取失败时静默忽略
    }
  }, []);

  /** 关闭声纹注册提示 */
  const dismissEnrollTip = useCallback(() => {
    setShowEnrollTip(false);
    try {
      setCookie(SPEAKER_ENROLL_DISMISSED_KEY, 'true', 365);
    } catch {
      // 静默忽略
    }
  }, []);

  /** 关闭面板（带动画） */
  const handleClose = useCallback(() => {
    setIsVisible(false);
    // 等动画结束后再真正关闭
    setTimeout(() => {
      onClose();
    }, 300);
  }, [onClose]);

  /** 录音按钮 - 鼠标/触摸按下 */
  const handleRecordPointerDown = useCallback(() => {
    if (sessionState === 'responding') {
      onCancelResponse();
      return;
    }

    if (sessionState === 'processing') {
      return;
    }

    if (recordingMode === 'hold') {
      onStartRecording();
    } else {
      // toggle 模式：点击切换
      if (isRecording) {
        onStopRecording();
      } else {
        onStartRecording();
      }
    }
  }, [
    sessionState,
    recordingMode,
    isRecording,
    onStartRecording,
    onStopRecording,
    onCancelResponse,
  ]);

  /** 录音按钮 - 鼠标/触摸松开（仅 hold 模式） */
  const handleRecordPointerUp = useCallback(() => {
    if (recordingMode === 'hold' && isRecording) {
      onStopRecording();
    }
  }, [recordingMode, isRecording, onStopRecording]);

  /** 判断录音按钮是否可交互 */
  const isButtonDisabled =
    sessionState === 'configuring' || sessionState === 'processing';

  /** 录音按钮样式 */
  const getButtonClasses = (): string => {
    const base =
      'relative flex h-16 w-16 items-center justify-center rounded-full transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-800';

    if (isButtonDisabled) {
      return `${base} cursor-not-allowed bg-gray-600`;
    }

    if (sessionState === 'responding') {
      return `${base} bg-orange-500 hover:bg-orange-600 focus:ring-orange-400`;
    }

    if (isRecording) {
      return `${base} bg-red-500 hover:bg-red-600 focus:ring-red-400 animate-pulse`;
    }

    return `${base} bg-blue-500 hover:bg-blue-600 focus:ring-blue-400`;
  };

  /** 渲染录音按钮图标 */
  const renderButtonIcon = () => {
    // processing 状态：旋转 loading
    if (sessionState === 'processing') {
      return (
        <svg
          className="h-7 w-7 animate-spin text-gray-300"
          viewBox="0 0 24 24"
          fill="none"
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
      );
    }

    // responding 状态：停止按钮（方形）
    if (sessionState === 'responding') {
      return (
        <svg
          className="h-6 w-6 text-white"
          viewBox="0 0 24 24"
          fill="currentColor"
        >
          <rect x="6" y="6" width="12" height="12" rx="2" />
        </svg>
      );
    }

    // 默认：麦克风图标
    return (
      <svg
        className="h-7 w-7 text-white"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <rect x="9" y="1" width="6" height="12" rx="3" />
        <path d="M19 10v1a7 7 0 01-14 0v-1" />
        <line x1="12" y1="18" x2="12" y2="23" />
        <line x1="8" y1="23" x2="16" y2="23" />
      </svg>
    );
  };

  return (
    <div
      ref={panelRef}
      className={`fixed inset-x-0 bottom-0 z-50 transform transition-transform duration-300 ease-out ${
        isVisible ? 'translate-y-0' : 'translate-y-full'
      }`}
    >
      {/* 声纹注册提示 */}
      {showEnrollTip && (
        <div className="mx-4 mb-2 flex items-center justify-between rounded-lg bg-blue-900/80 px-4 py-2.5 text-sm text-blue-200 backdrop-blur">
          <span>建议注册声纹以支持共享设备使用</span>
          <button
            type="button"
            onClick={dismissEnrollTip}
            className="ml-3 flex-shrink-0 rounded p-1 text-blue-300 transition-colors hover:bg-blue-800 hover:text-white"
            aria-label="关闭提示"
          >
            <svg
              className="h-4 w-4"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      )}

      {/* 主面板 */}
      <div className="rounded-t-2xl border-t border-gray-700 bg-gray-800 px-6 pb-8 pt-4 shadow-2xl">
        {/* 关闭按钮 */}
        <button
          type="button"
          onClick={handleClose}
          className="absolute right-4 top-4 rounded-full p-1.5 text-gray-400 transition-colors hover:bg-gray-700 hover:text-white"
          aria-label="关闭语音模式"
        >
          <svg
            className="h-5 w-5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        {/* 状态文字 */}
        <div className="mb-3 text-center">
          <span className="text-sm text-gray-400">
            {STATUS_TEXT[sessionState]}
          </span>
        </div>

        {/* 转写文本 / AI 回复预览 */}
        {(currentTranscription || currentResponse) && (
          <div className="mx-auto mb-3 max-w-md">
            {currentTranscription && (
              <p className="truncate text-center text-sm text-gray-300">
                {currentTranscription}
              </p>
            )}
            {currentResponse && sessionState === 'responding' && (
              <p className="line-clamp-2 text-center text-sm text-blue-300">
                {currentResponse}
              </p>
            )}
          </div>
        )}

        {/* 波形 + 录音按钮 + 时长 */}
        <div className="flex flex-col items-center gap-3">
          {/* 波形区域（录音/listening 时显示） */}
          {(isRecording ||
            sessionState === 'listening' ||
            sessionState === 'recording') && (
            <VoiceWaveform
              volumeLevel={volumeLevel}
              isRecording={isRecording}
              width={240}
              height={60}
            />
          )}

          {/* 录音按钮 */}
          <button
            type="button"
            className={getButtonClasses()}
            disabled={isButtonDisabled}
            onPointerDown={handleRecordPointerDown}
            onPointerUp={handleRecordPointerUp}
            onPointerLeave={
              recordingMode === 'hold' && isRecording
                ? handleRecordPointerUp
                : undefined
            }
            aria-label={
              isRecording
                ? '停止录音'
                : sessionState === 'responding'
                  ? '停止回复'
                  : '开始录音'
            }
          >
            {renderButtonIcon()}
          </button>

          {/* 录音时长 */}
          {isRecording && (
            <span className="font-mono text-sm text-gray-300">
              {formatDuration(duration)}
            </span>
          )}

          {/* hold 模式提示 */}
          {recordingMode === 'hold' &&
            !isRecording &&
            (sessionState === 'idle' || sessionState === 'listening') && (
              <span className="text-xs text-gray-500">按住说话</span>
            )}

          {/* toggle 模式提示 */}
          {recordingMode === 'toggle' &&
            !isRecording &&
            (sessionState === 'idle' || sessionState === 'listening') && (
              <span className="text-xs text-gray-500">点击开始录音</span>
            )}
        </div>

        {/* 错误提示区域 */}
        {error && (
          <div className="mt-3 rounded-lg bg-red-900/50 px-4 py-2 text-center text-sm text-red-300">
            {error}
          </div>
        )}
      </div>
    </div>
  );
});
