/**
 * 聊天页面管理员入口测试 (T031)
 *
 * 覆盖:
 * - 管理员可见"模型配置"按钮
 * - 非管理员不可见"模型配置"按钮
 */
import { render, screen } from '@testing-library/react';

import * as useAuthHook from '@/hooks/useAuth';

jest.mock('@/hooks/useAuth');
jest.mock('@/hooks/useChatStream', () => ({
  useChatStream: () => ({
    messages: [],
    isGenerating: false,
    isLoadingHistory: false,
    hasMore: false,
    error: null,
    failedContent: null,
    send: jest.fn(),
    stop: jest.fn(),
    resume: jest.fn(),
    loadMore: jest.fn(),
    clearFailedContent: jest.fn(),
  }),
}));

// Mock chat 子组件避免 react-markdown ESM 兼容问题
jest.mock('@/components/chat/MessageList', () => ({
  MessageList: () => <div data-testid="message-list" />,
}));
jest.mock('@/components/chat/MessageInput', () => ({
  MessageInput: () => <div data-testid="message-input" />,
}));
jest.mock('@/components/chat/NetworkError', () => ({
  NetworkError: () => null,
}));

import ChatPage from '@/app/chat/page';

describe('ChatPage 管理员入口', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('管理员用户应看到"模型配置"按钮', () => {
    (useAuthHook.useAuth as jest.Mock).mockReturnValue({
      isAuthenticated: true,
      user: { user_id: 1, username: 'admin', type: 'admin' },
      logout: jest.fn(),
    });

    render(<ChatPage />);

    expect(screen.getByText('模型配置')).toBeInTheDocument();
  });

  it('非管理员用户不应看到"模型配置"按钮', () => {
    (useAuthHook.useAuth as jest.Mock).mockReturnValue({
      isAuthenticated: true,
      user: { user_id: 2, username: 'user1', type: 'user' },
      logout: jest.fn(),
    });

    render(<ChatPage />);

    expect(screen.queryByText('模型配置')).not.toBeInTheDocument();
  });

  it('未登录时不应看到"模型配置"按钮', () => {
    (useAuthHook.useAuth as jest.Mock).mockReturnValue({
      isAuthenticated: false,
      user: null,
      logout: jest.fn(),
    });

    render(<ChatPage />);

    expect(screen.queryByText('模型配置')).not.toBeInTheDocument();
  });
});
