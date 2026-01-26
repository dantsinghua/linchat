/**
 * 消息输入组件
 *
 * 参考:
 * - rule-model.md#R_MSG_001 - 消息长度限制 4000 字符
 * - rule-model.md#R_MSG_002 - 空消息拦截
 * - spec.md US2场景9 - 停止按钮
 */
'use client';

import { memo, useCallback, useEffect, useRef, useState } from 'react';

const MAX_LENGTH = 4000;
const DEBOUNCE_MS = 300;

interface MessageInputProps {
  isGenerating: boolean;
  disabled?: boolean;
  failedContent?: string | null;
  onSend: (content: string) => Promise<void>;
  onStop: () => Promise<void>;
  onClearFailedContent?: () => void;
}

/**
 * 消息输入组件
 *
 * 功能：
 * - 空消息拦截（trim 后校验）
 * - 长度限制（4000 字符）
 * - 发送按钮 / 停止按钮切换
 * - 防抖处理（300ms）
 */
export const MessageInput = memo(function MessageInput({
  isGenerating,
  disabled = false,
  failedContent,
  onSend,
  onStop,
  onClearFailedContent,
}: MessageInputProps) {
  const [content, setContent] = useState('');
  const [isSending, setIsSending] = useState(false);
  const lastSendTimeRef = useRef(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 当有失败内容时，恢复到输入框
  useEffect(() => {
    if (failedContent) {
      setContent(failedContent);
      onClearFailedContent?.();
      // 聚焦到输入框
      textareaRef.current?.focus();
    }
  }, [failedContent, onClearFailedContent]);

  const trimmedContent = content.trim();
  const isEmpty = trimmedContent.length === 0;
  const isOverLimit = content.length > MAX_LENGTH;
  const canSend = !isEmpty && !isOverLimit && !disabled && !isGenerating && !isSending;

  // 自动调整文本框高度
  const adjustHeight = useCallback(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = 'auto';
      textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
    }
  }, []);

  // 处理输入变化
  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setContent(e.target.value);
      adjustHeight();
    },
    [adjustHeight]
  );

  // 发送消息
  const handleSend = useCallback(async () => {
    if (!canSend) return;

    // 防抖检查
    const now = Date.now();
    if (now - lastSendTimeRef.current < DEBOUNCE_MS) {
      return;
    }
    lastSendTimeRef.current = now;

    setIsSending(true);
    try {
      await onSend(trimmedContent);
      setContent('');
      // 重置文本框高度
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    } finally {
      setIsSending(false);
    }
  }, [canSend, trimmedContent, onSend]);

  // 停止生成
  const handleStop = useCallback(async () => {
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

        <div className="flex items-end gap-3">
          {/* 输入框 */}
          <div className="relative flex-1">
            <textarea
              ref={textareaRef}
              value={content}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
              disabled={disabled}
              rows={1}
              className={`w-full resize-none rounded-lg border px-4 py-3 pr-12 transition-colors focus:outline-none focus:ring-2 ${
                isOverLimit
                  ? 'border-red-300 focus:border-red-500 focus:ring-red-500/20'
                  : 'border-gray-300 focus:border-primary-500 focus:ring-primary-500/20'
              } disabled:cursor-not-allowed disabled:bg-gray-100 dark:border-gray-600 dark:bg-gray-700 dark:text-white`}
              style={{ maxHeight: '200px' }}
            />
          </div>

          {/* 发送/停止按钮 */}
          {isGenerating ? (
            <button
              onClick={handleStop}
              className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-lg bg-red-500 text-white transition-colors hover:bg-red-600"
              title="停止生成"
            >
              <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 20 20">
                <rect x="6" y="6" width="8" height="8" rx="1" />
              </svg>
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!canSend}
              className={`flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-lg transition-colors ${
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
                <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 20 20">
                  <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
                </svg>
              )}
            </button>
          )}
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
