/**
 * 声纹录音组件 (T046)
 *
 * 015-family-multiuser Phase 6:
 * - 浏览器 MediaRecorder 录音，用于声纹注册
 * - 10-30 秒录音范围，进度条显示
 * - 状态机: idle -> recording -> completed
 * - visibilitychange 事件暂停录音并提示重新录制
 *
 * UI 风格参考 AudioRecorder.tsx
 */
'use client';

import { memo, useCallback, useEffect, useRef, useState } from 'react';

/** 声纹录音最小时长（秒） */
const MIN_DURATION = 10;
/** 声纹录音最大时长（秒） */
const MAX_DURATION = 30;

type RecorderState = 'idle' | 'recording' | 'completed';

interface VoiceprintRecorderProps {
  onRecordingComplete: (audioBlob: Blob) => void;
  disabled?: boolean;
}

export const VoiceprintRecorder = memo(function VoiceprintRecorder({
  onRecordingComplete,
  disabled = false,
}: VoiceprintRecorderProps) {
  const [state, setState] = useState<RecorderState>('idle');
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const startTimeRef = useRef<number>(0);

  // 清理计时器
  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // 停止并释放媒体流
  const releaseStream = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  }, []);

  // 完整清理
  const cleanup = useCallback(() => {
    clearTimer();
    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.stop();
    }
    mediaRecorderRef.current = null;
    releaseStream();
  }, [clearTimer, releaseStream]);

  // 内部停止录音并生成 Blob（由 stopRecording 和自动停止调用）
  const finalizeRecording = useCallback(
    (recorder: MediaRecorder) => {
      if (recorder.state === 'recording') {
        recorder.stop();
      }
      clearTimer();
    },
    [clearTimer]
  );

  // 开始录音
  const startRecording = useCallback(async () => {
    setError(null);
    setDuration(0);
    chunksRef.current = [];

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const recorder = new MediaRecorder(stream, {
        mimeType: 'audio/webm',
      });
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          chunksRef.current.push(e.data);
        }
      };

      recorder.onstop = () => {
        clearTimer();
        releaseStream();

        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        const finalDuration = Math.floor(
          (Date.now() - startTimeRef.current) / 1000
        );

        if (finalDuration < MIN_DURATION) {
          setError(
            `录音时间不足 ${MIN_DURATION} 秒（当前 ${finalDuration} 秒），请重新录制`
          );
          setState('idle');
          return;
        }

        setDuration(finalDuration);
        setState('completed');
        onRecordingComplete(blob);
      };

      recorder.start();
      startTimeRef.current = Date.now();
      setState('recording');

      // 启动计时器
      timerRef.current = setInterval(() => {
        const elapsed = Math.floor(
          (Date.now() - startTimeRef.current) / 1000
        );
        setDuration(elapsed);

        // 到达最大时长自动停止
        if (elapsed >= MAX_DURATION) {
          finalizeRecording(recorder);
        }
      }, 1000);
    } catch {
      setError('无法访问麦克风，请检查浏览器权限设置');
      setState('idle');
    }
  }, [clearTimer, releaseStream, finalizeRecording, onRecordingComplete]);

  // 手动完成录音
  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current) {
      finalizeRecording(mediaRecorderRef.current);
    }
  }, [finalizeRecording]);

  // 取消录音
  const cancelRecording = useCallback(() => {
    cleanup();
    chunksRef.current = [];
    setDuration(0);
    setError(null);
    setState('idle');
  }, [cleanup]);

  // 重新录制
  const resetRecording = useCallback(() => {
    cleanup();
    chunksRef.current = [];
    setDuration(0);
    setError(null);
    setState('idle');
  }, [cleanup]);

  // 页面可见性变化时停止录音
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (
        document.hidden &&
        mediaRecorderRef.current?.state === 'recording'
      ) {
        cleanup();
        chunksRef.current = [];
        setDuration(0);
        setState('idle');
        setError('页面切换导致录音中断，请重新录制');
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [cleanup]);

  // 组件卸载时清理
  useEffect(() => {
    return () => {
      cleanup();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 进度百分比（基于最大时长）
  const progressPercent = Math.min((duration / MAX_DURATION) * 100, 100);
  // 最低线位置百分比
  const minLinePercent = (MIN_DURATION / MAX_DURATION) * 100;
  // 是否达到最低录音时长
  const reachedMinDuration = duration >= MIN_DURATION;

  return (
    <div className="space-y-4">
      {/* idle 状态 */}
      {state === 'idle' && (
        <div className="flex flex-col items-center rounded-xl border-2 border-dashed border-gray-300 px-6 py-8 text-center dark:border-gray-600">
          <svg
            className="mb-3 h-12 w-12 text-gray-400 dark:text-gray-500"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4M12 15a3 3 0 003-3V5a3 3 0 00-6 0v7a3 3 0 003 3z"
            />
          </svg>
          <p className="mb-1 text-sm font-medium text-gray-600 dark:text-gray-300">
            请朗读一段话
          </p>
          <p className="mb-4 text-xs text-gray-400 dark:text-gray-500">
            最少 {MIN_DURATION} 秒，最多 {MAX_DURATION} 秒
          </p>
          <button
            onClick={startRecording}
            disabled={disabled}
            className="flex items-center gap-2 rounded-full bg-red-500 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-red-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span className="h-2.5 w-2.5 rounded-full bg-white" />
            开始录音
          </button>
        </div>
      )}

      {/* recording 状态 */}
      {state === 'recording' && (
        <div className="rounded-xl border-2 border-red-200 bg-red-50/50 px-6 py-6 dark:border-red-800/50 dark:bg-red-900/10">
          {/* 录音动画和时长 */}
          <div className="mb-4 flex items-center justify-center gap-2">
            <span className="h-3 w-3 animate-pulse rounded-full bg-red-500" />
            <span className="text-lg font-semibold text-red-600 dark:text-red-400">
              {duration}s / {MAX_DURATION}s
            </span>
          </div>

          {/* 进度条 */}
          <div className="relative mb-2 h-3 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
            {/* 已录制进度 */}
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                reachedMinDuration
                  ? 'bg-green-500'
                  : 'bg-red-400'
              }`}
              style={{ width: `${progressPercent}%` }}
            />
            {/* 最低线标记 */}
            <div
              className="absolute top-0 h-full w-0.5 bg-gray-600 dark:bg-gray-300"
              style={{ left: `${minLinePercent}%` }}
            />
          </div>

          {/* 进度条标签 */}
          <div className="mb-4 flex justify-between text-xs text-gray-500 dark:text-gray-400">
            <span>0s</span>
            <span
              className={`font-medium ${
                reachedMinDuration
                  ? 'text-green-600 dark:text-green-400'
                  : 'text-gray-500'
              }`}
              style={{ marginLeft: `${minLinePercent - 5}%` }}
            >
              {MIN_DURATION}s 最低
            </span>
            <span>{MAX_DURATION}s</span>
          </div>

          {/* 状态提示 */}
          <p className="mb-4 text-center text-xs text-gray-500 dark:text-gray-400">
            {reachedMinDuration
              ? '已达到最低时长，可以完成录音'
              : `还需要 ${MIN_DURATION - duration} 秒`}
          </p>

          {/* 操作按钮 */}
          <div className="flex justify-center gap-3">
            <button
              onClick={cancelRecording}
              className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 transition-colors hover:bg-gray-100 dark:border-gray-600 dark:text-gray-400 dark:hover:bg-gray-700"
            >
              取消
            </button>
            <button
              onClick={stopRecording}
              disabled={!reachedMinDuration}
              className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                reachedMinDuration
                  ? 'bg-green-500 text-white hover:bg-green-600'
                  : 'cursor-not-allowed bg-gray-200 text-gray-400 dark:bg-gray-700 dark:text-gray-500'
              }`}
            >
              完成录音
            </button>
          </div>
        </div>
      )}

      {/* completed 状态 */}
      {state === 'completed' && (
        <div className="flex flex-col items-center rounded-xl border-2 border-green-200 bg-green-50/50 px-6 py-6 dark:border-green-800/50 dark:bg-green-900/10">
          <svg
            className="mb-2 h-10 w-10 text-green-500"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
          <p className="mb-1 text-sm font-semibold text-green-700 dark:text-green-400">
            录音完成
          </p>
          <p className="mb-3 text-xs text-gray-500 dark:text-gray-400">
            时长 {duration} 秒
          </p>
          <button
            onClick={resetRecording}
            className="text-sm text-primary-500 underline transition-colors hover:text-primary-600"
          >
            重新录制
          </button>
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600 dark:bg-red-900/20 dark:text-red-400">
          {error}
        </div>
      )}
    </div>
  );
});
