/**
 * 聊天服务
 *
 * 参考:
 * - process-model.md#三、消息发送与流式响应流程（P_CHAT_001）
 * - process-model.md#四、历史消息加载流程（P_CHAT_002）
 */
import api from './api';
import type {
  ApiResponse,
  ChatStreamEvent,
  GeneratingResponse,
  HistoryResponse,
  Message,
} from '@/types';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || '';

/**
 * 获取历史消息
 *
 * GET /api/v1/chat/messages/
 *
 * @param limit - 返回数量（默认50，最大100）
 * @param beforeSequence - 游标序号（分页用）
 */
export async function getMessages(
  limit: number = 50,
  beforeSequence?: number
): Promise<HistoryResponse> {
  const params = new URLSearchParams();
  params.set('limit', limit.toString());
  if (beforeSequence) {
    params.set('before_sequence', beforeSequence.toString());
  }

  const response = await api.get<ApiResponse<HistoryResponse>>(
    `/chat/messages/?${params.toString()}`
  );
  return response.data.data;
}

/**
 * 获取正在生成中的消息（用于页面刷新时检测）
 *
 * GET /api/v1/chat/generating/
 */
export async function getGeneratingMessage(): Promise<Message | null> {
  const response = await api.get<ApiResponse<GeneratingResponse>>(
    '/chat/generating/'
  );
  return response.data.data.message;
}

/**
 * 发送消息并获取流式响应
 *
 * POST /api/v1/chat/
 *
 * @param content - 消息内容
 * @param onChunk - 收到流式内容的回调
 * @param onDone - 完成时的回调
 * @param onError - 错误时的回调
 * @param signal - AbortController signal（用于取消请求）
 */
export async function sendMessage(
  content: string,
  callbacks: {
    onChunk?: (chunk: ChatStreamEvent) => void;
    onDone?: (messageId?: number) => void;
    onError?: (error: string) => void;
    onInterrupted?: (messageId?: number) => void;
  },
  signal?: AbortSignal
): Promise<void> {
  const { onChunk, onDone, onError, onInterrupted } = callbacks;

  try {
    const response = await fetch(`${API_BASE}/chat/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include', // httpOnly Cookie 自动携带
      body: JSON.stringify({ content }),
      signal,
    });

    if (!response.ok) {
      // 尝试解析错误响应
      try {
        const errorData = await response.json();
        onError?.(errorData.message || '发送失败');
      } catch {
        onError?.(`发送失败: ${response.status}`);
      }
      return;
    }

    // 处理 SSE 流式响应
    const reader = response.body?.getReader();
    if (!reader) {
      onError?.('无法读取响应流');
      return;
    }

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // 处理 SSE 数据行
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // 保留不完整的行

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data: ChatStreamEvent = JSON.parse(line.slice(6));

            switch (data.type) {
              case 'content':
                onChunk?.(data);
                break;
              case 'done':
                onDone?.(data.message_id);
                break;
              case 'error':
                onError?.(data.content || '生成失败');
                break;
              case 'interrupted':
                onInterrupted?.(data.message_id);
                break;
            }
          } catch {
            // 忽略解析错误
          }
        }
      }
    }
  } catch (error) {
    if ((error as Error).name === 'AbortError') {
      // 请求被取消，不需要处理
      return;
    }
    onError?.((error as Error).message || '网络错误');
  }
}

/**
 * 停止生成
 *
 * POST /api/v1/chat/stop/
 *
 * @param requestId - 请求ID
 */
export async function stopGeneration(requestId: string): Promise<boolean> {
  try {
    const response = await api.post<ApiResponse<null>>('/chat/stop/', {
      request_id: requestId,
    });
    return response.data.code === 'SUCCESS';
  } catch {
    return false;
  }
}

/**
 * 继续生成（从中断处恢复）
 *
 * POST /api/v1/chat/resume/
 *
 * 参考: behavior-model.md#2.5 继续生成（B_CHAT_005）
 * 用于 status=3（中断）消息的继续生成
 *
 * @param requestId - 原请求ID
 * @param callbacks - 回调函数
 * @param signal - AbortController signal
 */
export async function resumeGeneration(
  requestId: string,
  callbacks: {
    onChunk?: (chunk: ChatStreamEvent) => void;
    onDone?: (messageId?: number) => void;
    onError?: (error: string) => void;
    onInterrupted?: (messageId?: number) => void;
  },
  signal?: AbortSignal
): Promise<void> {
  const { onChunk, onDone, onError, onInterrupted } = callbacks;

  try {
    const response = await fetch(`${API_BASE}/chat/resume/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify({ request_id: requestId }),
      signal,
    });

    if (!response.ok) {
      try {
        const errorData = await response.json();
        onError?.(errorData.message || '恢复生成失败');
      } catch {
        onError?.(`恢复生成失败: ${response.status}`);
      }
      return;
    }

    // 处理 SSE 流式响应
    const reader = response.body?.getReader();
    if (!reader) {
      onError?.('无法读取响应流');
      return;
    }

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data: ChatStreamEvent = JSON.parse(line.slice(6));

            switch (data.type) {
              case 'content':
                onChunk?.(data);
                break;
              case 'done':
                onDone?.(data.message_id);
                break;
              case 'error':
                onError?.(data.content || '恢复生成失败');
                break;
              case 'interrupted':
                onInterrupted?.(data.message_id);
                break;
            }
          } catch {
            // 忽略解析错误
          }
        }
      }
    }
  } catch (error) {
    if ((error as Error).name === 'AbortError') {
      return;
    }
    onError?.((error as Error).message || '网络错误');
  }
}

/**
 * 重连流式响应（用于页面刷新时重连生成中的消息）
 *
 * GET /api/v1/chat/reconnect/?request_id={request_id}
 *
 * 参考: behavior-model.md#2.4 流式响应重连（B_CHAT_004）
 * 用于 status=2（生成中）消息的 SSE 重连
 *
 * @param requestId - 请求ID
 * @param callbacks - 回调函数
 * @param signal - AbortController signal
 */
export async function reconnectStream(
  requestId: string,
  callbacks: {
    onChunk?: (chunk: ChatStreamEvent) => void;
    onDone?: (messageId?: number) => void;
    onError?: (error: string) => void;
    onInterrupted?: (messageId?: number) => void;
  },
  signal?: AbortSignal
): Promise<void> {
  const { onChunk, onDone, onError, onInterrupted } = callbacks;

  try {
    const response = await fetch(
      `${API_BASE}/chat/reconnect/?request_id=${encodeURIComponent(requestId)}`,
      {
        method: 'GET',
        credentials: 'include',
        signal,
      }
    );

    if (!response.ok) {
      try {
        const errorData = await response.json();
        onError?.(errorData.message || '重连失败');
      } catch {
        onError?.(`重连失败: ${response.status}`);
      }
      return;
    }

    // 处理 SSE 流式响应
    const reader = response.body?.getReader();
    if (!reader) {
      onError?.('无法读取响应流');
      return;
    }

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data: ChatStreamEvent = JSON.parse(line.slice(6));

            switch (data.type) {
              case 'content':
                onChunk?.(data);
                break;
              case 'done':
                onDone?.(data.message_id);
                break;
              case 'error':
                onError?.(data.content || '重连失败');
                break;
              case 'interrupted':
                onInterrupted?.(data.message_id);
                break;
            }
          } catch {
            // 忽略解析错误
          }
        }
      }
    }
  } catch (error) {
    if ((error as Error).name === 'AbortError') {
      return;
    }
    onError?.((error as Error).message || '网络错误');
  }
}
