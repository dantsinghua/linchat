/**
 * 聊天流式响应 Hook
 *
 * 管理完整的聊天状态机：历史加载、发送、重连、恢复、停止
 */
import { useCallback, useEffect, useRef } from 'react';
import { toast } from 'sonner';

import { isAuthRedirecting } from '@/services/authGuard';
import { getErrorMessage } from '@/components/chat/NetworkError';
import {
  getGeneratingMessage,
  getMessages,
  reconnectStream,
  resumeGeneration,
  sendMessage,
  stopGeneration,
} from '@/services/chatService';
import { cancelInference } from '@/services/mediaApi';
import { useChatStore } from '@/stores/chatStore';
import type { ChatStreamEvent, Message, MessageStatus } from '@/types';
import type { MediaAttachment } from '@/types/media';

interface UseChatStreamReturn {
  messages: Message[];
  isGenerating: boolean;
  isCompacting: boolean;
  isLoadingHistory: boolean;
  hasMore: boolean;
  error: string | null;
  failedContent: string | null;
  failedAttachments: MediaAttachment[] | null;
  gatewayRetryAfter: number;
  send: (content: string, attachments?: MediaAttachment[]) => Promise<void>;
  stop: () => Promise<void>;
  resume: (messageId: number) => Promise<void>;
  loadMore: () => Promise<void>;
  reload: () => Promise<void>;
  clearFailedContent: () => void;
}

