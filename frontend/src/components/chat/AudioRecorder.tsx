/**
 * 音频录制组件 (T055)
 *
 * 功能：录音/停止/预览
 * - 录音时长校验：最短 1 秒、最长 60 秒
 * - 低于 1 秒提示"录音时间过短"阻止发送
 * - 到达 60 秒自动停止
 *
 * 参考: specs/008-multimodal-minicpm/tasks.md T055
 */
'use client';

import { memo, useCallback } from 'react';

import { useAudioRecorder } from '@/hooks/useAudioRecorder';
import { formatDuration, MEDIA_LIMITS } from '@/types/media';

interface AudioRecorderProps {
  /** 录音完成回调 */
  onRecordingComplete: (blob: Blob, duration: number) => void;
  /** 取消回调 */
  onCancel: () => void;
  /** 是否禁用 */
  disabled?: boolean;
}

export const AudioRecorder = memo(function AudioRecorder({
  onRecordingComplete,
  onCancel,
  disabled = false,
}: AudioRecorderProps) {
  const {
    status,
    duration,
    audioBlob,
    audioUrl,
    startRecording,
    stopRecording,
    reset,
    error,
  } = useAudioRecorder();

  const handleSend = useCallback(() => {
    if (audioBlob && duration >= 1) {
      onRecordingComplete(audioBlob, duration);
      reset();
    }
  }, [audioBlob, duration, onRecordingComplete, reset]);

  const handleCancel = useCallback(() => {
    reset();
    onCancel();
  }, [reset, onCancel]);

  return (
    <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-800">
      {/* 录音状态 */}
      {status === 'idle' && (
        <button
          onClick={startRecording}
          disabled={disabled}
          className="flex items-center gap-1.5 rounded-full bg-red-500 px-3 py-1.5 text-sm text-white transition-colors hover:bg-red-600 disabled:opacity-50"
        >
          <span className="h-2 w-2 rounded-full bg-white" />
          开始录音
        </button>
      )}

      {status === 'recording' && (
        <>
          {/* 录音动画 */}
          <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-red-500" />
          <span className="text-sm text-red-500">
            {formatDuration(duration)} / {formatDuration(MEDIA_LIMITS.MAX_DURATION_SECONDS)}
          </span>
          <button
            onClick={stopRecording}
            className="ml-2 rounded bg-gray-200 px-2 py-1 text-xs text-gray-600 hover:bg-gray-300 dark:bg-gray-600 dark:text-gray-300"
          >
            停止
          </button>
        </>
      )}

      {status === 'stopped' && audioUrl && (
        <>
          {/* 预览播放 */}
          <audio src={audioUrl} controls preload="metadata" className="h-8 max-w-[180px]" />
          <span className="text-xs text-gray-500">{formatDuration(duration)}</span>
          <button
            onClick={handleSend}
            className="rounded bg-primary-500 px-2 py-1 text-xs text-white hover:bg-primary-600"
          >
            发送
          </button>
        </>
      )}

      {/* 错误信息 */}
      {error && (
        <span className="text-xs text-red-500">{error}</span>
      )}

      {/* 取消按钮 */}
      <button
        onClick={handleCancel}
        className="ml-auto text-xs text-gray-400 hover:text-gray-600"
      >
        取消
      </button>
    </div>
  );
});
