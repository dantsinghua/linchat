/**
 * 聊天服务
 *
 * 参考:
 * - process-model.md#三、消息发送与流式响应流程（P_CHAT_001）
 * - process-model.md#四、历史消息加载流程（P_CHAT_002）
 */
import api from './api';
import { trigger401Redirect } from '@/services/authGuard';
import type {
  ApiResponse,
  ChatStreamEvent,
  GeneratingResponse,
  HistoryResponse,
  Message,
} from '@/types';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || '';

const JSON_HEADERS = { 'Content-Type': 'application/json' } as const;

// ============ SSE 流回调类型 ============

interface StreamCallbacks {
  onChunk?: (chunk: ChatStreamEvent) => void;
  onDone?: (messageId?: number) => void;
  onError?: (error: string, data?: ChatStreamEvent['data']) => void | Promise<void>;
  onInterrupted?: (messageId?: number) => void;
  onContextCompacting?: () => void;
  onContextCompacted?: () => void;
}

// ============ 公共 SSE 流处理 ============

/**
 * 通用 SSE 流处理：fetch → 401 检查 → reader 读取 → SSE 解析 → 回调分发
 */
async function streamSSE(
  url: string,
  init: RequestInit,
  { onChunk, onDone, onError, onInterrupted, onContextCompacting, onContextCompacted }: StreamCallbacks,
  errorLabel: string
): Promise<void> {
  // 心跳超时：后端每 15s 发一次 heartbeat，45s 无数据则判定流断开
  const HEARTBEAT_TIMEOUT_MS = 45_000;
  const HEARTBEAT_CHECK_MS = 10_000;

  try {
    const response = await fetch(`${API_BASE}${url}`, {
      credentials: 'include',
      ...init,
    });

    if (!response.ok) {
      if (response.status === 401) { trigger401Redirect(); return; }
      try {
        const errorData = await response.json();
        onError?.(errorData.message || errorLabel);
      } catch {
        onError?.(`${errorLabel}: ${response.status}`);
      }
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) { onError?.('无法读取响应流'); return; }

    const decoder = new TextDecoder();
    let buffer = '';
    let receivedTerminal = false;
    let lastDataTime = Date.now();

    // 心跳超时检测：定时检查，超时则取消 reader 触发 __SSE_STREAM_BROKEN__
    const heartbeatTimer = setInterval(() => {
      if (Date.now() - lastDataTime > HEARTBEAT_TIMEOUT_MS) {
        reader.cancel();
      }
    }, HEARTBEAT_CHECK_MS);

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        lastDataTime = Date.now();
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data: ChatStreamEvent = JSON.parse(line.slice(6));
            switch (data.type) {
              case 'heartbeat':        break; // 仅更新 lastDataTime
              case 'content':            onChunk?.(data); break;
              case 'done':               receivedTerminal = true; onDone?.(data.message_id); break;
              case 'error':              receivedTerminal = true; onError?.(data.content || errorLabel, data.data); break;
              case 'interrupted':        receivedTerminal = true; onInterrupted?.(data.message_id); break;
              case 'context_compacting': onContextCompacting?.(); break;
              case 'context_compacted':  onContextCompacted?.(); break;
            }
          } catch { /* 忽略解析错误 */ }
        }
      }
    } finally {
      clearInterval(heartbeatTimer);
    }

    // 流读完但未收到终态事件 → 异常断开
    if (!receivedTerminal) {
      onError?.('__SSE_STREAM_BROKEN__');
    }
  } catch (error) {
    if ((error as Error).name === 'AbortError') return;
    onError?.((error as Error).message || '网络错误');
  }
}

// ============ REST API ============

/**
 * 获取历史消息（支持游标分页）
 */
export async function getMessages(
  limit: number = 50,
  beforeSequence?: number
): Promise<HistoryResponse> {
  const params = new URLSearchParams({ limit: limit.toString() });
  if (beforeSequence) params.set('before_sequence', beforeSequence.toString());

  const response = await api.get<ApiResponse<HistoryResponse>>(
    `/chat/messages/?${params}`
  );
  return response.data.data;
}

/**
 * 获取正在生成中的消息（页面刷新时检测）
 */
export async function getGeneratingMessage(): Promise<Message | null> {
  const response = await api.get<ApiResponse<GeneratingResponse>>(
    '/chat/generating/'
  );
  return response.data.data.message;
}

/**
 * 停止生成
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

// ============ SSE 流式 API ============

/**
 * 发送消息并获取流式响应
 *
 * @param content 消息文本
 * @param callbacks SSE 流回调
 * @param signal 取消信号
 * @param attachmentUuids 附件 UUID 列表（多模态消息）
 */
export async function sendMessage(
  content: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
  attachmentUuids?: string[]
): Promise<void> {
  const body: Record<string, unknown> = { content };
  if (attachmentUuids && attachmentUuids.length > 0) {
    body.attachments = attachmentUuids;
  }
  await streamSSE('/chat/', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
    signal,
  }, callbacks, '发送失败');
}

/**
 * 继续生成（从中断处恢复，status=3）
 */
export async function resumeGeneration(
  requestId: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal
): Promise<void> {
  await streamSSE('/chat/resume/', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ request_id: requestId }),
    signal,
  }, callbacks, '恢复生成失败');
}

/**
 * 重连流式响应（页面刷新时重连 status=2 的消息）
 */
export async function reconnectStream(
  requestId: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal
): Promise<void> {
  await streamSSE(
    `/chat/reconnect/?request_id=${encodeURIComponent(requestId)}`,
    { method: 'GET', signal },
    callbacks,
    '重连失败'
  );
}
