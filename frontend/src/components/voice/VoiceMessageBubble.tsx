/**
 * 语音消息气泡组件
 *
 * 功能：在聊天历史中渲染语音消息
 * - "[语音消息]" 标签 + STT 转写文字
 * - 迷你音频播放器（如果有音频附件）
 * - 支持音频过期状态显示
 *
 * 参考: specs/009-voice-interaction
 */
'use client';

import { memo, useMemo } from 'react';

import { AudioPlayer } from '@/components/chat/AudioPlayer';
import { getMediaUrl } from '@/services/mediaApi';
import type { Message } from '@/types';
import type { MediaAttachment } from '@/types/media';

interface VoiceMessageBubbleProps {
  /** 语音消息对象 */
  message: Message;
  /** 是否为用户发送的消息 */
  isUser: boolean;
}

/**
 * 从附件列表中提取第一个音频附件
 */
function findAudioAttachment(
  attachments?: MediaAttachment[]
): MediaAttachment | null {
  if (!attachments || attachments.length === 0) return null;
  return attachments.find((a) => a.media_type === 'audio') ?? null;
}

/**
 * 判断消息内容是否为纯占位符（无实际转写文字）
 */
function isPlaceholderContent(content: string): boolean {
  const trimmed = content.trim();
  return trimmed === '' || trimmed === '[语音输入]';
}

/**
 * 语音消息气泡组件
 *
 * 渲染语音消息的转写文字和音频播放器。
 * 组件本身不包含外层气泡容器，由父组件（MessageBubble）提供背景色等样式。
 */
export const VoiceMessageBubble = memo(function VoiceMessageBubble({
  message,
  isUser,
}: VoiceMessageBubbleProps) {
  const audioAttachment = useMemo(
    () => findAudioAttachment(message.attachments as MediaAttachment[]),
    [message.attachments]
  );

  const hasTranscription = !isPlaceholderContent(message.content);

  const audioUrl = useMemo(() => {
    if (!audioAttachment || audioAttachment.is_expired) return null;
    return getMediaUrl(audioAttachment.attachment_uuid);
  }, [audioAttachment]);

  return (
    <div className="flex flex-col gap-2">
      {/* 语音消息标签 */}
      <div className="flex items-center gap-1.5">
        {/* 麦克风图标 */}
        <svg
          className="h-3.5 w-3.5 shrink-0 text-gray-400 dark:text-gray-500"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3Z"
          />
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M19 10v2a7 7 0 0 1-14 0v-2"
          />
          <line x1="12" y1="19" x2="12" y2="23" />
          <line x1="8" y1="23" x2="16" y2="23" />
        </svg>
        <span className="text-xs text-gray-400 dark:text-gray-500">
          [语音消息]
        </span>
      </div>

      {/* STT 转写文字 */}
      {hasTranscription && (
        <p
          className={`whitespace-pre-wrap break-words text-sm leading-relaxed ${
            isUser
              ? 'text-white'
              : 'text-gray-800 dark:text-gray-200'
          }`}
        >
          {message.content}
        </p>
      )}

      {/* 音频播放器 / 过期提示 */}
      {audioAttachment && (
        <>
          {audioAttachment.is_expired ? (
            <div className="flex items-center gap-1.5 text-xs text-gray-400 dark:text-gray-500">
              <svg
                className="h-3.5 w-3.5 shrink-0"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                viewBox="0 0 24 24"
              >
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              <span>音频已过期</span>
            </div>
          ) : audioUrl ? (
            <AudioPlayer
              src={audioUrl}
              duration={audioAttachment.duration_seconds}
            />
          ) : null}
        </>
      )}
    </div>
  );
});
