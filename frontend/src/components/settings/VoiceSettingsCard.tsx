/**
 * 语音设置卡片组件
 *
 * 管理唤醒词、录音模式、VAD 灵敏度等语音交互参数。
 * 参考: specs/009-voice-interaction/spec.md
 */
'use client';

import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { getVoiceSettings, updateVoiceSettings } from '@/services/voiceApi';
import type { RecordingMode, VoiceSettings } from '@/types/voice';

/** 唤醒词数量上限 */
const MAX_WAKE_WORDS = 5;
/** 单个唤醒词最大字符数 */
const MAX_WAKE_WORD_LENGTH = 20;

/** VAD 灵敏度等级标签 */
function getSensitivityLabel(value: number): string {
  if (value <= 0.3) return '低';
  if (value <= 0.6) return '中';
  return '高';
}

// ========== 主组件 ==========

export const VoiceSettingsCard = memo(function VoiceSettingsCard() {
  // ---------- 状态 ----------
  const [settings, setSettings] = useState<VoiceSettings | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // 表单状态
  const [wakeWords, setWakeWords] = useState<string[]>([]);
  const [newWakeWord, setNewWakeWord] = useState('');
  const [recordingMode, setRecordingMode] = useState<RecordingMode>('hold');
  const [vadSensitivity, setVadSensitivity] = useState(0.5);

  // 唤醒词输入框 ref，用于添加后聚焦
  const wakeWordInputRef = useRef<HTMLInputElement>(null);

  // ---------- 加载设置 ----------
  const loadSettings = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await getVoiceSettings();
      const data = response.data;
      if (data) {
        setSettings(data);
        setWakeWords(data.wakeWords ?? []);
        setRecordingMode(data.recordingMode ?? 'hold');
        setVadSensitivity(data.vadSensitivity ?? 0.5);
      }
    } catch {
      setError('加载语音设置失败，请刷新重试');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  // ---------- 变更检测 ----------
  const hasChanges = useMemo(() => {
    if (!settings) return false;
    const origWords = settings.wakeWords ?? [];
    if (origWords.length !== wakeWords.length) return true;
    if (!origWords.every((w, i) => w === wakeWords[i])) return true;
    if (recordingMode !== (settings.recordingMode ?? 'hold')) return true;
    if (vadSensitivity !== (settings.vadSensitivity ?? 0.5)) return true;
    return false;
  }, [settings, wakeWords, recordingMode, vadSensitivity]);

  // ---------- 唤醒词管理 ----------
  const handleAddWakeWord = useCallback(() => {
    const trimmed = newWakeWord.trim();
    if (!trimmed) return;
    if (trimmed.length > MAX_WAKE_WORD_LENGTH) {
      setError(`唤醒词不能超过 ${MAX_WAKE_WORD_LENGTH} 个字符`);
      return;
    }
    if (wakeWords.length >= MAX_WAKE_WORDS) {
      setError(`最多添加 ${MAX_WAKE_WORDS} 个唤醒词`);
      return;
    }
    if (wakeWords.includes(trimmed)) {
      setError('该唤醒词已存在');
      return;
    }
    setError(null);
    setSuccessMsg(null);
    setWakeWords((prev) => [...prev, trimmed]);
    setNewWakeWord('');
    wakeWordInputRef.current?.focus();
  }, [newWakeWord, wakeWords]);

  const handleRemoveWakeWord = useCallback((index: number) => {
    setError(null);
    setSuccessMsg(null);
    setWakeWords((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleWakeWordKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        handleAddWakeWord();
      }
    },
    [handleAddWakeWord],
  );

  // ---------- 保存 ----------
  const handleSave = useCallback(async () => {
    setIsSaving(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const response = await updateVoiceSettings({
        wake_words: wakeWords,
        recording_mode: recordingMode,
        vad_sensitivity: vadSensitivity,
      });
      const data = response.data;
      if (data) {
        setSettings(data);
        setWakeWords(data.wakeWords ?? []);
        setRecordingMode(data.recordingMode ?? 'hold');
        setVadSensitivity(data.vadSensitivity ?? 0.5);
      }
      setSuccessMsg('语音设置已保存');
      // 3 秒后自动隐藏成功提示
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch {
      setError('保存失败，请重试');
    } finally {
      setIsSaving(false);
    }
  }, [wakeWords, recordingMode, vadSensitivity]);

  // ---------- 渲染 ----------

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800">
      {/* 卡片头部 */}
      <div className="mb-4">
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
          语音设置
        </h3>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          配置唤醒词、录音模式和语音检测灵敏度
        </p>
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
          {error}
        </div>
      )}

      {/* 成功提示 */}
      {successMsg && (
        <div className="mb-4 rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700 dark:border-green-800 dark:bg-green-900/20 dark:text-green-400">
          {successMsg}
        </div>
      )}

      {/* 加载状态 */}
      {isLoading && (
        <div className="flex items-center justify-center py-8">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-primary-500" />
          <span className="ml-2 text-sm text-gray-500 dark:text-gray-400">
            加载语音设置...
          </span>
        </div>
      )}

      {/* 设置表单 */}
      {!isLoading && (
        <div className="space-y-6">
          {/* 唤醒词管理 */}
          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
              唤醒词
              <span className="ml-1 text-xs font-normal text-gray-400">
                （最多 {MAX_WAKE_WORDS} 个，每个不超过 {MAX_WAKE_WORD_LENGTH} 字符）
              </span>
            </label>

            {/* 唤醒词标签列表 */}
            {wakeWords.length > 0 && (
              <div className="mb-3 flex flex-wrap gap-2">
                {wakeWords.map((word, index) => (
                  <span
                    key={`${word}-${index}`}
                    className="inline-flex items-center gap-1 rounded-full bg-primary-50 px-3 py-1 text-sm text-primary-700 dark:bg-primary-900/30 dark:text-primary-300"
                  >
                    {word}
                    <button
                      type="button"
                      onClick={() => handleRemoveWakeWord(index)}
                      className="ml-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full text-primary-500 transition-colors hover:bg-primary-200 hover:text-primary-700 dark:hover:bg-primary-800 dark:hover:text-primary-200"
                      aria-label={`删除唤醒词 ${word}`}
                    >
                      <svg
                        className="h-3 w-3"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth={2}
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M6 18L18 6M6 6l12 12"
                        />
                      </svg>
                    </button>
                  </span>
                ))}
              </div>
            )}

            {/* 添加唤醒词输入 */}
            <div className="flex gap-2">
              <input
                ref={wakeWordInputRef}
                type="text"
                value={newWakeWord}
                onChange={(e) => setNewWakeWord(e.target.value)}
                onKeyDown={handleWakeWordKeyDown}
                placeholder="输入唤醒词，按回车添加"
                maxLength={MAX_WAKE_WORD_LENGTH}
                disabled={wakeWords.length >= MAX_WAKE_WORDS}
                className="flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-600 dark:bg-gray-900 dark:text-white dark:placeholder-gray-500"
              />
              <button
                type="button"
                onClick={handleAddWakeWord}
                disabled={
                  !newWakeWord.trim() || wakeWords.length >= MAX_WAKE_WORDS
                }
                className="shrink-0 rounded-lg bg-primary-500 px-4 py-2 text-sm text-white transition-colors hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                添加
              </button>
            </div>
          </div>

          {/* 分隔线 */}
          <hr className="border-gray-200 dark:border-gray-700" />

          {/* 录音模式选择 */}
          <div>
            <label className="mb-3 block text-sm font-medium text-gray-700 dark:text-gray-300">
              录音模式
            </label>
            <div className="space-y-3">
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="radio"
                  name="recordingMode"
                  value="hold"
                  checked={recordingMode === 'hold'}
                  onChange={() => {
                    setRecordingMode('hold');
                    setSuccessMsg(null);
                  }}
                  className="mt-0.5 h-4 w-4 border-gray-300 text-primary-500 focus:ring-primary-500"
                />
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-white">
                    按住说话
                  </span>
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    按住录音按钮时录音，松开后自动发送
                  </p>
                </div>
              </label>
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="radio"
                  name="recordingMode"
                  value="toggle"
                  checked={recordingMode === 'toggle'}
                  onChange={() => {
                    setRecordingMode('toggle');
                    setSuccessMsg(null);
                  }}
                  className="mt-0.5 h-4 w-4 border-gray-300 text-primary-500 focus:ring-primary-500"
                />
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-white">
                    点击切换
                  </span>
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    点击一次开始录音，再次点击停止并发送
                  </p>
                </div>
              </label>
            </div>
          </div>

          {/* 分隔线 */}
          <hr className="border-gray-200 dark:border-gray-700" />

          {/* VAD 灵敏度 */}
          <div>
            <div className="mb-3 flex items-center justify-between">
              <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                语音检测灵敏度 (VAD)
              </label>
              <span className="rounded-md bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700 dark:bg-gray-700 dark:text-gray-300">
                {vadSensitivity.toFixed(1)} - {getSensitivityLabel(vadSensitivity)}
              </span>
            </div>

            {/* 滑块 */}
            <input
              type="range"
              min="0"
              max="1"
              step="0.1"
              value={vadSensitivity}
              onChange={(e) => {
                setVadSensitivity(parseFloat(e.target.value));
                setSuccessMsg(null);
              }}
              className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-gray-200 accent-primary-500 dark:bg-gray-700"
            />

            {/* 刻度标签 */}
            <div className="mt-1 flex justify-between text-xs text-gray-400">
              <span>低</span>
              <span>中</span>
              <span>高</span>
            </div>

            <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
              灵敏度越高，越容易触发语音检测；灵敏度越低，需要更大的声音才会触发
            </p>
          </div>

          {/* 分隔线 */}
          <hr className="border-gray-200 dark:border-gray-700" />

          {/* 保存按钮 */}
          <div className="flex justify-end">
            <button
              type="button"
              onClick={handleSave}
              disabled={!hasChanges || isSaving}
              className="rounded-lg bg-primary-500 px-6 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSaving ? '保存中...' : '保存设置'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
});
