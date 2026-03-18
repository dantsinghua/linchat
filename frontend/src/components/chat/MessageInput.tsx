/**
 * 消息输入组件
 *
 * 参考:
 * - rule-model.md#R_MSG_001 - 消息长度限制 4000 字符
 * - rule-model.md#R_MSG_002 - 空消息拦截
 * - spec.md US2场景9 - 停止按钮
 * - T029: 支持图片上传按钮（多模态附件）
 */
'use client';

import { memo, useCallback, useEffect, useRef, useState } from 'react';

import { AudioRecorder } from '@/components/chat/AudioRecorder';
import {
  MediaUploader,
  type MediaUploaderRef,
} from '@/components/chat/MediaUploader';
import { uploadMedia } from '@/services/mediaApi';
import { useUploadStore, createUploadTask } from '@/stores/uploadStore';
import { MEDIA_LIMITS } from '@/types/media';
import type { MediaAttachment } from '@/types/media';

const MAX_LENGTH = 4000;
const DEBOUNCE_MS = 300;
const STOP_DEBOUNCE_MS = 500;

interface MessageInputProps {
  isGenerating: boolean;
  disabled?: boolean;
  failedContent?: string | null;
  failedAttachments?: MediaAttachment[] | null;
  /** 语音模式是否开启 */
  voiceMode?: boolean;
  /** 成员类型（member 时显示头像按钮） */
  memberType?: 'member' | 'guest';
  /** 打开成员切换模态框 */
  onOpenMemberModal?: () => void;
  /** 当前操作用户的首字母 */
  currentAvatarLetter?: string;
  /** 当前操作用户的头像背景色 */
  currentAvatarColor?: string;
  onSend: (content: string, attachments?: MediaAttachment[]) => Promise<void>;
  onStop: () => Promise<void>;
  onClearFailedContent?: () => void;
  /** 切换语音模式 */
  onToggleVoiceMode?: () => void;
}

/**
 * 消息输入组件
 *
 * 功能：
 * - 空消息拦截（trim 后校验）
 * - 长度限制（4000 字符）
 * - 发送按钮 / 停止按钮切换
 * - 防抖处理（300ms）
 * - 多模态文件上传（T029）
 */
export const MessageInput = memo(function MessageInput({
  isGenerating,
  disabled = false,
  failedContent,
  failedAttachments,
  voiceMode = false,
  memberType,
  onOpenMemberModal,
  currentAvatarLetter,
  currentAvatarColor,
  onSend,
  onStop,
  onClearFailedContent,
  onToggleVoiceMode,
}: MessageInputProps) {
  // 语音模式开启时不渲染文字输入区（由 VoiceModePanel 替代）
  if (voiceMode) {
    return null;
  }

  return (
    <MessageInputInner
      isGenerating={isGenerating}
      disabled={disabled}
      failedContent={failedContent}
      failedAttachments={failedAttachments}
      memberType={memberType}
      onOpenMemberModal={onOpenMemberModal}
      currentAvatarLetter={currentAvatarLetter}
      currentAvatarColor={currentAvatarColor}
      onSend={onSend}
      onStop={onStop}
      onClearFailedContent={onClearFailedContent}
      onToggleVoiceMode={onToggleVoiceMode}
    />
  );
});

