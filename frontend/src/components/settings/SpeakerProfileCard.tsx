/**
 * 声纹管理卡片组件
 *
 * 展示当前用户声纹档案，支持注册新声纹和删除已有声纹。
 * 录音使用 usePCMAudioCapture Hook 采集 PCM16 帧，合并为 WAV 文件上传。
 * 参考: specs/009-voice-interaction/spec.md
 */
'use client';

import { memo, useCallback, useEffect, useRef, useState } from 'react';

import { usePCMAudioCapture } from '@/hooks/usePCMAudioCapture';
import {
  deleteSpeaker,
  enrollSpeaker,
  getSpeakerProfile,
} from '@/services/voiceApi';
import type { SpeakerProfile } from '@/types/voice';

/** 最小录音时长（秒） */
const MIN_DURATION = 10;
/** 最大录音时长（秒） */
const MAX_DURATION = 30;

/** 注册步骤 */
type EnrollStep = 'idle' | 'naming' | 'recording' | 'uploading' | 'done';

// ========== WAV 文件构建工具 ==========

/** 向 DataView 写入 ASCII 字符串 */
function writeString(view: DataView, offset: number, str: string): void {
  for (let i = 0; i < str.length; i++) {
    view.setUint8(offset + i, str.charCodeAt(i));
  }
}

/** 将 PCM16 帧数组合并为 WAV Blob */
function createWavBlob(pcmFrames: ArrayBuffer[]): Blob {
  const totalLength = pcmFrames.reduce(
    (sum, frame) => sum + frame.byteLength,
    0,
  );
  const pcmData = new Uint8Array(totalLength);
  let offset = 0;
  for (const frame of pcmFrames) {
    pcmData.set(new Uint8Array(frame), offset);
    offset += frame.byteLength;
  }

  const sampleRate = 16000;
  const numChannels = 1;
  const bitsPerSample = 16;
  const byteRate = sampleRate * numChannels * (bitsPerSample / 8);
  const blockAlign = numChannels * (bitsPerSample / 8);

  const wavHeader = new ArrayBuffer(44);
  const view = new DataView(wavHeader);

  // RIFF chunk
  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + totalLength, true);
  writeString(view, 8, 'WAVE');

  // fmt sub-chunk
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitsPerSample, true);

  // data sub-chunk
  writeString(view, 36, 'data');
  view.setUint32(40, totalLength, true);

  return new Blob([wavHeader, pcmData], { type: 'audio/wav' });
}

// ========== 子组件 ==========

/** 质量评分进度条 */
function QualityBar({ score }: { score: number | null }) {
  if (score === null) {
    return (
      <span className="text-xs text-gray-400">质量评分：未评估</span>
    );
  }

  const percent = Math.round(score * 100);
  const barColor =
    percent >= 80
      ? 'bg-green-500'
      : percent >= 50
        ? 'bg-yellow-500'
        : 'bg-red-500';

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-500 dark:text-gray-400">
        质量评分
      </span>
      <div className="h-2 w-24 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="text-xs font-medium text-gray-700 dark:text-gray-300">
        {percent}%
      </span>
    </div>
  );
}

