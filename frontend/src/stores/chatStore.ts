/**
 * 聊天状态管理
 *
 * 使用 Zustand 管理聊天相关状态
 * 参考: data-model.md#2.2 消息表
 */
import { create } from 'zustand';

import type { Message } from '@/types';

interface ChatState {
  // 消息列表
  messages: Message[];
  // 是否正在加载历史消息
  isLoadingHistory: boolean;
  // 是否正在生成中
  isGenerating: boolean;
  // 当前生成的请求ID（用于停止生成）
  currentRequestId: string | null;
  // 是否还有更多历史消息
  hasMore: boolean;
  // 错误信息
  error: string | null;
  // 发送失败时保留的输入内容（用于恢复）
  failedContent: string | null;

  // Actions
  setMessages: (messages: Message[]) => void;
  addMessage: (message: Message) => void;
  updateMessage: (messageId: number, updates: Partial<Message>) => void;
  appendContent: (messageId: number, content: string) => void;
  prependMessages: (messages: Message[]) => void;
  setIsLoadingHistory: (loading: boolean) => void;
  setIsGenerating: (generating: boolean) => void;
  setCurrentRequestId: (requestId: string | null) => void;
  setHasMore: (hasMore: boolean) => void;
  setError: (error: string | null) => void;
  setFailedContent: (content: string | null) => void;
  clearMessages: () => void;
  reset: () => void;
}

const initialState = {
  messages: [],
  isLoadingHistory: false,
  isGenerating: false,
  currentRequestId: null,
  hasMore: true,
  error: null,
  failedContent: null,
};

export const useChatStore = create<ChatState>((set) => ({
  ...initialState,

  setMessages: (messages) => set({ messages }),

  addMessage: (message) =>
    set((state) => ({
      messages: [...state.messages, message],
    })),

  updateMessage: (messageId, updates) =>
    set((state) => ({
      messages: state.messages.map((msg) =>
        msg.message_id === messageId ? { ...msg, ...updates } : msg
      ),
    })),

  appendContent: (messageId, content) =>
    set((state) => ({
      messages: state.messages.map((msg) =>
        msg.message_id === messageId
          ? { ...msg, content: (msg.content || '') + content }
          : msg
      ),
    })),

  prependMessages: (messages) =>
    set((state) => ({
      messages: [...messages, ...state.messages],
    })),

  setIsLoadingHistory: (isLoadingHistory) => set({ isLoadingHistory }),

  setIsGenerating: (isGenerating) => set({ isGenerating }),

  setCurrentRequestId: (currentRequestId) => set({ currentRequestId }),

  setHasMore: (hasMore) => set({ hasMore }),

  setError: (error) => set({ error }),

  setFailedContent: (failedContent) => set({ failedContent }),

  clearMessages: () => set({ messages: [] }),

  reset: () => set(initialState),
}));