export function useChatStream(): UseChatStreamReturn {
  const store = useChatStore();
  const abortRef = useRef<AbortController | null>(null);
  const reqIdRef = useRef<string | null>(null);
  const loadCountRef = useRef(0);

  /** 重置流状态（生成结束/错误/中断时调用） */
  const resetStream = useCallback(() => {
    store.setIsGenerating(false);
    store.setCurrentRequestId(null);
    reqIdRef.current = null;
    abortRef.current = null;
  }, [store]);

  /** 创建新的 AbortController 并绑定 */
  const newAbort = useCallback(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    return controller;
  }, []);

  /** 循环重连：覆盖长耗时工具（文档解析等）的多次 SSE 超时断开 */
  const reconnectWithRetry = useCallback(async (
    messageId: number,
    requestId: string,
    controller: AbortController,
    onFinalError: (err: string) => void,
  ) => {
    const MAX_RETRIES = 12; // 12 × ~45s ≈ 9 分钟

    for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
      if (controller.signal.aborted) return;
      if (attempt > 1) {
        await new Promise((r) => setTimeout(r, 2000));
        if (controller.signal.aborted) return;
      }

      // 检查后端状态
      try {
        const generating = await getGeneratingMessage();
        if (!generating || generating.message_id !== messageId) {
          // 消息不存在或不匹配 → 从 DB 加载最新
          const history = await getMessages(1);
          const latest = history.messages?.[0];
          if (latest && latest.message_id === messageId) {
            store.updateMessage(messageId, {
              content: latest.content,
              status: latest.status as MessageStatus,
            });
          }
          resetStream();
          return;
        }
        if (generating.status !== 2) {
          // 已完成/中断/失败
          store.updateMessage(messageId, {
            content: generating.content || '',
            status: generating.status as MessageStatus,
          });
          resetStream();
          return;
        }
      } catch { /* 查询失败，继续尝试重连 */ }

      // 后端仍在生成 → 重连 SSE 流
      // 清空内容（后端重连会重发全量）
      store.updateMessage(messageId, { content: '' });

      let streamBroken = false;
      await reconnectStream(requestId, {
        onChunk: (c) => store.appendContent(messageId, c.content),
        onDone: () => {
          store.updateMessage(messageId, { status: 1 as MessageStatus });
          resetStream();
        },
        onError: (err) => {
          if (err === '__SSE_STREAM_BROKEN__') {
            streamBroken = true;
          } else {
            onFinalError(err);
          }
        },
        onInterrupted: () => {
          store.updateMessage(messageId, { status: 3 as MessageStatus });
          resetStream();
          toast.info('响应已中断，如有需要请复制已显示内容');
        },
      }, controller.signal);

      if (!store.isGenerating) return; // onDone/onInterrupted 已处理
      if (!streamBroken) return; // onFinalError 已处理

      // 流又断了，继续下一轮重连
      console.warn(`[reconnectWithRetry] 第 ${attempt}/${MAX_RETRIES} 次重连后流再次断开，继续重试...`);
    }

    // 超过最大重试次数
    onFinalError('长时间任务超时，请刷新页面查看结果');
  }, [store, resetStream]);

  /** 加载历史消息 + 自动重连生成中的流 */
  const loadHistory = useCallback(async () => {
    if (isAuthRedirecting()) return;
    if (store.isLoadingHistory) return;
    if (store.isGenerating) return;  // 生成中不覆盖消息
    store.setIsLoadingHistory(true);
    store.setError(null);

    const thisLoad = ++loadCountRef.current;

    try {
      const data = await getMessages(50);
      if (loadCountRef.current !== thisLoad) return; // 放弃过期结果
      store.setMessages(data.messages);
      store.setHasMore(data.has_more);

      // 页面刷新时重连 status=2 的生成中消息
      const msg = await getGeneratingMessage();
      if (msg?.request_id && msg.status === 2) {
        store.setIsGenerating(true);
        reqIdRef.current = msg.request_id;
        store.setCurrentRequestId(msg.request_id);

        await reconnectWithRetry(
          msg.message_id,
          msg.request_id,
          newAbort(),
          (err) => {
            store.setError(getErrorMessage(err));
            store.updateMessage(msg.message_id, { status: 0 as MessageStatus });
            resetStream();
          },
        );
      }
    } catch (err) {
      store.setError(getErrorMessage((err as Error).message || '加载历史消息失败'));
    } finally {
      store.setIsLoadingHistory(false);
    }
  }, [store, resetStream, newAbort, reconnectWithRetry]);

  /** 加载更多历史消息（向上滚动） */
  const loadMore = useCallback(async () => {
    const oldest = store.messages[0];
    if (store.isLoadingHistory || !store.hasMore || !oldest) return;
    store.setIsLoadingHistory(true);

    try {
      const data = await getMessages(50, oldest.sequence);
      store.prependMessages(data.messages);
      store.setHasMore(data.has_more);
    } catch (err) {
      store.setError(getErrorMessage((err as Error).message || '加载更多消息失败'));
    } finally {
      store.setIsLoadingHistory(false);
    }
  }, [store]);

  /** 发送消息（支持多模态附件） */
  const send = useCallback(async (content: string, attachments?: MediaAttachment[]) => {
    if (store.isGenerating) return;
    store.setError(null);
    store.setFailedContent(null);

    const attachmentUuids = attachments?.map((a) => a.attachment_uuid);

    const originalMessages = [...store.messages];
    const lastMsg = store.messages[store.messages.length - 1];
    const seq = lastMsg ? lastMsg.sequence + 1 : 1;
    const now = Date.now();

    const tempUser: Message = {
      message_id: now, message_uuid: `temp-${now}`,
      role: 'user', content, status: 1, sequence: seq,
      created_time: new Date().toISOString(),
      attachments: attachments || undefined,
    };
    const tempAssistant: Message = {
      message_id: now + 1, message_uuid: `temp-${now + 1}`,
      role: 'assistant', content: '', status: 2, sequence: seq + 1,
      created_time: new Date().toISOString(),
    };

    store.addMessage(tempUser);
    store.addMessage(tempAssistant);
    store.setIsGenerating(true);

    const controller = newAbort();
    let realId: number | undefined;

    const handleFail = (errMsg: string, data?: ChatStreamEvent['data']) => {
      // T067a: Gateway 模型错误特殊处理
      const gatewayError = data?.gateway_error;
      let friendly: string;

      if (gatewayError === 'E3001') {
        friendly = '请求的模型不存在';
      } else if (gatewayError === 'E3002') {
        friendly = '多模态服务暂时不可用，请稍后重试';
        if (data?.retry_after) {
          store.setGatewayRetryAfter(data.retry_after);
        }
      } else {
        friendly = getErrorMessage(errMsg);
      }

      store.setError(friendly);
      if (!realId) {
        store.setMessages(originalMessages);
        store.setFailedContent(content);
        store.setFailedAttachments(attachments ?? null);
      } else {
        store.updateMessage(realId, { status: 0 as MessageStatus });
      }
      resetStream();
      toast.error(friendly);
    };

    try {
      await sendMessage(content, {
        onChunk: (chunk) => {
          if (chunk.request_id && !reqIdRef.current) {
            reqIdRef.current = chunk.request_id;
            store.setCurrentRequestId(chunk.request_id);
          }
          if (chunk.message_id && !realId) {
            realId = chunk.message_id;
            store.updateMessage(tempAssistant.message_id, {
              message_id: chunk.message_id,
              request_id: reqIdRef.current,
            });
          }
          store.appendContent(realId || tempAssistant.message_id, chunk.content);
        },
        onDone: (msgId) => {
          store.updateMessage(msgId || realId || tempAssistant.message_id, { status: 1 as MessageStatus });
          store.setIsCompacting(false);
          resetStream();
        },
        onError: async (err, data) => {
          store.setIsCompacting(false);
          if (err === '__SSE_STREAM_BROKEN__' && realId && reqIdRef.current) {
            await reconnectWithRetry(realId, reqIdRef.current, controller, (e) => handleFail(e));
            return;
          }
          handleFail(err, data);
        },
        onInterrupted: (msgId) => {
          store.updateMessage(msgId || realId || tempAssistant.message_id, { status: 3 as MessageStatus });
          store.setIsCompacting(false);
          resetStream();
          toast.info('响应已中断，如有需要请复制已显示内容');
        },
        onContextCompacting: () => store.setIsCompacting(true),
        onContextCompacted: () => store.setIsCompacting(false),
      }, controller.signal, attachmentUuids);
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        handleFail((err as Error).message || '发送失败');
      } else {
        resetStream();
      }
    }
  }, [store, resetStream, newAbort, reconnectWithRetry]);

  /** 停止生成（T039: 同时调用推理取消 API） */
  const stop = useCallback(async () => {
    if (!store.isGenerating) return;
    abortRef.current?.abort();
    if (reqIdRef.current) {
      // 并行调用：停止生成 + 推理取消（非多模态时 cancel 返回 404，无副作用）
      await Promise.allSettled([
        stopGeneration(reqIdRef.current),
        cancelInference(reqIdRef.current),
      ]);
    }
    store.setIsGenerating(false);
    store.setCurrentRequestId(null);
    reqIdRef.current = null;
  }, [store]);

  /** 继续生成（status=3 中断消息） */
  const resume = useCallback(async (messageId: number) => {
    if (store.isGenerating) return;

    const targetMsg = store.messages.find((m) => m.message_id === messageId);
    if (!targetMsg?.request_id) {
      toast.error('无法继续生成：缺少请求ID');
      return;
    }

    store.setIsGenerating(true);
    store.setError(null);
    store.updateMessage(messageId, {
      content: targetMsg.content.replace('[已中断]', ''),
      status: 2 as MessageStatus,
    });

    try {
      await resumeGeneration(targetMsg.request_id, {
        onChunk: (c) => store.appendContent(messageId, c.content),
        onDone: () => {
          store.updateMessage(messageId, { status: 1 as MessageStatus });
          resetStream();
        },
        onError: (err) => {
          const friendly = getErrorMessage(err);
          store.setError(friendly);
          store.updateMessage(messageId, { status: 0 as MessageStatus });
          resetStream();
          toast.error(friendly);
        },
        onInterrupted: () => {
          store.updateMessage(messageId, { status: 3 as MessageStatus });
          resetStream();
          toast.info('响应已中断，如有需要请复制已显示内容');
        },
      }, newAbort().signal);
    } catch (err) {
      store.setError(getErrorMessage((err as Error).message || '恢复生成失败'));
      resetStream();
    }
  }, [store, resetStream, newAbort]);

  const reload = useCallback(async () => {
    store.setMessages([]);
    store.setHasMore(true);
    await loadHistory();
  }, [store, loadHistory]);

  const clearFailedContent = useCallback(() => {
    store.setFailedContent(null);
    store.setFailedAttachments(null);
  }, [store]);

  // 挂载时加载历史，卸载时取消请求
  useEffect(() => {
    loadHistory();
    return () => { abortRef.current?.abort(); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    messages: store.messages,
    isGenerating: store.isGenerating,
    isCompacting: store.isCompacting,
    isLoadingHistory: store.isLoadingHistory,
    hasMore: store.hasMore,
    error: store.error,
    failedContent: store.failedContent,
    failedAttachments: store.failedAttachments,
    gatewayRetryAfter: store.gatewayRetryAfter,
    send, stop, resume, loadMore, reload, clearFailedContent,
  };
}
