/**
 * useDocParse Hook 单元测试 (T085)
 *
 * 覆盖: SSE 事件接收与状态流转、completed 后自动获取结果、
 * failed 后展示错误、FR-034 截断逻辑、组合文本格式验证
 */
import { renderHook, act } from '@testing-library/react';
import { useDocParse } from '@/hooks/useDocParse';

jest.mock('@/services/mediaApi', () => ({
  createDocParseTask: jest.fn(),
  getDocParseResult: jest.fn(),
}));

const { createDocParseTask, getDocParseResult } =
  require('@/services/mediaApi') as {
    createDocParseTask: jest.Mock;
    getDocParseResult: jest.Mock;
  };

function dispatchDocParseEvent(detail: Record<string, unknown>) {
  window.dispatchEvent(
    new CustomEvent('doc_parse_progress', { detail })
  );
}

describe('useDocParse', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('初始状态', () => {
    it('状态应为 idle', () => {
      const { result } = renderHook(() => useDocParse());
      expect(result.current.status).toBe('idle');
      expect(result.current.progress).toBeNull();
      expect(result.current.result).toBeNull();
      expect(result.current.error).toBeNull();
    });

    it('statusText 应为空', () => {
      const { result } = renderHook(() => useDocParse());
      expect(result.current.statusText).toBe('');
    });
  });

  describe('parse 方法', () => {
    it('成功创建任务后应设为 pending', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123', status: 'pending' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('attachment-uuid');
      });

      expect(result.current.status).toBe('pending');
      expect(result.current.statusText).toBe('等待解析...');
      expect(createDocParseTask).toHaveBeenCalledWith(
        'attachment-uuid',
        undefined
      );
    });

    it('应传递 pages 参数', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('attachment-uuid', '1-5');
      });

      expect(createDocParseTask).toHaveBeenCalledWith(
        'attachment-uuid',
        '1-5'
      );
    });

    it('创建任务失败应设为 failed', async () => {
      createDocParseTask.mockRejectedValue(new Error('网络错误'));

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('attachment-uuid');
      });

      expect(result.current.status).toBe('failed');
      expect(result.current.error).toBe('网络错误');
    });

    it('无 task_id 应设为 failed', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: null },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('attachment-uuid');
      });

      expect(result.current.status).toBe('failed');
      expect(result.current.error).toContain('未获取到解析任务 ID');
    });

    it('E6006 PAGE_LIMIT_EXCEEDED 应显示友好提示', async () => {
      createDocParseTask.mockRejectedValue(
        new Error('PAGE_LIMIT_EXCEEDED')
      );

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('attachment-uuid');
      });

      expect(result.current.status).toBe('failed');
      expect(result.current.error).toContain('文档页数超过限制');
      expect(result.current.error).toContain('最大 200 页');
    });
  });

  describe('SSE 事件处理', () => {
    it('pending 事件应更新状态', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      act(() => {
        dispatchDocParseEvent({
          task_id: 'task-123',
          status: 'pending',
        });
      });

      expect(result.current.status).toBe('pending');
    });

    it('processing 事件应更新进度', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      act(() => {
        dispatchDocParseEvent({
          task_id: 'task-123',
          status: 'processing',
          progress: { current: 3, total: 10 },
        });
      });

      expect(result.current.status).toBe('processing');
      expect(result.current.progress).toEqual({ current: 3, total: 10 });
      expect(result.current.statusText).toContain('3/10');
    });

    it('completed 事件应自动获取结果', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });
      getDocParseResult.mockResolvedValue({
        data: { content: '# Parsed Document\nHello world', format: 'markdown' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      await act(async () => {
        dispatchDocParseEvent({
          task_id: 'task-123',
          status: 'completed',
        });
        // Wait for fetchResult async call
        await new Promise((resolve) => setTimeout(resolve, 50));
      });

      expect(result.current.status).toBe('completed');
      expect(result.current.result).toBe('# Parsed Document\nHello world');
      expect(getDocParseResult).toHaveBeenCalledWith('task-123');
    });

    it('failed 事件应设置错误', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      act(() => {
        dispatchDocParseEvent({
          task_id: 'task-123',
          status: 'failed',
          error_message: '文档格式不支持',
        });
      });

      expect(result.current.status).toBe('failed');
      expect(result.current.error).toBe('文档格式不支持');
    });

    it('failed 事件无 error_message 应使用默认', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      act(() => {
        dispatchDocParseEvent({
          task_id: 'task-123',
          status: 'failed',
        });
      });

      expect(result.current.error).toBe('文档解析失败');
    });

    it('不同 task_id 的事件应被忽略', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      act(() => {
        dispatchDocParseEvent({
          task_id: 'other-task',
          status: 'completed',
        });
      });

      // Should still be pending (not completed)
      expect(result.current.status).toBe('pending');
    });
  });

  describe('FR-034 截断逻辑', () => {
    it('超过 8000 字符应截断并追加提示', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });
      const longContent = 'a'.repeat(9000);
      getDocParseResult.mockResolvedValue({
        data: { content: longContent, format: 'markdown' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      await act(async () => {
        dispatchDocParseEvent({
          task_id: 'task-123',
          status: 'completed',
        });
        await new Promise((resolve) => setTimeout(resolve, 50));
      });

      expect(result.current.result!.length).toBe(
        8000 + '\n\n[内容已截断]'.length
      );
      expect(result.current.result).toContain('[内容已截断]');
      // 截断部分应是前 8000 个字符
      expect(result.current.result!.startsWith('a'.repeat(8000))).toBe(true);
    });

    it('恰好 8000 字符不应截断', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });
      const exactContent = 'b'.repeat(8000);
      getDocParseResult.mockResolvedValue({
        data: { content: exactContent, format: 'markdown' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      await act(async () => {
        dispatchDocParseEvent({
          task_id: 'task-123',
          status: 'completed',
        });
        await new Promise((resolve) => setTimeout(resolve, 50));
      });

      expect(result.current.result).toBe(exactContent);
      expect(result.current.result).not.toContain('[内容已截断]');
    });

    it('空结果应正常处理', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });
      getDocParseResult.mockResolvedValue({
        data: { content: '', format: 'markdown' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      await act(async () => {
        dispatchDocParseEvent({
          task_id: 'task-123',
          status: 'completed',
        });
        await new Promise((resolve) => setTimeout(resolve, 50));
      });

      expect(result.current.result).toBe('');
    });
  });

  describe('组合文本格式验证', () => {
    it('result 可用于组合正确的发送格式', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });
      const markdown = '# 标题\n\n表格内容...';
      getDocParseResult.mockResolvedValue({
        data: { content: markdown, format: 'markdown' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      await act(async () => {
        dispatchDocParseEvent({
          task_id: 'task-123',
          status: 'completed',
        });
        await new Promise((resolve) => setTimeout(resolve, 50));
      });

      // 验证可以组合为正确的发送格式
      const userQuestion = '这个文档说了什么？';
      const combined = `[文档内容]\n${result.current.result}\n[/文档内容]\n\n${userQuestion}`;

      expect(combined).toMatch(/^\[文档内容\]\n/);
      expect(combined).toContain(markdown);
      expect(combined).toMatch(/\n\[\/文档内容\]\n\n/);
      expect(combined).toContain(userQuestion);
      expect(combined).toBe(
        `[文档内容]\n# 标题\n\n表格内容...\n[/文档内容]\n\n这个文档说了什么？`
      );
    });
  });

  describe('reset', () => {
    it('应重置所有状态到初始值', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      expect(result.current.status).toBe('pending');

      act(() => {
        result.current.reset();
      });

      expect(result.current.status).toBe('idle');
      expect(result.current.progress).toBeNull();
      expect(result.current.result).toBeNull();
      expect(result.current.error).toBeNull();
      expect(result.current.statusText).toBe('');
    });
  });

  describe('statusText', () => {
    it('processing 无进度时显示"解析中..."', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      act(() => {
        dispatchDocParseEvent({
          task_id: 'task-123',
          status: 'processing',
        });
      });

      expect(result.current.statusText).toBe('解析中...');
    });

    it('completed 有 result 时显示"解析完成"', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });
      getDocParseResult.mockResolvedValue({
        data: { content: 'content', format: 'markdown' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      await act(async () => {
        dispatchDocParseEvent({
          task_id: 'task-123',
          status: 'completed',
        });
        await new Promise((resolve) => setTimeout(resolve, 50));
      });

      expect(result.current.statusText).toBe('解析完成');
    });

    it('failed 应显示错误信息', async () => {
      createDocParseTask.mockResolvedValue({
        data: { task_id: 'task-123' },
      });

      const { result } = renderHook(() => useDocParse());

      await act(async () => {
        await result.current.parse('uuid');
      });

      act(() => {
        dispatchDocParseEvent({
          task_id: 'task-123',
          status: 'failed',
          error_message: '解析超时',
        });
      });

      expect(result.current.statusText).toBe('解析超时');
    });
  });
});
