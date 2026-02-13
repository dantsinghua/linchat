/**
 * 聊天页面
 *
 * 参考:
 * - spec.md US2 - LLM 聊天交互
 * - process-model.md#三、消息发送与流式响应流程
 * - behavior-model.md#2.1-2.4 聊天相关行为
 */
'use client';

import { useCallback, useState } from 'react';
import { useRouter } from 'next/navigation';

import { MessageInput } from '@/components/chat/MessageInput';
import { MessageList } from '@/components/chat/MessageList';
import { NetworkError } from '@/components/chat/NetworkError';
import {
  ContextStatusBar,
  MonitorSidebar,
  MonitorToggleButton,
  useContextMonitor,
} from '@/components/chat/ContextMonitorPanel';
import { useChatStream } from '@/hooks/useChatStream';
import { useAuth } from '@/hooks/useAuth';
import { useChatStore } from '@/stores/chatStore';

export default function ChatPage() {
  const router = useRouter();
  const { user, logout } = useAuth();
  const [monitorOpen, setMonitorOpen] = useState(true);
  const { data: monitorData, tokenHistory, contextHistory } = useContextMonitor();
  const {
    messages,
    isGenerating,
    isCompacting,
    isLoadingHistory,
    hasMore,
    error,
    failedContent,
    failedAttachments,
    gatewayRetryAfter,
    send,
    stop,
    resume,
    loadMore,
    clearFailedContent,
  } = useChatStream();

  // 清除错误
  const handleClearError = useCallback(() => {
    // 错误已通过 useChatStore 管理，这里主要处理 UI 状态
  }, []);

  // 重试发送
  const handleRetry = useCallback(async () => {
    if (failedContent) {
      const content = failedContent;
      const attachments = failedAttachments ?? undefined;
      clearFailedContent();
      await send(content, attachments);
    }
  }, [failedContent, failedAttachments, clearFailedContent, send]);

  // 处理登出
  const handleLogout = useCallback(async () => {
    await logout();
    router.push('/login');
  }, [logout, router]);

  return (
    <div className="flex h-screen bg-gray-50 dark:bg-gray-900">
      {/* 主内容区 */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* 顶部导航 */}
        <header className="border-b bg-white px-6 py-4 dark:bg-gray-800 dark:border-gray-700">
          <div className="mx-auto flex max-w-5xl items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary-500 text-white font-bold">
                L
              </div>
              <h1 className="text-xl font-semibold text-gray-800 dark:text-white">
                LinChat
              </h1>
            </div>

            <div className="flex items-center gap-4">
              {/* 用户信息 */}
              {user && (
                <span className="text-sm text-gray-600 dark:text-gray-300">
                  {user.username}
                </span>
              )}

              {/* 模型配置入口 - 仅管理员可见 */}
              {user && user.type === 'admin' && (
                <button
                  onClick={() => router.push('/settings')}
                  className="flex items-center gap-2 rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 transition-colors hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
                    />
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
                    />
                  </svg>
                  模型配置
                </button>
              )}

              {/* 监控面板切换 */}
              <MonitorToggleButton
                isOpen={monitorOpen}
                onClick={() => setMonitorOpen((v) => !v)}
              />

              {/* 登出按钮 */}
              <button
                onClick={handleLogout}
                className="flex items-center gap-2 rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 transition-colors hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
                  />
                </svg>
                退出
              </button>
            </div>
          </div>
        </header>

        {/* 网络错误提示 */}
        <NetworkError
          error={error}
          onClear={handleClearError}
          onRetry={failedContent ? handleRetry : undefined}
          showRetry={!!failedContent}
          gatewayRetryAfter={gatewayRetryAfter}
          onRetryAfterDone={() => useChatStore.getState().setGatewayRetryAfter(0)}
        />

        {/* 聊天区域 */}
        <main className="flex flex-1 flex-col overflow-hidden">
          {/* 消息列表 */}
          <MessageList
            messages={messages}
            isGenerating={isGenerating}
            isCompacting={isCompacting}
            isLoadingHistory={isLoadingHistory}
            hasMore={hasMore}
            onLoadMore={loadMore}
            onResume={resume}
          />

          {/* 输入框 + 状态条 */}
          <div>
            <MessageInput
              isGenerating={isGenerating}
              failedContent={failedContent}
              failedAttachments={failedAttachments}
              onSend={send}
              onStop={stop}
              onClearFailedContent={clearFailedContent}
            />
            {monitorData && (
              <div className="mx-auto max-w-3xl px-4 pb-2">
                <ContextStatusBar pct={monitorData.pct} alert={monitorData.alert} />
              </div>
            )}
          </div>
        </main>
      </div>

      {/* 监控侧边栏 */}
      <MonitorSidebar
        isOpen={monitorOpen}
        data={monitorData}
        tokenHistory={tokenHistory}
        contextHistory={contextHistory}
      />
    </div>
  );
}
