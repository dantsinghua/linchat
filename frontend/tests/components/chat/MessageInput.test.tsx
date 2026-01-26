/**
 * MessageInput 组件测试
 *
 * 测试内容:
 * - 空消息拦截（trim 后校验）
 * - 长度限制（4000 字符）
 * - 发送按钮 / 停止按钮切换
 * - 防抖处理（300ms）
 * - 键盘事件（Enter 发送，Shift+Enter 换行）
 */
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MessageInput } from '@/components/chat/MessageInput';

describe('MessageInput', () => {
  const mockOnSend = jest.fn().mockResolvedValue(undefined);
  const mockOnStop = jest.fn().mockResolvedValue(undefined);
  const mockOnClearFailedContent = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  describe('渲染测试', () => {
    it('应正确渲染输入框和发送按钮', () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      expect(screen.getByPlaceholderText(/输入消息/)).toBeInTheDocument();
      expect(screen.getByTitle('发送消息')).toBeInTheDocument();
    });

    it('生成中应显示停止按钮', () => {
      render(
        <MessageInput
          isGenerating={true}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      expect(screen.getByTitle('停止生成')).toBeInTheDocument();
    });

    it('disabled 状态下输入框应禁用', () => {
      render(
        <MessageInput
          isGenerating={false}
          disabled={true}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      expect(textarea).toBeDisabled();
    });
  });

  describe('空消息拦截 - R_MSG_002', () => {
    it('空消息时发送按钮应禁用', () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const sendButton = screen.getByTitle('发送消息');
      expect(sendButton).toBeDisabled();
    });

    it('仅空白字符时发送按钮应禁用', () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      fireEvent.change(textarea, { target: { value: '   ' } });

      const sendButton = screen.getByTitle('发送消息');
      expect(sendButton).toBeDisabled();
    });

    it('有内容时发送按钮应启用', () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      fireEvent.change(textarea, { target: { value: 'hello' } });

      const sendButton = screen.getByTitle('发送消息');
      expect(sendButton).not.toBeDisabled();
    });
  });

  describe('长度限制 - R_MSG_001', () => {
    it('超过4000字符时应显示警告', () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      const longText = 'a'.repeat(4001);
      fireEvent.change(textarea, { target: { value: longText } });

      expect(screen.getByText(/超出字符限制/)).toBeInTheDocument();
    });

    it('超过4000字符时发送按钮应禁用', () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      const longText = 'a'.repeat(4001);
      fireEvent.change(textarea, { target: { value: longText } });

      const sendButton = screen.getByTitle('发送消息');
      expect(sendButton).toBeDisabled();
    });

    it('接近限制时应显示字符计数', () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      // 超过 90% (3600 字符) 时显示计数
      const nearLimitText = 'a'.repeat(3601);
      fireEvent.change(textarea, { target: { value: nearLimitText } });

      expect(screen.getByText(/3601\/4000/)).toBeInTheDocument();
    });

    it('最大长度4000字符时应允许发送', () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      const maxText = 'a'.repeat(4000);
      fireEvent.change(textarea, { target: { value: maxText } });

      const sendButton = screen.getByTitle('发送消息');
      expect(sendButton).not.toBeDisabled();
    });
  });

  describe('发送消息', () => {
    it('点击发送按钮应调用 onSend', async () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      fireEvent.change(textarea, { target: { value: 'hello' } });

      const sendButton = screen.getByTitle('发送消息');
      fireEvent.click(sendButton);

      // 等待异步操作完成
      await act(async () => {
        await Promise.resolve();
      });

      expect(mockOnSend).toHaveBeenCalledWith('hello');
    });

    it('发送后应清空输入框', async () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: 'hello' } });

      const sendButton = screen.getByTitle('发送消息');
      fireEvent.click(sendButton);

      await act(async () => {
        await Promise.resolve();
      });

      expect(textarea.value).toBe('');
    });

    it('发送时应去除首尾空白', async () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      fireEvent.change(textarea, { target: { value: '  hello  ' } });

      const sendButton = screen.getByTitle('发送消息');
      fireEvent.click(sendButton);

      await act(async () => {
        await Promise.resolve();
      });

      expect(mockOnSend).toHaveBeenCalledWith('hello');
    });
  });

  describe('防抖处理 - 300ms', () => {
    it('300ms内重复点击不应触发多次发送', async () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      fireEvent.change(textarea, { target: { value: 'hello' } });

      const sendButton = screen.getByTitle('发送消息');

      // 第一次点击
      fireEvent.click(sendButton);

      await act(async () => {
        await Promise.resolve();
      });

      // 100ms 后再次点击（在防抖时间内）
      act(() => {
        jest.advanceTimersByTime(100);
      });

      // 输入新内容
      fireEvent.change(textarea, { target: { value: 'world' } });
      fireEvent.click(sendButton);

      // onSend 只应被调用一次
      expect(mockOnSend).toHaveBeenCalledTimes(1);
    });

    it('300ms后再次点击应允许发送', async () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      fireEvent.change(textarea, { target: { value: 'hello' } });

      const sendButton = screen.getByTitle('发送消息');

      // 第一次点击
      fireEvent.click(sendButton);

      await act(async () => {
        await Promise.resolve();
      });

      // 300ms 后
      act(() => {
        jest.advanceTimersByTime(300);
      });

      // 输入新内容并发送
      fireEvent.change(textarea, { target: { value: 'world' } });
      fireEvent.click(sendButton);

      await act(async () => {
        await Promise.resolve();
      });

      expect(mockOnSend).toHaveBeenCalledTimes(2);
    });
  });

  describe('停止生成', () => {
    it('生成中时点击停止按钮应调用 onStop', async () => {
      render(
        <MessageInput
          isGenerating={true}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const stopButton = screen.getByTitle('停止生成');
      fireEvent.click(stopButton);

      await act(async () => {
        await Promise.resolve();
      });

      expect(mockOnStop).toHaveBeenCalled();
    });
  });

  describe('键盘事件', () => {
    it('Enter 键应发送消息', async () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      fireEvent.change(textarea, { target: { value: 'hello' } });
      fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });

      await act(async () => {
        await Promise.resolve();
      });

      expect(mockOnSend).toHaveBeenCalledWith('hello');
    });

    it('Shift+Enter 不应发送消息', async () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      fireEvent.change(textarea, { target: { value: 'hello' } });
      fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });

      expect(mockOnSend).not.toHaveBeenCalled();
    });

    it('生成中按 Enter 应停止生成', async () => {
      render(
        <MessageInput
          isGenerating={true}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/);
      fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });

      await act(async () => {
        await Promise.resolve();
      });

      expect(mockOnStop).toHaveBeenCalled();
    });
  });

  describe('失败内容恢复', () => {
    it('应恢复失败内容到输入框', () => {
      render(
        <MessageInput
          isGenerating={false}
          failedContent="failed message"
          onSend={mockOnSend}
          onStop={mockOnStop}
          onClearFailedContent={mockOnClearFailedContent}
        />
      );

      const textarea = screen.getByPlaceholderText(/输入消息/) as HTMLTextAreaElement;
      expect(textarea.value).toBe('failed message');
      expect(mockOnClearFailedContent).toHaveBeenCalled();
    });
  });

  describe('提示信息', () => {
    it('正常状态显示发送提示', () => {
      render(
        <MessageInput
          isGenerating={false}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      expect(screen.getByText(/按 Enter 发送/)).toBeInTheDocument();
    });

    it('生成中显示停止提示', () => {
      render(
        <MessageInput
          isGenerating={true}
          onSend={mockOnSend}
          onStop={mockOnStop}
        />
      );

      expect(screen.getByText(/AI 正在生成中/)).toBeInTheDocument();
    });
  });
});
