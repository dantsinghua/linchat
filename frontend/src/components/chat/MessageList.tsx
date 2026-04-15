/**
 * 消息列表组件
 *
 * 参考:
 * - spec.md US2场景5,6,8 - 历史消息显示和滚动
 * - data-model.md#2.2 消息表 - status 字段定义
 * - T030: 扩展支持渲染带附件的消息
 */
'use client';

import { memo, useCallback, useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';

import { AudioPlayer } from './AudioPlayer';
import { MarkdownRenderer } from './MarkdownRenderer';
import { AttachmentList } from './MediaPreview';

// T055b: 语音消息组件动态导入，拆分 chunk 减小首次加载体积
const VoiceMessageBubble = dynamic(
  () => import('@/components/voice/VoiceMessageBubble').then((mod) => mod.VoiceMessageBubble),
  { ssr: false },
);
import { getMediaUrl } from '@/services/mediaApi';
import { useChatStore } from '@/stores/chatStore';
import type { Message } from '@/types';

interface MessageListProps {
  messages: Message[];
  isGenerating: boolean;
  isCompacting: boolean;
  isLoadingHistory: boolean;
  hasMore: boolean;
  onLoadMore: () => void;
  onResume: (messageId: number) => void;
}

/**
 * 消息列表组件
 *
 * 功能：
 * - 历史消息渲染（用户消息右侧蓝底、AI消息左侧灰底）
 * - 滚动锚定（默认锚定最底部）
 * - 向上滚动加载更多
 * - 消息状态渲染（生成中、中断、失败）
 * - 附件渲染（多模态消息）
 */
export const MessageList = memo(function MessageList({
  messages,
  isGenerating: _isGenerating, // 保留以备后续使用
  isCompacting,
  isLoadingHistory,
  hasMore,
  onLoadMore,
  onResume,
}: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const isUserScrollingRef = useRef(false);
  const prevMessagesLengthRef = useRef(messages.length);
  const isLoadingHistoryRef = useRef(false);
  const loadMoreTimerRef = useRef<NodeJS.Timeout | null>(null);
  const prevScrollHeightRef = useRef(0);

  // 012-doc-parse-progress: 文档解析进度
  const docParseProgress = useChatStore(state => state.docParseProgress);

  // T051a: 视频推理超时提示
  const [showVideoHint, setShowVideoHint] = useState(false);
  const videoHintTimerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    // 清理旧计时器
    if (videoHintTimerRef.current) {
      clearTimeout(videoHintTimerRef.current);
      videoHintTimerRef.current = null;
    }
    setShowVideoHint(false);

    if (!_isGenerating || messages.length < 2) return;

    // 找到最后一条生成中的 assistant 消息
    const lastMsg = messages[messages.length - 1] as Message | undefined;
    if (!lastMsg || lastMsg.role !== 'assistant' || lastMsg.status !== 2) return;

    // 有内容说明已收到首个 content
    if (lastMsg.content && lastMsg.content.trim().length > 0) return;

    // 找前一条 user 消息中的视频附件
    const userMsg = messages[messages.length - 2] as Message | undefined;
    if (!userMsg || userMsg.role !== 'user' || !userMsg.attachments) return;

    const videoAttachments = userMsg.attachments.filter(
      (a) => a.media_type === 'video'
    );
    if (videoAttachments.length === 0) return;

    // 取最大时长作为基准
    const maxDuration = Math.max(
      ...videoAttachments.map((a) => a.duration_seconds || 30)
    );
    const delayMs = maxDuration * 2 * 1000;

    videoHintTimerRef.current = setTimeout(() => {
      setShowVideoHint(true);
    }, delayMs);

    return () => {
      if (videoHintTimerRef.current) {
        clearTimeout(videoHintTimerRef.current);
        videoHintTimerRef.current = null;
      }
    };
  }, [_isGenerating, messages]);

  // 滚动到底部
  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    bottomRef.current?.scrollIntoView({ behavior });
  }, []);

  // 同步 isLoadingHistory 到 ref，避免 handleScroll 闭包过期
  useEffect(() => {
    isLoadingHistoryRef.current = isLoadingHistory;
  }, [isLoadingHistory]);

  // 检测用户是否正在向上滚动（带 500ms 防抖）
  const handleScroll = useCallback(() => {
    if (!containerRef.current) return;

    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 100;
    isUserScrollingRef.current = !isAtBottom;

    // 向上滚动到顶部时加载更多（500ms 防抖）
    if (scrollTop < 100 && hasMore && !isLoadingHistoryRef.current) {
      if (!loadMoreTimerRef.current) {
        loadMoreTimerRef.current = setTimeout(() => {
          loadMoreTimerRef.current = null;
          if (hasMore && !isLoadingHistoryRef.current && containerRef.current) {
            prevScrollHeightRef.current = containerRef.current.scrollHeight;
            onLoadMore();
          }
        }, 500);
      }
    }
  }, [hasMore, onLoadMore]);

  // 加载历史完成后恢复滚动位置（补偿 prepend 导致的高度增长）
  useEffect(() => {
    if (!isLoadingHistory && prevScrollHeightRef.current > 0 && containerRef.current) {
      const delta = containerRef.current.scrollHeight - prevScrollHeightRef.current;
      if (delta > 0) {
        containerRef.current.scrollTop += delta;
      }
      prevScrollHeightRef.current = 0;
    }
  }, [isLoadingHistory]);

  // 新消息时自动滚动到底部（仅当用户没有向上滚动时）
  // 加载历史消息（prepend）时保持滚动位置不变
  useEffect(() => {
    if (messages.length > prevMessagesLengthRef.current) {
      if (!isUserScrollingRef.current && !isLoadingHistoryRef.current) {
        scrollToBottom();
      }
    }
    prevMessagesLengthRef.current = messages.length;
  }, [messages.length, scrollToBottom]);

  // 初始加载时滚动到底部
  useEffect(() => {
    if (messages.length > 0 && !isLoadingHistory) {
      scrollToBottom('instant');
    }
  }, [isLoadingHistory, messages.length, scrollToBottom]);

  return (
    <div
      ref={containerRef}
      className="flex-1 overflow-y-auto p-4"
      onScroll={handleScroll}
    >
      {/* 加载更多提示 */}
      {isLoadingHistory && (
        <div className="flex justify-center py-4">
          <div className="flex items-center gap-2 text-gray-500">
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
            <span className="text-sm">加载中...</span>
          </div>
        </div>
      )}

      {/* 没有更多消息提示 */}
      {!hasMore && messages.length > 0 && (
        <div className="py-4 text-center text-sm text-gray-400">
          没有更多消息了
        </div>
      )}

      {/* 空状态 */}
      {messages.length === 0 && !isLoadingHistory && (
        <div className="flex h-full items-center justify-center">
          <div className="text-center text-gray-500">
            <svg
              className="mx-auto h-12 w-12 text-gray-300"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
              />
            </svg>
            <p className="mt-4 text-lg font-medium">开始对话</p>
            <p className="mt-1 text-sm">输入消息开始与 AI 助手交流</p>
          </div>
        </div>
      )}

      {/* 消息列表 */}
      <div className="mx-auto max-w-3xl space-y-4">
        {messages.map((message) => (
          <MessageBubble
            key={message.message_id}
            message={message}
            onResume={onResume}
          />
        ))}
      </div>

      {/* T051a: 视频推理超时提示 */}
      {showVideoHint && (
        <div className="mx-auto max-w-3xl py-2">
          <div className="flex items-center gap-2 rounded-lg bg-amber-50 px-4 py-2 text-sm text-amber-600 dark:bg-amber-900/20 dark:text-amber-400">
            <svg
              className="h-4 w-4 shrink-0"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
            <span>AI 正在分析视频，请耐心等待...</span>
          </div>
        </div>
      )}

      {/* 文档解析进度条（012-doc-parse-progress） */}
      {docParseProgress && (
        <div className="mx-auto max-w-3xl py-2">
          <div className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm ${
            docParseProgress.status === 'completed'
              ? 'bg-green-50 text-green-600 dark:bg-green-900/20 dark:text-green-400'
              : docParseProgress.status === 'failed'
              ? 'bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400'
              : docParseProgress.status === 'incomplete'
              ? 'bg-orange-50 text-orange-600 dark:bg-orange-900/20 dark:text-orange-400'
              : 'bg-indigo-50 text-indigo-600 dark:bg-indigo-900/20 dark:text-indigo-400'
          }`}>
            {/* Icon */}
            {docParseProgress.status === 'completed' ? (
              <svg className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            ) : docParseProgress.status === 'failed' ? (
              <svg className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            ) : docParseProgress.status === 'incomplete' ? (
              <svg className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            ) : (
              <svg className="h-4 w-4 shrink-0 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
            )}
            <div className="min-w-0 flex-1">
              <span className="truncate">{docParseProgress.fileName}</span>
              {' — '}
              {docParseProgress.status === 'pending' && '排队等待解析...'}
              {docParseProgress.status === 'processing' && `${docParseProgress.current}/${docParseProgress.total} 页`}
              {docParseProgress.status === 'completed' && '解析完成'}
              {docParseProgress.status === 'incomplete' && `${docParseProgress.current}/${docParseProgress.total} 页（部分完成）`}
              {docParseProgress.status === 'failed' && (docParseProgress.errorMessage || '解析失败')}
              {docParseProgress.status === 'processing' && docParseProgress.total > 0 && (
                <div className="mt-1 h-1.5 w-full rounded-full bg-indigo-100 dark:bg-indigo-900/40">
                  <div
                    className="h-full rounded-full bg-indigo-500 transition-all duration-300"
                    style={{ width: `${Math.round((docParseProgress.current / docParseProgress.total) * 100)}%` }}
                  />
                </div>
              )}
              {docParseProgress.suggestion && (
                <div className="mt-1 text-xs opacity-75">{docParseProgress.suggestion}</div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 上下文压缩状态提示 */}
      {isCompacting && (
        <div className="mx-auto max-w-3xl py-2">
          <div className="flex items-center gap-2 rounded-lg bg-blue-50 px-4 py-2 text-sm text-blue-600 dark:bg-blue-900/20 dark:text-blue-400">
            <svg
              className="h-4 w-4 animate-spin"
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
            <span>正在压缩上下文...</span>
          </div>
        </div>
      )}

      {/* 底部锚点 */}
      <div ref={bottomRef} />
    </div>
  );
});

/**
 * 消息气泡组件
 */
interface MessageBubbleProps {
  message: Message;
  onResume: (messageId: number) => void;
}

const MessageBubble = memo(function MessageBubble({
  message,
  onResume,
}: MessageBubbleProps) {
  const isUser = message.role === 'user';
  const isGenerating = message.status === 2;
  const isInterrupted = message.status === 3;
  const isFailed = message.status === 0;
  const hasAttachments =
    message.attachments && message.attachments.length > 0;

  // 移除内容中的 [已中断] 标记（如果有），由UI单独渲染
  const displayContent = message.content?.replace(/\[已中断\]$/, '') || '';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`relative max-w-[80%] rounded-lg px-4 py-3 ${
          isUser
            ? 'bg-primary-500 text-white'
            : 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-100'
        }`}
      >
        {/* 语音消息: 使用 VoiceMessageBubble 渲染 */}
        {message.is_voice ? (
          <VoiceMessageBubble message={message} isUser={isUser} />
        ) : (
          <>
            {/* 用户消息: 附件显示在文本上方 */}
            {isUser && hasAttachments && (
              <div className="mb-2">
                {/* T058: 音频附件使用 AudioPlayer */}
                {message.attachments!.map((att) =>
                  att.media_type === 'audio' ? (
                    <AudioPlayer
                      key={att.attachment_uuid}
                      src={getMediaUrl(att.attachment_uuid)}
                      duration={att.duration_seconds}
                    />
                  ) : null
                )}
                <AttachmentList
                  attachments={message.attachments!.filter(
                    (a) => a.media_type !== 'audio'
                  )}
                />
              </div>
            )}

            {/* 消息内容 */}
            {isUser ? (
              <p className="whitespace-pre-wrap">{message.content}</p>
            ) : (
              <>
                <MarkdownRenderer content={displayContent} />
                {/* [已中断]标记：显示在消息内容末尾（内联） */}
                {isInterrupted && (
                  <span className="text-xs text-gray-400">[已中断]</span>
                )}
              </>
            )}
          </>
        )}

        {/* AI 消息: 附件显示在文本下方（语音消息已在 VoiceMessageBubble 内处理） */}
        {!isUser && hasAttachments && !message.is_voice && (
          <div className="mt-2">
            <AttachmentList attachments={message.attachments!} />
          </div>
        )}

        {/* 生成中动画 */}
        {isGenerating && (
          <span className="ml-1 inline-block">
            <span className="animate-pulse">|</span>
          </span>
        )}

        {/* 继续生成按钮：仅在 status=3 时显示 */}
        {isInterrupted && (
          <div className="mt-2 flex justify-end">
            <button
              onClick={() => onResume(message.message_id)}
              className="flex items-center gap-1 rounded border border-primary-500 bg-white px-3 py-1 text-xs text-primary-500 transition-colors hover:bg-primary-50"
            >
              {/* 播放图标 */}
              <span className="text-xs">&#9654;</span>
              继续生成
            </button>
          </div>
        )}

        {/* 失败标记 */}
        {isFailed && (
          <div className="mt-2 text-xs text-red-500">
            发送失败
          </div>
        )}
      </div>
    </div>
  );
});