/** 内部消息输入组件（语音模式关闭时显示） */
const MessageInputInner = memo(function MessageInputInner({
  isGenerating,
  disabled = false,
  failedContent,
  failedAttachments,
  memberType,
  onOpenMemberModal,
  currentAvatarLetter,
  currentAvatarColor,
  onSend,
  onStop,
  onClearFailedContent,
  onToggleVoiceMode,
}: Omit<MessageInputProps, 'voiceMode'>) {
  const [content, setContent] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const lastSendTimeRef = useRef(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const uploaderRef = useRef<MediaUploaderRef>(null);

  const uploadStore = useUploadStore();
  const hasUploadingTasks = uploadStore.tasks.some(
    (t) => t.status === 'uploading' || t.status === 'pending'
  );

  const trimmedContent = content.trim();
  const isEmpty = trimmedContent.length === 0;
  const isOverLimit = content.length > MAX_LENGTH;
  const canSend =
    !isEmpty &&
    !isOverLimit &&
    !disabled &&
    !isGenerating &&
    !isSending &&
    !hasUploadingTasks;

  // 自动调整文本框高度（最大170px，超出后出现内部滚动条）
  const adjustHeight = useCallback(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = 'auto';
      const maxH = 170;
      const scrollH = textarea.scrollHeight;
      textarea.style.height = `${Math.min(scrollH, maxH)}px`;
      textarea.style.overflowY = scrollH > maxH ? 'auto' : 'hidden';
    }
  }, []);

  // 当有失败内容时，恢复到输入框
  useEffect(() => {
    if (failedContent) {
      setContent(failedContent);
      // 恢复附件到 uploadStore（附件已在 MinIO 中，无需重新上传）
      if (failedAttachments && failedAttachments.length > 0) {
        failedAttachments.forEach((att) => {
          uploadStore.addTask({
            id: att.attachment_uuid,
            file: new File([], att.file_name),
            previewUrl: '',
            progress: { percent: 100, stage: 'uploading', status: '已恢复' },
            status: 'completed',
            attachment: att,
          });
        });
      }
      onClearFailedContent?.();
      // 聚焦到输入框并调整高度
      requestAnimationFrame(() => {
        adjustHeight();
        textareaRef.current?.focus();
      });
    }
  }, [failedContent, failedAttachments, onClearFailedContent, adjustHeight, uploadStore]);

  // 处理输入变化
  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setContent(e.target.value);
      adjustHeight();
    },
    [adjustHeight]
  );

  // 重置文本框高度和滚动条
  const resetTextarea = useCallback(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.overflowY = 'hidden';
    }
  }, []);

  // 发送消息
  const handleSend = useCallback(async () => {
    if (!canSend) return;

    // 防抖检查
    const now = Date.now();
    if (now - lastSendTimeRef.current < DEBOUNCE_MS) {
      return;
    }
    lastSendTimeRef.current = now;

    // 收集已完成的附件数据
    const completedTasks = uploadStore.tasks.filter(
      (t) => t.status === 'completed' && t.attachment
    );
    const attachments =
      completedTasks.length > 0
        ? completedTasks.map((t) => t.attachment!)
        : undefined;

    const sendContent = trimmedContent;
    // 立即清空输入框、上传列表并重置高度（出错时通过 failedContent 机制恢复）
    setContent('');
    resetTextarea();
    uploadStore.clearTasks();

    setIsSending(true);
    try {
      await onSend(sendContent, attachments);
    } finally {
      setIsSending(false);
    }
  }, [canSend, trimmedContent, onSend, resetTextarea, uploadStore]);

  // 停止生成（T039: 500ms 防抖）
  const lastStopTimeRef = useRef(0);
  const handleStop = useCallback(async () => {
    const now = Date.now();
    if (now - lastStopTimeRef.current < STOP_DEBOUNCE_MS) return;
    lastStopTimeRef.current = now;
    await onStop();
  }, [onStop]);

  // 处理键盘事件
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // Enter 发送，Shift+Enter 换行
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (isGenerating) {
          handleStop();
        } else {
          handleSend();
        }
      }
    },
    [isGenerating, handleSend, handleStop]
  );

  // 打开文件选择器
  const handleOpenPicker = useCallback(() => {
    uploaderRef.current?.openFilePicker();
  }, []);

  // T057: 录音完成，创建上传任务并设置"[语音消息]"
  const handleRecordingComplete = useCallback(
    async (blob: Blob, _duration: number) => {
      setIsRecording(false);
      const file = new File([blob], `voice_${Date.now()}.webm`, {
        type: 'audio/webm',
      });
      const task = createUploadTask(file);
      uploadStore.addTask(task);

      // 设置占位文本
      setContent('[语音消息]');

      // 上传文件
      uploadStore.updateTaskStatus(task.id, 'uploading');
      try {
        const response = await uploadMedia(file, (progress) => {
          uploadStore.updateTaskProgress(task.id, progress);
        });
        uploadStore.completeTask(task.id, response.data);
      } catch (error) {
        uploadStore.updateTaskStatus(
          task.id,
          'failed',
          (error as Error).message
        );
      }
    },
    [uploadStore]
  );

  return (
    <div className="border-t bg-white p-4 dark:bg-gray-800">
      <div className="mx-auto max-w-3xl">
        {/* 字符计数警告 */}
        {content.length > MAX_LENGTH * 0.9 && (
          <div
            className={`mb-2 text-right text-xs ${
              isOverLimit ? 'text-red-500' : 'text-yellow-500'
            }`}
          >
            {content.length}/{MAX_LENGTH}
            {isOverLimit && ' 超出字符限制'}
          </div>
        )}

        {/* 上传预览区域 */}
        <MediaUploader
          ref={uploaderRef}
          disabled={disabled || isGenerating}
        />

        {/* T057: 语音录制面板 */}
        {isRecording && (
          <div className="mb-2">
            <AudioRecorder
              onRecordingComplete={handleRecordingComplete}
              onCancel={() => setIsRecording(false)}
              disabled={disabled}
            />
          </div>
        )}

        {/* 输入框 */}
        <div className="relative">
          <textarea
            ref={textareaRef}
            value={content}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
            disabled={disabled || isGenerating}
            rows={1}
            className={`w-full resize-none rounded-lg border px-4 py-3 transition-colors focus:outline-none focus:ring-2 ${
              isOverLimit
                ? 'border-red-300 focus:border-red-500 focus:ring-red-500/20'
                : 'border-gray-300 focus:border-primary-500 focus:ring-primary-500/20'
            } disabled:cursor-not-allowed disabled:bg-gray-100 dark:border-gray-600 dark:bg-gray-700 dark:text-white`}
            style={{ maxHeight: '170px', overflowY: 'hidden' }}
          />
        </div>

        {/* 操作按钮行: 附件 + 发送/停止 */}
        <div className="mt-2 flex items-center justify-between">
          {/* 左侧: 头像按钮 + 附件按钮 */}
          <div className="flex items-center gap-1">
            {/* 成员头像按钮 (T027) — 仅 member 类型用户显示 */}
            {memberType === 'member' && onOpenMemberModal && (
              <button
                type="button"
                onClick={onOpenMemberModal}
                className="flex h-8 w-8 items-center justify-center rounded-full text-xs font-bold text-white transition-opacity hover:opacity-80"
                style={{ backgroundColor: currentAvatarColor || '#3B82F6' }}
                title="切换用户"
              >
                {currentAvatarLetter || '?'}
              </button>
            )}

            <button
              type="button"
              onClick={handleOpenPicker}
              disabled={
                disabled ||
                isGenerating ||
                uploadStore.tasks.length >= MEDIA_LIMITS.MAX_ATTACHMENTS
              }
              className={`flex h-10 w-10 items-center justify-center rounded-lg transition-colors ${
                disabled ||
                isGenerating ||
                uploadStore.tasks.length >= MEDIA_LIMITS.MAX_ATTACHMENTS
                  ? 'cursor-not-allowed text-gray-300'
                  : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-600'
              }`}
              title="上传文件"
            >
              <svg
                className="h-5 w-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"
                />
              </svg>
            </button>

            {/* 语音录制按钮 (T057) */}
            <button
              type="button"
              onClick={() => setIsRecording(true)}
              disabled={disabled || isGenerating || isRecording}
              className={`flex h-10 w-10 items-center justify-center rounded-lg transition-colors ${
                disabled || isGenerating || isRecording
                  ? 'cursor-not-allowed text-gray-300'
                  : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-600'
              }`}
              title="语音录制"
            >
              <svg
                className="h-5 w-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4M12 15a3 3 0 003-3V5a3 3 0 00-6 0v7a3 3 0 003 3z"
                />
              </svg>
            </button>

            {/* 语音模式切换按钮 (T025) */}
            {onToggleVoiceMode && (
              <button
                type="button"
                onClick={onToggleVoiceMode}
                disabled={disabled || isGenerating}
                className={`flex h-10 w-10 items-center justify-center rounded-lg transition-colors ${
                  disabled || isGenerating
                    ? 'cursor-not-allowed text-gray-300'
                    : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-600'
                }`}
                title="语音模式"
              >
                <svg
                  className="h-5 w-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M15.536 8.464a5 5 0 010 7.072M18.364 5.636a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707A1 1 0 0112 5.586v12.828a1 1 0 01-1.707.707L5.586 15z"
                  />
                </svg>
              </button>
            )}

            {/* 上传中提示 */}
            {hasUploadingTasks && (
              <span className="text-xs text-gray-400">上传中...</span>
            )}
          </div>

          {/* 右侧: 发送/停止按钮 */}
          <div>
            {isGenerating ? (
              <button
                onClick={handleStop}
                className="flex h-10 w-10 items-center justify-center rounded-lg bg-red-500 text-white transition-colors hover:bg-red-600"
                title="停止生成"
              >
                <svg
                  className="h-5 w-5"
                  fill="currentColor"
                  viewBox="0 0 20 20"
                >
                  <rect x="6" y="6" width="8" height="8" rx="1" />
                </svg>
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!canSend}
                className={`flex h-10 w-10 items-center justify-center rounded-lg transition-colors ${
                  canSend
                    ? 'bg-primary-500 text-white hover:bg-primary-600'
                    : 'cursor-not-allowed bg-gray-200 text-gray-400 dark:bg-gray-600'
                }`}
                title="发送消息"
              >
                {isSending ? (
                  <svg
                    className="h-5 w-5 animate-spin"
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
                ) : (
                  <svg
                    className="h-5 w-5"
                    fill="currentColor"
                    viewBox="0 0 20 20"
                  >
                    <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
                  </svg>
                )}
              </button>
            )}
          </div>
        </div>

        {/* 提示信息 */}
        <div className="mt-2 text-center text-xs text-gray-400">
          {isGenerating ? (
            <span>AI 正在生成中，点击红色按钮可停止</span>
          ) : (
            <span>按 Enter 发送，Shift+Enter 换行</span>
          )}
        </div>
      </div>
    </div>
  );
});
