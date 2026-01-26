/**
 * MessageList 组件测试
 *
 * 测试内容:
 * - 消息渲染
 * - 空状态显示
 * - 滚动锚定
 * - 加载更多
 * - 中断标记显示
 * - 继续生成按钮
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { MessageList } from '@/components/chat/MessageList';
import type { Message } from '@/types';

// Mock scrollIntoView
Element.prototype.scrollIntoView = jest.fn();

// Mock MarkdownRenderer
jest.mock('@/components/chat/MarkdownRenderer', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => (
    <div data-testid="markdown-renderer">{content}</div>
  ),
}));

describe('MessageList', () => {
  const mockOnLoadMore = jest.fn();
  const mockOnResume = jest.fn();

  const createMessage = (overrides: Partial<Message> = {}): Message => ({
    message_id: 1,
    message_uuid: 'uuid-1',
    role: 'user',
    content: 'Hello',
    status: 1,
    sequence: 1,
    created_time: new Date().toISOString(),
    request_id: 'req-1',
    model_name: null,
    response_time_ms: null,
    ...overrides,
  });

  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('渲染测试', () => {
    it('应正确渲染消息列表', () => {
      const messages: Message[] = [
        createMessage({ message_id: 1, content: 'User message', role: 'user' }),
        createMessage({ message_id: 2, content: 'AI response', role: 'assistant' }),
      ];

      render(
        <MessageList
          messages={messages}
          isGenerating={false}
          isLoadingHistory={false}
          hasMore={true}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      expect(screen.getByText('User message')).toBeInTheDocument();
      expect(screen.getByText('AI response')).toBeInTheDocument();
    });

    it('用户消息应使用 whitespace-pre-wrap', () => {
      const messages: Message[] = [
        createMessage({ message_id: 1, content: 'Hello World', role: 'user' }),
      ];

      render(
        <MessageList
          messages={messages}
          isGenerating={false}
          isLoadingHistory={false}
          hasMore={true}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      const userMessage = screen.getByText('Hello World');
      expect(userMessage).toHaveClass('whitespace-pre-wrap');
    });

    it('AI消息应使用 MarkdownRenderer', () => {
      const messages: Message[] = [
        createMessage({ message_id: 1, content: '**bold**', role: 'assistant' }),
      ];

      render(
        <MessageList
          messages={messages}
          isGenerating={false}
          isLoadingHistory={false}
          hasMore={true}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      expect(screen.getByTestId('markdown-renderer')).toBeInTheDocument();
    });
  });

  describe('空状态', () => {
    it('无消息时应显示空状态提示', () => {
      render(
        <MessageList
          messages={[]}
          isGenerating={false}
          isLoadingHistory={false}
          hasMore={true}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      expect(screen.getByText('开始对话')).toBeInTheDocument();
      expect(screen.getByText(/输入消息开始/)).toBeInTheDocument();
    });

    it('加载历史时不应显示空状态', () => {
      render(
        <MessageList
          messages={[]}
          isGenerating={false}
          isLoadingHistory={true}
          hasMore={true}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      expect(screen.queryByText('开始对话')).not.toBeInTheDocument();
    });
  });

  describe('加载状态', () => {
    it('加载历史时应显示加载提示', () => {
      render(
        <MessageList
          messages={[]}
          isGenerating={false}
          isLoadingHistory={true}
          hasMore={true}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      expect(screen.getByText('加载中...')).toBeInTheDocument();
    });

    it('没有更多消息时应显示提示', () => {
      const messages: Message[] = [
        createMessage({ message_id: 1 }),
      ];

      render(
        <MessageList
          messages={messages}
          isGenerating={false}
          isLoadingHistory={false}
          hasMore={false}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      expect(screen.getByText('没有更多消息了')).toBeInTheDocument();
    });
  });

  describe('消息状态', () => {
    it('生成中消息应显示动画', () => {
      const messages: Message[] = [
        createMessage({ message_id: 1, content: 'Partial', role: 'assistant', status: 2 }),
      ];

      render(
        <MessageList
          messages={messages}
          isGenerating={false}
          isLoadingHistory={false}
          hasMore={true}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      // 应该有动画元素
      expect(screen.getByText('|')).toBeInTheDocument();
    });

    it('中断消息应显示[已中断]标记', () => {
      const messages: Message[] = [
        createMessage({ message_id: 1, content: 'Partial[已中断]', role: 'assistant', status: 3 }),
      ];

      render(
        <MessageList
          messages={messages}
          isGenerating={false}
          isLoadingHistory={false}
          hasMore={true}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      // 内容应该移除[已中断]，单独渲染
      expect(screen.getByTestId('markdown-renderer').textContent).toBe('Partial');
      expect(screen.getByText('[已中断]')).toBeInTheDocument();
    });

    it('中断消息应显示继续生成按钮', () => {
      const messages: Message[] = [
        createMessage({ message_id: 1, content: 'Partial', role: 'assistant', status: 3 }),
      ];

      render(
        <MessageList
          messages={messages}
          isGenerating={false}
          isLoadingHistory={false}
          hasMore={true}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      expect(screen.getByText('继续生成')).toBeInTheDocument();
    });

    it('点击继续生成应调用 onResume', () => {
      const messages: Message[] = [
        createMessage({ message_id: 123, content: 'Partial', role: 'assistant', status: 3 }),
      ];

      render(
        <MessageList
          messages={messages}
          isGenerating={false}
          isLoadingHistory={false}
          hasMore={true}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      fireEvent.click(screen.getByText('继续生成'));
      expect(mockOnResume).toHaveBeenCalledWith(123);
    });

    it('失败消息应显示失败标记', () => {
      const messages: Message[] = [
        createMessage({ message_id: 1, content: 'Failed', role: 'assistant', status: 0 }),
      ];

      render(
        <MessageList
          messages={messages}
          isGenerating={false}
          isLoadingHistory={false}
          hasMore={true}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      expect(screen.getByText('发送失败')).toBeInTheDocument();
    });
  });

  describe('消息样式', () => {
    it('用户消息应右对齐', () => {
      const messages: Message[] = [
        createMessage({ message_id: 1, role: 'user' }),
      ];

      const { container } = render(
        <MessageList
          messages={messages}
          isGenerating={false}
          isLoadingHistory={false}
          hasMore={true}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      const messageBubble = container.querySelector('.justify-end');
      expect(messageBubble).toBeInTheDocument();
    });

    it('AI消息应左对齐', () => {
      const messages: Message[] = [
        createMessage({ message_id: 1, role: 'assistant' }),
      ];

      const { container } = render(
        <MessageList
          messages={messages}
          isGenerating={false}
          isLoadingHistory={false}
          hasMore={true}
          onLoadMore={mockOnLoadMore}
          onResume={mockOnResume}
        />
      );

      const messageBubble = container.querySelector('.justify-start');
      expect(messageBubble).toBeInTheDocument();
    });
  });
});