/** 录音进度条 */
function RecordingProgress({ duration }: { duration: number }) {
  const percent = Math.min((duration / MAX_DURATION) * 100, 100);
  const meetsMinimum = duration >= MIN_DURATION;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-sm">
        <span className="text-gray-600 dark:text-gray-300">
          录音中 {duration}s / {MAX_DURATION}s
        </span>
        {meetsMinimum ? (
          <span className="text-green-600 dark:text-green-400">
            已满足最低时长
          </span>
        ) : (
          <span className="text-amber-600 dark:text-amber-400">
            至少需录制 {MIN_DURATION} 秒
          </span>
        )}
      </div>
      <div className="h-2.5 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
        <div
          className={`h-full rounded-full transition-all ${
            meetsMinimum ? 'bg-green-500' : 'bg-amber-500'
          }`}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

/** 删除确认对话框 */
function DeleteConfirmDialog({
  onConfirm,
  onCancel,
  isDeleting,
}: {
  onConfirm: () => void;
  onCancel: () => void;
  isDeleting: boolean;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="mx-4 w-full max-w-sm rounded-xl border border-gray-200 bg-white p-6 shadow-lg dark:border-gray-700 dark:bg-gray-800">
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
          确认删除声纹
        </h3>
        <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">
          删除后将无法通过声纹识别身份，需要重新录制注册。确定要删除吗？
        </p>
        <div className="mt-4 flex justify-end gap-3">
          <button
            onClick={onCancel}
            disabled={isDeleting}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 transition-colors hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
          >
            取消
          </button>
          <button
            onClick={onConfirm}
            disabled={isDeleting}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm text-white transition-colors hover:bg-red-700 disabled:opacity-50"
          >
            {isDeleting ? '删除中...' : '确认删除'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ========== 主组件 ==========

export const SpeakerProfileCard = memo(function SpeakerProfileCard() {
  // ---------- 状态 ----------
  const [speaker, setSpeaker] = useState<SpeakerProfile | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [enrollName, setEnrollName] = useState('');
  const [, setIsEnrolling] = useState(false);
  const [enrollStep, setEnrollStep] = useState<EnrollStep>('idle');
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [volumeLevel, setVolumeLevel] = useState(0);

  // PCM 帧收集：使用 ref 避免闭包问题
  const pcmFramesRef = useRef<ArrayBuffer[]>([]);

  // ---------- 音频采集 ----------
  const handleAudioData = useCallback((pcmData: ArrayBuffer) => {
    pcmFramesRef.current.push(pcmData);
  }, []);

  const handleVolumeLevel = useCallback((level: number) => {
    setVolumeLevel(level);
  }, []);

  const {
    isCapturing,
    duration,
    error: captureError,
    startCapture,
    stopCapture,
  } = usePCMAudioCapture({
    onAudioData: handleAudioData,
    onVolumeLevel: handleVolumeLevel,
    maxDuration: MAX_DURATION,
  });

  // ---------- 加载声纹 ----------
  const loadProfile = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await getSpeakerProfile();
      setSpeaker(response.data ?? null);
    } catch {
      // 404 或其他错误表示未注册
      setSpeaker(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  // 监听录音停止（maxDuration 自动停止或手动停止），触发上传
  const prevCapturingRef = useRef(false);
  useEffect(() => {
    // 检测 isCapturing 从 true -> false 的变化，且处于 recording 步骤
    if (prevCapturingRef.current && !isCapturing && enrollStep === 'recording') {
      // 录音已停止，检查时长并上传
      handleRecordingFinished();
    }
    prevCapturingRef.current = isCapturing;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isCapturing, enrollStep]);

  // ---------- 注册流程 ----------

  /** 开始注册：进入命名步骤 */
  const handleStartEnroll = useCallback(() => {
    setEnrollStep('naming');
    setEnrollName('');
    setError(null);
    pcmFramesRef.current = [];
  }, []);

  /** 取消注册 */
  const handleCancelEnroll = useCallback(() => {
    if (isCapturing) {
      stopCapture();
    }
    pcmFramesRef.current = [];
    setEnrollStep('idle');
    setEnrollName('');
    setError(null);
    setIsEnrolling(false);
    setVolumeLevel(0);
  }, [isCapturing, stopCapture]);

  /** 命名完成，开始录音 */
  const handleStartRecording = useCallback(async () => {
    if (!enrollName.trim()) {
      setError('请输入声纹名称');
      return;
    }
    setError(null);
    pcmFramesRef.current = [];
    setVolumeLevel(0);
    setEnrollStep('recording');
    await startCapture();
  }, [enrollName, startCapture]);

  /** 手动停止录音 */
  const handleStopRecording = useCallback(() => {
    stopCapture();
  }, [stopCapture]);

  /** 录音结束后处理上传 */
  const handleRecordingFinished = useCallback(async () => {
    const frames = pcmFramesRef.current;

    // 计算实际录音时长（每帧 960 bytes = 480 samples @ 16000Hz = 30ms）
    const totalSamples = frames.reduce(
      (sum, frame) => sum + frame.byteLength / 2,
      0,
    );
    const recordedDuration = totalSamples / 16000;

    if (recordedDuration < MIN_DURATION) {
      setError(`录音时长不足，至少需要 ${MIN_DURATION} 秒（当前 ${Math.floor(recordedDuration)} 秒）`);
      setEnrollStep('naming');
      pcmFramesRef.current = [];
      return;
    }

    // 构建 WAV 文件并上传
    setEnrollStep('uploading');
    setIsEnrolling(true);
    setError(null);

    try {
      const wavBlob = createWavBlob(frames);
      const response = await enrollSpeaker(enrollName.trim(), wavBlob);
      setSpeaker(response.data);
      setEnrollStep('done');
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : '声纹注册失败，请重试';
      setError(message);
      setEnrollStep('naming');
    } finally {
      setIsEnrolling(false);
      pcmFramesRef.current = [];
    }
  }, [enrollName]);

  // ---------- 删除声纹 ----------

  const handleDeleteConfirm = useCallback(async () => {
    setIsDeleting(true);
    setError(null);
    try {
      await deleteSpeaker();
      setSpeaker(null);
      setShowDeleteConfirm(false);
      setEnrollStep('idle');
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : '删除失败，请重试';
      setError(message);
    } finally {
      setIsDeleting(false);
    }
  }, []);

  // ---------- 渲染 ----------

  return (
    <>
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800">
        {/* 卡片头部 */}
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
            声纹管理
          </h3>
          {speaker && enrollStep === 'idle' && (
            <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-700 dark:bg-green-900 dark:text-green-300">
              <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
              已注册
            </span>
          )}
        </div>

        {/* 错误提示 */}
        {(error || captureError) && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
            {error || captureError}
          </div>
        )}

        {/* 加载状态 */}
        {isLoading && (
          <div className="flex items-center justify-center py-8">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-primary-500" />
            <span className="ml-2 text-sm text-gray-500 dark:text-gray-400">
              加载声纹信息...
            </span>
          </div>
        )}

        {/* 已注册声纹展示 */}
        {!isLoading && speaker && enrollStep !== 'done' && (
          <div className="space-y-4">
            <div className="rounded-lg border border-gray-100 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-900/50">
              <div className="flex items-start justify-between">
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-900 dark:text-white">
                      {speaker.name}
                    </span>
                  </div>
                  <QualityBar score={speaker.qualityScore} />
                  <span className="text-xs text-gray-400">
                    注册时间：{new Date(speaker.enrolledAt).toLocaleString('zh-CN')}
                  </span>
                </div>
                <button
                  onClick={() => setShowDeleteConfirm(true)}
                  className="shrink-0 rounded-lg border border-red-300 px-3 py-1.5 text-sm text-red-600 transition-colors hover:bg-red-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-900/20"
                >
                  删除
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 未注册 - 显示注册入口 */}
        {!isLoading && !speaker && enrollStep === 'idle' && (
          <div className="space-y-4">
            <p className="text-sm text-gray-600 dark:text-gray-400">
              尚未注册声纹。注册声纹后，系统可通过语音识别您的身份。
            </p>
            <button
              onClick={handleStartEnroll}
              className="rounded-lg bg-primary-500 px-4 py-2 text-sm text-white transition-colors hover:bg-primary-600"
            >
              注册声纹
            </button>
          </div>
        )}

        {/* 注册成功 */}
        {enrollStep === 'done' && (
          <div className="space-y-4">
            <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700 dark:border-green-800 dark:bg-green-900/20 dark:text-green-400">
              声纹注册成功！
            </div>
            <button
              onClick={() => {
                setEnrollStep('idle');
                loadProfile();
              }}
              className="rounded-lg bg-primary-500 px-4 py-2 text-sm text-white transition-colors hover:bg-primary-600"
            >
              完成
            </button>
          </div>
        )}

        {/* 命名步骤 */}
        {enrollStep === 'naming' && (
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                声纹名称
              </label>
              <input
                type="text"
                value={enrollName}
                onChange={(e) => setEnrollName(e.target.value)}
                placeholder="例如：我的声纹"
                maxLength={50}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 dark:border-gray-600 dark:bg-gray-900 dark:text-white dark:placeholder-gray-500"
              />
            </div>
            <p className="text-xs text-gray-400">
              录音需 {MIN_DURATION}-{MAX_DURATION} 秒，请在安静环境下清晰朗读一段文字。
            </p>
            <div className="flex gap-3">
              <button
                onClick={handleStartRecording}
                disabled={!enrollName.trim()}
                className="rounded-lg bg-primary-500 px-4 py-2 text-sm text-white transition-colors hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                开始录音
              </button>
              <button
                onClick={handleCancelEnroll}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 transition-colors hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
              >
                取消
              </button>
            </div>
          </div>
        )}

        {/* 录音步骤 */}
        {enrollStep === 'recording' && (
          <div className="space-y-4">
            <RecordingProgress duration={duration} />

            {/* 音量指示器 */}
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500 dark:text-gray-400">
                音量
              </span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
                <div
                  className="h-full rounded-full bg-primary-500 transition-all duration-75"
                  style={{ width: `${Math.min(volumeLevel * 100, 100)}%` }}
                />
              </div>
            </div>

            <div className="flex items-center gap-3">
              <button
                onClick={handleStopRecording}
                disabled={duration < MIN_DURATION}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm text-white transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                停止录音
              </button>
              {duration < MIN_DURATION && (
                <span className="text-xs text-amber-600 dark:text-amber-400">
                  还需录制 {MIN_DURATION - duration} 秒
                </span>
              )}
            </div>

            {/* 录音动画指示点 */}
            <div className="flex items-center gap-1.5">
              <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
              <span className="text-xs text-gray-500 dark:text-gray-400">
                正在录音，请保持朗读...
              </span>
            </div>
          </div>
        )}

        {/* 上传步骤 */}
        {enrollStep === 'uploading' && (
          <div className="flex items-center justify-center py-8">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-primary-500" />
            <span className="ml-2 text-sm text-gray-500 dark:text-gray-400">
              正在注册声纹...
            </span>
          </div>
        )}
      </div>

      {/* 删除确认对话框 */}
      {showDeleteConfirm && (
        <DeleteConfirmDialog
          onConfirm={handleDeleteConfirm}
          onCancel={() => setShowDeleteConfirm(false)}
          isDeleting={isDeleting}
        />
      )}
    </>
  );
});
