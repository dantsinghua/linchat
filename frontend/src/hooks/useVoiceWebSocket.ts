/**
 * 语音 WebSocket 连接管理 Hook (T021)
 *
 * 管理语音交互的 WebSocket 生命周期：
 * - 建立/断开 WebSocket 连接
 * - 发送会话配置（session.configure）
 * - 发送 PCM 音频二进制帧
 * - 接收并分类分发 JSON 事件
 * - 心跳保活（30 秒间隔）
 * - 发送取消响应（response.cancel）和关闭会话（session.close）
 * - 断线自动重连一次 (T053)
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/** 语音事件回调函数类型 */
type VoiceEventHandler = (data: Record<string, unknown>) => void;

/** Hook 配置选项：各类事件的回调函数 */
interface UseVoiceWebSocketOptions {
  /** 会话配置完成 */
  onSessionConfigured?: VoiceEventHandler;
  /** 会话关闭 */
  onSessionClosed?: VoiceEventHandler;
  /** VAD 检测到语音开始 */
  onVadSpeechStart?: VoiceEventHandler;
  /** VAD 检测到语音结束 */
  onVadSpeechEnd?: VoiceEventHandler;
  /** 说话人识别完成 */
  onSpeakerIdentified?: VoiceEventHandler;
  /** 响应开始 */
  onResponseStart?: VoiceEventHandler;
  /** 响应增量数据（流式） */
  onResponseDelta?: VoiceEventHandler;
  /** 响应结束 */
  onResponseEnd?: VoiceEventHandler;
  /** 转录完成 */
  onTranscriptionComplete?: VoiceEventHandler;
  /** 转录失败 */
  onTranscriptionFailed?: VoiceEventHandler;
  /** 消息已保存 */
  onMessageSaved?: VoiceEventHandler;
  /** 错误事件 */
  onError?: VoiceEventHandler;
  /** T052: 会话冲突通知 */
  onSessionConflict?: VoiceEventHandler;
  /** T052: 会话重连成功 */
  onSessionReconnected?: VoiceEventHandler;
  /** T052: 会话重连失败 */
  onSessionReconnectFailed?: VoiceEventHandler;
  /** T052: 响应决策结果（continuous_listen 模式） */
  onDecisionResult?: VoiceEventHandler;
}

/** Hook 返回值 */
interface UseVoiceWebSocketReturn {
  /** WebSocket 是否已连接 */
  isConnected: boolean;
  /** 建立 WebSocket 连接 */
  connect: () => void;
  /** 断开 WebSocket 连接 */
  disconnect: () => void;
  /** 发送会话配置 */
  configure: (config: Record<string, unknown>) => void;
  /** 发送 PCM 音频二进制帧 */
  sendAudio: (pcmData: ArrayBuffer) => void;
  /** 取消指定响应 */
  cancelResponse: (responseId: string) => void;
  /** 关闭会话 */
  closeSession: () => void;
  /** T052: 发送会话重连请求 */
  sendReconnect: (config: Record<string, unknown>) => void;
  /** 错误信息 */
  error: string | null;
}

/** 心跳检查间隔（毫秒） */
const HEARTBEAT_INTERVAL_MS = 30_000;

/** 断线重连延迟（毫秒） */
const RECONNECT_DELAY_MS = 2_000;

/** WebSocket 基础路径 */
const WS_BASE_URL = process.env.NEXT_PUBLIC_WS_BASE_URL || '/linchat/ws/voice/';

/**
 * 事件类型到回调映射表
 *
 * 将服务端下发的 JSON 事件 type 字段映射到对应的回调选项键名。
 */
