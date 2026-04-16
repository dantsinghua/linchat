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
import dynamic from 'next/dynamic';

import { MessageInput } from '@/components/chat/MessageInput';
import { MessageList } from '@/components/chat/MessageList';
import { NetworkError } from '@/components/chat/NetworkError';
import { MemberSwitchModal } from '@/components/members/MemberSwitchModal';
import { CreateMemberWizard } from '@/components/members/CreateMemberWizard';
import { getAvatarColor, getAvatarLetter } from '@/components/members/avatarUtils';
import {
  ContextStatusBar,
  MonitorSidebar,
  MonitorToggleButton,
  useContextMonitor,
} from '@/components/chat/ContextMonitorPanel';
import { useChatStream } from '@/hooks/useChatStream';
import { useAuth } from '@/hooks/useAuth';
import { useChatStore } from '@/stores/chatStore';
import { useMemberStore } from '@/stores/memberStore';
import { useVoiceStore } from '@/stores/voiceStore';

// T055b: 语音容器动态导入，拆分 useVoiceMode + VoiceModePanel 到独立 chunk
const VoiceModeContainer = dynamic(
  () => import('@/components/voice/VoiceModeContainer').then((mod) => mod.VoiceModeContainer),
  { ssr: false },
);

export default function ChatPage() {
  const router = useRouter();
  const { user, logout } = useAuth();
  const [monitorOpen, setMonitorOpen] = useState(true);
  const [memberModalOpen, setMemberModalOpen] = useState(false);
  const [createWizardOpen, setCreateWizardOpen] = useState(false);
  const { data: monitorData, tokenHistory, contextHistory } = useContextMonitor();

  // 015-family-multiuser: 成员状态
  const targetUserId = useMemberStore((s) => s.targetUserId);
  const targetUsername = useMemberStore((s) => s.targetUsername);
  const authUserId = useMemberStore((s) => s.authUserId);
  const isViewingOther = useMemberStore((s) => s.isViewingOther);
  const loadMembers = useMemberStore((s) => s.loadMembers);

  // 语音模式开关状态通过 voiceStore 读取（轻量级，不引入 useVoiceMode 重依赖）
  const voiceModeActive = useVoiceStore((s) => s.voiceMode);
  const setVoiceMode = useVoiceStore((s) => s.setVoiceMode);

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
    reload,
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

  // 语音模式切换：直接操作 voiceStore
  // T050: 进入语音模式前检查是否在查看他人视角，若是则先切回自身
  const handleToggleVoiceMode = useCallback(async () => {
    if (!voiceModeActive && useMemberStore.getState().isViewingOther()) {
      console.warn('[LinChat] 语音模式需使用本人声纹，已切换回自身视角');
      await stop();
      useMemberStore.getState().clearTarget();
      await reload();
    }
    setVoiceMode(!voiceModeActive);
  }, [voiceModeActive, setVoiceMode, stop, reload]);

  // 处理登出
  const handleLogout = useCallback(async () => {
    await logout();
    router.push('/login');
  }, [logout, router]);

  // 015-family-multiuser: 成员切换相关
  const handleOpenMemberModal = useCallback(() => {
    setMemberModalOpen(true);
  }, []);

  const handleMemberSelect = useCallback(async (userId: number, username: string) => {
    const { authUserId, isViewingOther: checkViewingOther, setTargetUser, clearTarget } = useMemberStore.getState();
    // 切换到自己且当前未查看他人时忽略操作
    if (userId === authUserId && !checkViewingOther()) {
      setMemberModalOpen(false);
      return;
    }
    // 中断活跃 SSE 流
    await stop();
    // 切换目标用户（或回到自己）
    if (userId === authUserId) {
      clearTarget();
    } else {
      setTargetUser(userId, username);
    }
    // 重新加载聊天历史
    await reload();
    setMemberModalOpen(false);
  }, [stop, reload]);

  const handleOpenCreateWizard = useCallback(() => {
    setMemberModalOpen(false);
    setCreateWizardOpen(true);
  }, []);

  // 015-family-multiuser: 回到自己
  const handleBackToSelf = useCallback(async () => {
    await stop();
    useMemberStore.getState().clearTarget();
    await reload();
  }, [stop, reload]);

  const handleMemberCreated = useCallback(() => {
    setCreateWizardOpen(false);
    loadMembers();
  }, [loadMembers]);

  // 计算当前头像信息
  const currentAvatarUserId = isViewingOther() && targetUserId ? targetUserId : (authUserId ?? 0);
  const currentAvatarUsername = isViewingOther() && targetUsername ? targetUsername : (user?.username ?? '');
  const currentAvatarLetter = getAvatarLetter(currentAvatarUsername);
  const currentAvatarColor = getAvatarColor(currentAvatarUserId);

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
                <div className="flex items-center gap-2">
                  <span className="text-sm text-gray-600 dark:text-gray-300">
                    {user.username}
                  </span>
                  {user.member_type === 'guest' && (
                    <span className="rounded bg-yellow-100 px-1.5 py-0.5 text-xs font-medium text-yellow-700 dark:bg-yellow-900/50 dark:text-yellow-300">
                      访客
                    </span>
                  )}
                </div>
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

        {/* 015-family-multiuser: 代查模式提示条 */}
        {isViewingOther() && targetUsername && (
          <div className="flex items-center justify-center gap-3 border-b bg-blue-50 px-4 py-2 text-sm text-blue-700 dark:bg-blue-900/30 dark:border-blue-800 dark:text-blue-300">
            <svg className="h-4 w-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
            </svg>
            <span>正在查看 <strong>{targetUsername}</strong> 的对话</span>
            <button
              onClick={handleBackToSelf}
              className="rounded-md bg-blue-100 px-3 py-1 text-xs font-medium text-blue-700 transition-colors hover:bg-blue-200 dark:bg-blue-800 dark:text-blue-200 dark:hover:bg-blue-700"
            >
              回到自己
            </button>
          </div>
        )}

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
            username={targetUsername ?? user?.username}
          />

          {/* 输入框 / 语音面板 + 状态条 */}
          <div>
            <MessageInput
              isGenerating={isGenerating}
              failedContent={failedContent}
              failedAttachments={failedAttachments}
              voiceMode={voiceModeActive}
              memberType={user?.member_type}
              onOpenMemberModal={handleOpenMemberModal}
              currentAvatarLetter={currentAvatarLetter}
              currentAvatarColor={currentAvatarColor}
              onSend={send}
              onStop={stop}
              onClearFailedContent={clearFailedContent}
              onToggleVoiceMode={handleToggleVoiceMode}
            />
            {/* T055b: 语音容器动态加载，包含 useVoiceMode + VoiceModePanel */}
            <VoiceModeContainer />
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

      {/* 015-family-multiuser: 成员切换模态框 */}
      <MemberSwitchModal
        isOpen={memberModalOpen}
        onClose={() => setMemberModalOpen(false)}
        onSelect={handleMemberSelect}
        onCreateUser={handleOpenCreateWizard}
      />

      {/* 015-family-multiuser: 创建成员向导 */}
      <CreateMemberWizard
        isOpen={createWizardOpen}
        onClose={() => setCreateWizardOpen(false)}
        onCreated={handleMemberCreated}
      />
    </div>
  );
}
