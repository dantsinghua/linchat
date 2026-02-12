/**
 * 音频录制 Hook (T056)
 *
 * 管理 MediaRecorder 生命周期：
 * - 开始/停止录音
 * - 录音时长实时显示和校验（1-60 秒）
 * - 输出 audio/webm Blob 文件
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { MEDIA_LIMITS } from '@/types/media';

export type RecordingStatus = 'idle' | 'recording' | 'stopped';

interface UseAudioRecorderReturn {
  /** 录音状态 */
  status: RecordingStatus;
  /** 已录制时长（秒） */
  duration: number;
  /** 录音结果 Blob */
  audioBlob: Blob | null;
  /** 录音结果本地 URL */
  audioUrl: string | null;
  /** 开始录音 */
  startRecording: () => Promise<void>;
  /** 停止录音 */
  stopRecording: () => void;
  /** 重置状态 */
  reset: () => void;
  /** 错误信息 */
  error: string | null;
}

export function useAudioRecorder(): UseAudioRecorderReturn {
  const [status, setStatus] = useState<RecordingStatus>('idle');
  const [duration, setDuration] = useState(0);
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
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

  // 开始录音
  const startRecording = useCallback(async () => {
    setError(null);
    setAudioBlob(null);
    if (audioUrl) {
      URL.revokeObjectURL(audioUrl);
      setAudioUrl(null);
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, {
        mimeType: 'audio/webm',
      });

      chunksRef.current = [];
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          chunksRef.current.push(e.data);
        }
      };

      recorder.onstop = () => {
        clearTimer();
        // 停止所有音轨
        stream.getTracks().forEach((t) => t.stop());

        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        const finalDuration = (Date.now() - startTimeRef.current) / 1000;

        if (finalDuration < 1) {
          setError('录音时间过短（最短 1 秒）');
          setStatus('idle');
          return;
        }

        setAudioBlob(blob);
        setAudioUrl(URL.createObjectURL(blob));
        setDuration(Math.round(finalDuration));
        setStatus('stopped');
      };

      recorder.start();
      startTimeRef.current = Date.now();
      setDuration(0);
      setStatus('recording');

      // 启动计时器
      timerRef.current = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTimeRef.current) / 1000);
        setDuration(elapsed);

        // 到达最大时长自动停止
        if (elapsed >= MEDIA_LIMITS.MAX_DURATION_SECONDS) {
          recorder.stop();
        }
      }, 1000);
    } catch {
      setError('无法访问麦克风，请检查权限设置');
      setStatus('idle');
    }
  }, [audioUrl, clearTimer]);

  // 停止录音
  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.stop();
    }
    clearTimer();
  }, [clearTimer]);

  // 重置
  const reset = useCallback(() => {
    stopRecording();
    if (audioUrl) {
      URL.revokeObjectURL(audioUrl);
    }
    setStatus('idle');
    setDuration(0);
    setAudioBlob(null);
    setAudioUrl(null);
    setError(null);
    chunksRef.current = [];
  }, [stopRecording, audioUrl]);

  // 组件卸载时清理
  useEffect(() => {
    return () => {
      clearTimer();
      if (mediaRecorderRef.current?.state === 'recording') {
        mediaRecorderRef.current.stop();
      }
    };
  }, [clearTimer]);

  return {
    status,
    duration,
    audioBlob,
    audioUrl,
    startRecording,
    stopRecording,
    reset,
    error,
  };
}