const EVENT_HANDLER_MAP: Record<string, keyof UseVoiceWebSocketOptions> = {
  'session.configured': 'onSessionConfigured',
  'session.closed': 'onSessionClosed',
  'vad.speech_start': 'onVadSpeechStart',
  'vad.speech_end': 'onVadSpeechEnd',
  'speaker.identified': 'onSpeakerIdentified',
  'response.start': 'onResponseStart',
  'response.delta': 'onResponseDelta',
  'response.end': 'onResponseEnd',
  'transcription.complete': 'onTranscriptionComplete',
  'transcription.failed': 'onTranscriptionFailed',
  'message.saved': 'onMessageSaved',
  'error': 'onError',
  'session.conflict': 'onSessionConflict',
  'session.reconnected': 'onSessionReconnected',
  'session.reconnect_failed': 'onSessionReconnectFailed',
  'decision.result': 'onDecisionResult',
};

/**
 * 语音 WebSocket 连接管理 Hook
 *
 * @param options - 各类事件的回调函数
 * @returns WebSocket 连接状态与操作方法
 */
export function useVoiceWebSocket(
  options: UseVoiceWebSocketOptions = {},
): UseVoiceWebSocketReturn {
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const heartbeatTimerRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectTimerRef = useRef<NodeJS.Timeout | null>(null);
  /** 是否已尝试过一次自动重连 */
  const hasReconnectedRef = useRef(false);
  /** 是否为用户主动断开（主动断开时不触发自动重连） */
  const intentionalDisconnectRef = useRef(false);
  const optionsRef = useRef(options);

  // 始终保持最新的回调引用，避免 stale closure
  optionsRef.current = options;

  /** 清理心跳定时器 */
  const clearHeartbeat = useCallback(() => {
    if (heartbeatTimerRef.current) {
      clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = null;
    }
  }, []);

  /** 清理重连定时器 */
  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  /** 启动心跳保活定时器 */
  const startHeartbeat = useCallback(() => {
    clearHeartbeat();
    heartbeatTimerRef.current = setInterval(() => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        // 连接已关闭，停止心跳
        clearHeartbeat();
        setIsConnected(false);
      }
      // readyState === OPEN 说明连接活跃，
      // 原生 WebSocket Ping/Pong 由浏览器自动处理
    }, HEARTBEAT_INTERVAL_MS);
  }, [clearHeartbeat]);

  /**
   * 构建 WebSocket 完整 URL
   *
   * 根据当前页面协议自动选择 ws:// 或 wss://
   */
  const buildWsUrl = useCallback((): string => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    return `${protocol}//${host}${WS_BASE_URL}`;
  }, []);

  /** 断开 WebSocket 连接 */
  const disconnect = useCallback(() => {
    intentionalDisconnectRef.current = true;
    clearHeartbeat();
    clearReconnectTimer();
    const ws = wsRef.current;
    if (ws) {
      // 移除事件监听，避免 onclose 中触发额外逻辑
      ws.onopen = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
      wsRef.current = null;
    }
    setIsConnected(false);
  }, [clearHeartbeat, clearReconnectTimer]);

  /** 建立 WebSocket 连接 */
  const connect = useCallback(() => {
    // 如果已有连接，先断开（内部调用，暂时标记为非主动断开）
    if (wsRef.current) {
      intentionalDisconnectRef.current = true;
      clearHeartbeat();
      clearReconnectTimer();
      const oldWs = wsRef.current;
      oldWs.onopen = null;
      oldWs.onmessage = null;
      oldWs.onerror = null;
      oldWs.onclose = null;
      if (oldWs.readyState === WebSocket.OPEN || oldWs.readyState === WebSocket.CONNECTING) {
        oldWs.close();
      }
      wsRef.current = null;
    }

    // 重置标记：新连接不是主动断开
    intentionalDisconnectRef.current = false;
    hasReconnectedRef.current = false;
    setError(null);

    const url = buildWsUrl();
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      setError(null);
      // 连接成功后重置重连标记，允许下一次断线时再次重连
      hasReconnectedRef.current = false;
      startHeartbeat();
    };

    ws.onmessage = (event: MessageEvent) => {
      // 下行不发 binary，忽略二进制帧
      if (event.data instanceof ArrayBuffer) {
        return;
      }

      // 文本帧：JSON.parse 后按 type 分发回调
      try {
        const parsed = JSON.parse(event.data as string) as Record<string, unknown>;
        const eventType = parsed.type as string | undefined;

        if (!eventType) {
          return;
        }

        const handlerKey = EVENT_HANDLER_MAP[eventType];
        if (handlerKey) {
          const handler = optionsRef.current[handlerKey];
          if (handler) {
            // 传递 data 内层数据给回调，与后端 {type, data} 格式对齐
            const eventData = (parsed.data as Record<string, unknown>) || {};
            handler(eventData);
          }
        }
      } catch {
        // JSON 解析失败，忽略该帧
      }
    };

    ws.onerror = () => {
      setError('WebSocket 连接异常');
    };

    ws.onclose = (event: CloseEvent) => {
      clearHeartbeat();
      setIsConnected(false);
      wsRef.current = null;

      if (!event.wasClean) {
        setError(`WebSocket 连接断开 (code: ${event.code})`);
      }

      // 自动重连逻辑：非主动断开 + 尚未重连过 → 延迟重连一次
      if (!intentionalDisconnectRef.current && !hasReconnectedRef.current) {
        hasReconnectedRef.current = true;
        setError('连接断开，正在尝试重连...');
        reconnectTimerRef.current = setTimeout(() => {
          reconnectTimerRef.current = null;
          // 重连时保持 hasReconnectedRef 为 true，防止再次触发
          intentionalDisconnectRef.current = false;

          const reconnectUrl = buildWsUrl();
          const reconnectWs = new WebSocket(reconnectUrl);
          reconnectWs.binaryType = 'arraybuffer';
          wsRef.current = reconnectWs;

          reconnectWs.onopen = ws.onopen;
          reconnectWs.onmessage = ws.onmessage;
          reconnectWs.onerror = ws.onerror;
          reconnectWs.onclose = ws.onclose;
        }, RECONNECT_DELAY_MS);
      }
    };
  }, [buildWsUrl, startHeartbeat, clearHeartbeat, clearReconnectTimer]);

  /**
   * 发送会话配置
   *
   * 向服务端发送 session.configure 文本帧。
   */
  const configure = useCallback((config: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setError('WebSocket 未连接，无法发送配置');
      return;
    }

    const payload = JSON.stringify({
      type: 'session.configure',
      data: config,
    });
    ws.send(payload);
  }, []);

  /**
   * 发送 PCM 音频二进制帧
   *
   * 直接将 ArrayBuffer 作为 Binary 帧发送。
   */
  const sendAudio = useCallback((pcmData: ArrayBuffer) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return;
    }

    ws.send(pcmData);
  }, []);

  /**
   * 取消指定响应
   *
   * 向服务端发送 response.cancel 文本帧。
   */
  const cancelResponse = useCallback((responseId: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setError('WebSocket 未连接，无法取消响应');
      return;
    }

    const payload = JSON.stringify({
      type: 'response.cancel',
      data: { response_id: responseId },
    });
    ws.send(payload);
  }, []);

  /**
   * T052: 发送会话重连请求
   *
   * 断线重连后发送 session.reconnect，恢复已有会话。
   */
  const sendReconnect = useCallback((config: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setError('WebSocket 未连接，无法重连会话');
      return;
    }

    const payload = JSON.stringify({
      type: 'session.reconnect',
      data: config,
    });
    ws.send(payload);
  }, []);

  /**
   * 关闭会话
   *
   * 向服务端发送 session.close 文本帧。
   */
  const closeSession = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return;
    }

    const payload = JSON.stringify({
      type: 'session.close',
    });
    ws.send(payload);
  }, []);

  // 组件卸载时自动断开连接并清理重连定时器
  useEffect(() => {
    return () => {
      // eslint-disable-next-line react-hooks/exhaustive-deps
      const ws = wsRef.current;
      if (heartbeatTimerRef.current) {
        clearInterval(heartbeatTimerRef.current);
        heartbeatTimerRef.current = null;
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close();
        }
        wsRef.current = null;
      }
    };
  }, []);

  return {
    isConnected,
    connect,
    disconnect,
    configure,
    sendAudio,
    cancelResponse,
    closeSession,
    sendReconnect,
    error,
  };
}
