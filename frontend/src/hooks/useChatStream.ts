/**
 * 聊天流式响应 Hook
 *
 * 参考:
 * - process-model.md#三、消息发送与流式响应流程（P_CHAT_001）
 * - behavior-model.md#2.4 流式响应重连（B_CHAT_004）
 */
import { useCallback, useEffect, useRef } from 'react';
import { toast } from 'sonner';

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
  const {
    messages,
    isGenerating,
    isLoadingHistory,
    hasMore,
    error,
    failedContent,
    setMessages,
    addMessage,
    updateMessage,
    appendContent,
    prependMessages,
    setIsLoadingHistory,
    setIsGenerating,
    setCurrentRequestId,
    setHasMore,
    setError,
    setFailedContent,
  } = useChatStore();

  const abortControllerRef = useRef<AbortController | null>(null);
  const currentRequestIdRef = useRef<string | null>(null);

  /**
   * 加载历史消息
   *
   * 参考: behavior-model.md#2.4 流式响应重连（B_CHAT_004）
   */
  const loadHistory = useCallback(async () => {
    if (isLoadingHistory) return;

    setIsLoadingHistory(true);
    setError(null);

    try {
      const data = await getMessages(50);
      setMessages(data.messages);
      setHasMore(data.has_more);

      // 检查是否有正在生成中的消息（用于页面刷新时重连）
      // status=2 时使用 reconnectStream API
      const generatingMsg = await getGeneratingMessage();
      if (generatingMsg && generatingMsg.request_id && generatingMsg.status === 2) {
        // 自动重连继续接收
        setIsGenerating(true);
        currentRequestIdRef.current = generatingMsg.request_id;
        setCurrentRequestId(generatingMsg.request_id);

        // 重连 SSE（使用 reconnectStream API）
        const controller = new AbortController();
        abortControllerRef.current = controller;

        // 清空当前内容，因为 reconnectStream 会返回完整内容
        updateMessage(generatingMsg.message_id, { content: '' });

        await reconnectStream(
          generatingMsg.request_id,
          {
            onChunk: (chunk) => {
              appendContent(generatingMsg.message_id, chunk.content);
            },
            onDone: () => {
              updateMessage(generatingMsg.message_id, { status: 1 as MessageStatus });
              setIsGenerating(false);
              setCurrentRequestId(null);
              currentRequestIdRef.current = null;
            },
            onError: (err) => {
              setError(err);
              updateMessage(generatingMsg.message_id, { status: 0 as MessageStatus });
              setIsGenerating(false);
              setCurrentRequestId(null);
            },
            onInterrupted: () => {
              updateMessage(generatingMsg.message_id, {
                status: 3 as MessageStatus,
              });
              setIsGenerating(false);
              setCurrentRequestId(null);
              toast.info('响应已中断，如有需要请复制已显示内容');
            },
          },
          controller.signal
        );
      }
    } catch (err) {
      const friendlyMessage = getErrorMessage((err as Error).message || '加载历史消息失败');
      setError(friendlyMessage);
    } finally {
      setIsLoadingHistory(false);
    }
  }, [
    isLoadingHistory,
    setIsLoadingHistory,
    setError,
    setMessages,
    setHasMore,
    setIsGenerating,
    setCurrentRequestId,
    appendContent,
    updateMessage,
  ]);

  /**
   * 加载更多历史消息（向上滚动）
   */
  const loadMore = useCallback(async () => {
    const oldestMessage = messages[0];
    if (isLoadingHistory || !hasMore || !oldestMessage) return;

    setIsLoadingHistory(true);

    try {
      // 获取最早消息的 sequence 作为游标
      const data = await getMessages(50, oldestMessage.sequence);
      prependMessages(data.messages);
      setHasMore(data.has_more);
    } catch (err) {
      const friendlyMessage = getErrorMessage((err as Error).message || '加载更多消息失败');
      setError(friendlyMessage);
    } finally {
      setIsLoadingHistory(false);
    }
  }, [isLoadingHistory, hasMore, messages, setIsLoadingHistory, prependMessages, setHasMore, setError]);

  /**
   * 发送消息
   *
   * 参考: spec.md US2场景10 - 发送失败时保留用户输入
   */
  const send = useCallback(async (content: string) => {
    if (isGenerating) return;

    // 清除错误和失败内容
    setError(null);
    setFailedContent(null);

    // 保存原始消息列表（用于失败时恢复）
    const originalMessages = [...messages];

    // 创建临时用户消息（乐观更新）
    const lastMessage = messages[messages.length - 1];
    const nextSequence = lastMessage ? lastMessage.sequence + 1 : 1;
    const tempUserMsg: Message = {
      message_id: Date.now(), // 临时ID
      message_uuid: `temp-${Date.now()}`,
      role: 'user',
      content,
      status: 1,
      sequence: nextSequence,
      created_time: new Date().toISOString(),
    };

    // 创建临时助手消息占位
    const tempAssistantMsg: Message = {
      message_id: Date.now() + 1,
      message_uuid: `temp-${Date.now() + 1}`,
      role: 'assistant',
      content: '',
      status: 2, // 生成中
      sequence: tempUserMsg.sequence + 1,
      created_time: new Date().toISOString(),
    };

    // 添加消息到列表
    addMessage(tempUserMsg);
    addMessage(tempAssistantMsg);
    setIsGenerating(true);

    // 创建 AbortController
    const controller = new AbortController();
    abortControllerRef.current = controller;

    // 记录实际消息ID（从服务端返回）
    let realMessageId: number | undefined;

    try {
      await sendMessage(
        content,
        {
          onChunk: (chunk) => {
            // 从首个 chunk 获取 request_id
            if (chunk.request_id && !currentRequestIdRef.current) {
              currentRequestIdRef.current = chunk.request_id;
              setCurrentRequestId(chunk.request_id);
            }
            if (chunk.message_id && !realMessageId) {
              realMessageId = chunk.message_id;
              // 更新临时消息为真实消息，包含 request_id
              updateMessage(tempAssistantMsg.message_id, {
                message_id: chunk.message_id,
                request_id: currentRequestIdRef.current,
              });
            }
            const targetId = realMessageId || tempAssistantMsg.message_id;
            appendContent(targetId, chunk.content);
          },
          onDone: (messageId) => {
            const targetId = messageId || realMessageId || tempAssistantMsg.message_id;
            updateMessage(targetId, { status: 1 as MessageStatus });
            setIsGenerating(false);
            setCurrentRequestId(null);
            currentRequestIdRef.current = null;
            abortControllerRef.current = null;
          },
          onError: (err) => {
            const friendlyMessage = getErrorMessage(err);
            setError(friendlyMessage);
            // 移除乐观更新的消息，恢复原始消息列表
            setMessages(originalMessages);
            // 保存失败的内容，用于恢复到输入框
            setFailedContent(content);
            setIsGenerating(false);
            setCurrentRequestId(null);
            currentRequestIdRef.current = null;
            abortControllerRef.current = null;
            toast.error(friendlyMessage);
          },
          onInterrupted: (messageId) => {
            const targetId = messageId || realMessageId || tempAssistantMsg.message_id;
            updateMessage(targetId, { status: 3 as MessageStatus });
            setIsGenerating(false);
            setCurrentRequestId(null);
            currentRequestIdRef.current = null;
            abortControllerRef.current = null;
            toast.info('响应已中断，如有需要请复制已显示内容');
          },
        },
        controller.signal
      );
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        const friendlyMessage = getErrorMessage((err as Error).message || '发送失败');
        setError(friendlyMessage);
        // 移除乐观更新的消息，恢复原始消息列表
        setMessages(originalMessages);
        // 保存失败的内容，用于恢复到输入框
        setFailedContent(content);
        toast.error(friendlyMessage);
      }
      setIsGenerating(false);
      setCurrentRequestId(null);
      currentRequestIdRef.current = null;
      abortControllerRef.current = null;
    }
  }, [
    isGenerating,
    messages,
    setError,
    setFailedContent,
    addMessage,
    setIsGenerating,
    updateMessage,
    appendContent,
    setCurrentRequestId,
    setMessages,
  ]);

  /**
   * 停止生成
   */
  const stop = useCallback(async () => {
    if (!isGenerating) return;

    // 取消 fetch 请求
    abortControllerRef.current?.abort();

    // 通知后端停止
    const requestId = currentRequestIdRef.current;
    if (requestId) {
      await stopGeneration(requestId);
    }

    setIsGenerating(false);
    setCurrentRequestId(null);
    currentRequestIdRef.current = null;
  }, [isGenerating, setIsGenerating, setCurrentRequestId]);

  /**
   * 继续生成（用于 status=3 中断消息）
   *
   * 参考: behavior-model.md#2.5 继续生成（B_CHAT_005）
   */
  const resume = useCallback(async (messageId: number) => {
    if (isGenerating) return;

    // 查找消息获取 request_id
    const targetMsg = messages.find((m) => m.message_id === messageId);
    if (!targetMsg || !targetMsg.request_id) {
      toast.error('无法继续生成：缺少请求ID');
      return;
    }

    setIsGenerating(true);
    setError(null);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    // 移除 [已中断] 标记，更新状态为生成中
    updateMessage(messageId, {
      content: targetMsg.content.replace('[已中断]', ''),
      status: 2 as MessageStatus,
    });

    try {
      await resumeGeneration(
        targetMsg.request_id,
        {
          onChunk: (chunk) => {
            appendContent(messageId, chunk.content);
          },
          onDone: () => {
            updateMessage(messageId, { status: 1 as MessageStatus });
            setIsGenerating(false);
            abortControllerRef.current = null;
          },
          onError: (err) => {
            const friendlyMessage = getErrorMessage(err);
            setError(friendlyMessage);
            updateMessage(messageId, { status: 0 as MessageStatus });
            setIsGenerating(false);
            abortControllerRef.current = null;
            toast.error(friendlyMessage);
          },
          onInterrupted: () => {
            updateMessage(messageId, { status: 3 as MessageStatus });
            setIsGenerating(false);
            abortControllerRef.current = null;
            toast.info('响应已中断，如有需要请复制已显示内容');
          },
        },
        controller.signal
      );
    } catch (err) {
      const friendlyMessage = getErrorMessage((err as Error).message || '恢复生成失败');
      setError(friendlyMessage);
      setIsGenerating(false);
      abortControllerRef.current = null;
    }
  }, [isGenerating, messages, setIsGenerating, setError, updateMessage, appendContent]);

  /**
   * 重新加载
   */
  const reload = useCallback(async () => {
    setMessages([]);
    setHasMore(true);
    await loadHistory();
  }, [setMessages, setHasMore, loadHistory]);

  /**
   * 清除失败内容
   */
  const clearFailedContent = useCallback(() => {
    setFailedContent(null);
  }, [setFailedContent]);

  // 组件挂载时加载历史消息
  useEffect(() => {
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 组件卸载时取消请求
  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  return {
    messages,
    isGenerating,
    isLoadingHistory,
    hasMore,
    error,
    failedContent,
    send,
    stop,
    resume,
    loadMore,
    reload,
    clearFailedContent,
  };
}
