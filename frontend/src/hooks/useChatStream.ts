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
import { useChatStore } from '@/stores/chatStore';
import type { Message, MessageStatus } from '@/types';

interface UseChatStreamReturn {
  messages: Message[];
  isGenerating: boolean;
  isCompacting: boolean;
  isLoadingHistory: boolean;
  hasMore: boolean;
  error: string | null;
  failedContent: string | null;
  send: (content: string) => Promise<void>;
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

  /** 加载历史消息 + 自动重连生成中的流 */
  const loadHistory = useCallback(async () => {
    if (isAuthRedirecting()) return;
    if (store.isLoadingHistory) return;
    store.setIsLoadingHistory(true);
    store.setError(null);

    try {
      const data = await getMessages(50);
      store.setMessages(data.messages);
      store.setHasMore(data.has_more);

      // 页面刷新时重连 status=2 的生成中消息
      const msg = await getGeneratingMessage();
      if (msg?.request_id && msg.status === 2) {
        store.setIsGenerating(true);
        reqIdRef.current = msg.request_id;
        store.setCurrentRequestId(msg.request_id);
        store.updateMessage(msg.message_id, { content: '' });

        await reconnectStream(msg.request_id, {
          onChunk: (c) => store.appendContent(msg.message_id, c.content),
          onDone: () => {
            store.updateMessage(msg.message_id, { status: 1 as MessageStatus });
            resetStream();
          },
          onError: (err) => {
            store.setError(err);
            store.updateMessage(msg.message_id, { status: 0 as MessageStatus });
            resetStream();
          },
          onInterrupted: () => {
            store.updateMessage(msg.message_id, { status: 3 as MessageStatus });
            resetStream();
            toast.info('响应已中断，如有需要请复制已显示内容');
          },
        }, newAbort().signal);
      }
    } catch (err) {
      store.setError(getErrorMessage((err as Error).message || '加载历史消息失败'));
    } finally {
      store.setIsLoadingHistory(false);
    }
  }, [store, resetStream, newAbort]);

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

  /** 发送消息 */
  const send = useCallback(async (content: string) => {
    if (store.isGenerating) return;
    store.setError(null);
    store.setFailedContent(null);

    const originalMessages = [...store.messages];
    const lastMsg = store.messages[store.messages.length - 1];
    const seq = lastMsg ? lastMsg.sequence + 1 : 1;
    const now = Date.now();

    const tempUser: Message = {
      message_id: now, message_uuid: `temp-${now}`,
      role: 'user', content, status: 1, sequence: seq,
      created_time: new Date().toISOString(),
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

    const handleFail = (errMsg: string) => {
      const friendly = getErrorMessage(errMsg);
      store.setError(friendly);
      store.setMessages(originalMessages);
      store.setFailedContent(content);
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
        onError: (err) => {
          store.setIsCompacting(false);
          handleFail(err);
        },
        onInterrupted: (msgId) => {
          store.updateMessage(msgId || realId || tempAssistant.message_id, { status: 3 as MessageStatus });
          store.setIsCompacting(false);
          resetStream();
          toast.info('响应已中断，如有需要请复制已显示内容');
        },
        onContextCompacting: () => store.setIsCompacting(true),
        onContextCompacted: () => store.setIsCompacting(false),
      }, controller.signal);
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        handleFail((err as Error).message || '发送失败');
      } else {
        resetStream();
      }
    }
  }, [store, resetStream, newAbort]);

  /** 停止生成 */
  const stop = useCallback(async () => {
    if (!store.isGenerating) return;
    abortRef.current?.abort();
    if (reqIdRef.current) await stopGeneration(reqIdRef.current);
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
    send, stop, resume, loadMore, reload, clearFailedContent,
  };
}
