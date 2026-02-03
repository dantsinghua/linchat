/**
 * 消息列表组件
 *
 * 参考:
 * - spec.md US2场景5,6,8 - 历史消息显示和滚动
 * - data-model.md#2.2 消息表 - status 字段定义
 */
'use client';

import { memo, useCallback, useEffect, useRef } from 'react';

import { MarkdownRenderer } from './MarkdownRenderer';
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

  // 滚动到底部
  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    bottomRef.current?.scrollIntoView({ behavior });
  }, []);

  // 检测用户是否正在向上滚动
  const handleScroll = useCallback(() => {
    if (!containerRef.current) return;

    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 100;
    isUserScrollingRef.current = !isAtBottom;

    // 向上滚动到顶部时加载更多
    if (scrollTop < 100 && hasMore && !isLoadingHistory) {
      onLoadMore();
    }
  }, [hasMore, isLoadingHistory, onLoadMore]);

  // 新消息时自动滚动到底部（仅当用户没有向上滚动时）
  useEffect(() => {
    if (messages.length > prevMessagesLengthRef.current) {
      if (!isUserScrollingRef.current) {
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
              {/* ▶ 播放图标 */}
              <span className="text-xs">▶</span>
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
