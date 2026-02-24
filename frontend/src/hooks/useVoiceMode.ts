'use client';

/**
 * 语音模式核心状态机 Hook (T022)
 *
 * 整合 usePCMAudioCapture + useVoiceWebSocket + voiceStore，
 * 驱动完整的语音交互生命周期：
 *
 * 状态机:
 *   idle → configuring → listening → recording → processing → responding → idle
 *                                                        ↓ (cancel)
 *                                                     interrupted → idle
 *
 * - idle: 语音模式未开启
 * - configuring: WebSocket 连接中，等待 session.configured
 * - listening: 已配置，等待 VAD 检测到语音
 * - recording: VAD 检测到语音，正在录音
 * - processing: 录音结束，等待推理开始
 * - responding: AI 正在流式回复
 * - interrupted: 被打断
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { usePCMAudioCapture } from '@/hooks/usePCMAudioCapture';
import { useVoiceWebSocket } from '@/hooks/useVoiceWebSocket';
import { useVoiceStore } from '@/stores/voiceStore';
import type { VoiceSessionState } from '@/types/voice';

/** Hook 返回值接口 */
interface UseVoiceModeReturn {
  /** 语音模式是否开启 */
  isActive: boolean;
  /** 当前状态 */
  sessionState: VoiceSessionState;
  /** 是否正在录音 */
  isRecording: boolean;
  /** 当前音量级别 (0.0~1.0) */
  volumeLevel: number;
  /** 录音时长（秒） */
  duration: number;
  /** 当前 AI 回复内容（流式累积） */
  currentResponse: string;
  /** 当前转写文本 */
  currentTranscription: string;
  /** 当前 response_id */
  currentResponseId: string | null;
  /** 错误信息 */
  error: string | null;
  /** 开启语音模式 */
  enableVoiceMode: () => void;
  /** 关闭语音模式 */
  disableVoiceMode: () => void;
  /** 取消当前响应（打断） */
  cancelCurrentResponse: () => void;
  /** 手动开始录音（hold 模式） */
  manualStartRecording: () => void;
  /** 手动停止录音（hold 模式） */
  manualStopRecording: () => void;
}

/**
 * 语音模式核心状态机 Hook
 *
 * 整合 PCM 音频采集、WebSocket 通信和全局 voiceStore，
 * 提供统一的语音交互控制接口。
 *
 * @returns 语音模式状态与操作方法
 */
export function useVoiceMode(): UseVoiceModeReturn {
  // ─── 本地状态 ───
  const [sessionState, setSessionState] = useState<VoiceSessionState>('idle');
  const [volumeLevel, setVolumeLevel] = useState(0);
  const [currentResponse, setCurrentResponse] = useState('');
  const [currentTranscription, setCurrentTranscription] = useState('');
  const [currentResponseId, setCurrentResponseId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // ─── Refs：用于在 WebSocket 回调中访问最新值，避免闭包过期 ───
  const sessionStateRef = useRef<VoiceSessionState>('idle');
  const currentResponseRef = useRef('');
  const currentResponseIdRef = useRef<string | null>(null);

  // ─── voiceStore（使用 selector 获取稳定 action 引用，避免整个 store 订阅导致无限循环） ───
  const storeSetSessionState = useVoiceStore((s) => s.setSessionState);
  const storeSetError = useVoiceStore((s) => s.setError);
  const storeSetIsConnected = useVoiceStore((s) => s.setIsConnected);
  const storeSetIsRecording = useVoiceStore((s) => s.setIsRecording);
  const storeSetVoiceMode = useVoiceStore((s) => s.setVoiceMode);
  const storeSetCurrentTranscription = useVoiceStore((s) => s.setCurrentTranscription);
  const storeReset = useVoiceStore((s) => s.reset);
  const storeSettings = useVoiceStore((s) => s.settings);
  const recordingMode = useVoiceStore((s) => s.recordingMode);

  /**
   * 同步更新本地 state 和 ref
   *
   * 确保 WebSocket 回调中始终读到最新的 sessionState。
   */
  const updateSessionState = useCallback((newState: VoiceSessionState) => {
    sessionStateRef.current = newState;
    setSessionState(newState);
    storeSetSessionState(newState);
  }, [storeSetSessionState]);

  /**
   * 设置错误信息，同步到本地 state 和 voiceStore
   */
  const updateError = useCallback((msg: string | null) => {
    setError(msg);
    storeSetError(msg);
  }, [storeSetError]);

  // ─── WebSocket 事件回调 ───

  /** 会话配置完成：configuring → listening */
  const handleSessionConfigured = useCallback(() => {
    if (sessionStateRef.current === 'configuring') {
      updateSessionState('listening');
      updateError(null);
    }
  }, [updateSessionState, updateError]);

  /** VAD 检测到语音开始：listening → recording，启动 PCM 采集 */
  const handleVadSpeechStart = useCallback(() => {
    if (sessionStateRef.current === 'listening' || sessionStateRef.current === 'responding') {
      // 如果在 responding 状态收到 speech_start，说明用户打断了 AI
      if (sessionStateRef.current === 'responding') {
        // 先取消当前响应
        if (currentResponseIdRef.current) {
          wsCancelResponse(currentResponseIdRef.current);
        }
      }
      updateSessionState('recording');
      storeSetIsRecording(true);
      startCapture();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [updateSessionState, storeSetIsRecording]);

  /** VAD 检测到语音结束：recording → processing，停止 PCM 采集 */
  const handleVadSpeechEnd = useCallback(() => {
    if (sessionStateRef.current === 'recording') {
      updateSessionState('processing');
      storeSetIsRecording(false);
      stopCapture();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [updateSessionState, storeSetIsRecording]);

  /** 响应开始：processing → responding */
  const handleResponseStart = useCallback((data: Record<string, unknown>) => {
    if (sessionStateRef.current === 'processing' || sessionStateRef.current === 'listening') {
      const responseId = (data.response_id as string) || null;
      currentResponseIdRef.current = responseId;
      setCurrentResponseId(responseId);
      currentResponseRef.current = '';
      setCurrentResponse('');
      updateSessionState('responding');
    }
  }, [updateSessionState]);

  /** 响应增量数据：流式追加 AI 回复内容 */
  const handleResponseDelta = useCallback((data: Record<string, unknown>) => {
    if (sessionStateRef.current !== 'responding') {
      return;
    }

    // 从 data.delta.content 提取文本增量
    const delta = data.delta as Record<string, unknown> | undefined;
    const content = delta?.content as string | null | undefined;

    if (content) {
      currentResponseRef.current += content;
      setCurrentResponse(currentResponseRef.current);
    }
  }, []);

  /** 响应结束：responding → listening（准备接收下一轮语音） */
  const handleResponseEnd = useCallback(() => {
    if (sessionStateRef.current === 'responding') {
      // 响应完成，回到 listening 等待下一轮语音输入
      updateSessionState('listening');
      // 重置响应状态，为下一轮做准备
      currentResponseIdRef.current = null;
      setCurrentResponseId(null);
    }
  }, [updateSessionState]);

  /** 转录完成：更新用户消息转写文本 */
  const handleTranscriptionComplete = useCallback((data: Record<string, unknown>) => {
    const text = (data.text as string) || '';
    setCurrentTranscription(text);
    storeSetCurrentTranscription(text);
  }, [storeSetCurrentTranscription]);

  /** 转录失败：记录错误但不中断会话 */
  const handleTranscriptionFailed = useCallback((data: Record<string, unknown>) => {
    const errorMsg = (data.error as string) || '转录失败';
    console.warn('[useVoiceMode] 转录失败:', errorMsg);
  }, []);

  /** 消息已保存：通知消息已持久化到数据库 */
  const handleMessageSaved = useCallback((_data: Record<string, unknown>) => {
    // 消息已持久化，可触发消息列表刷新等操作
    // 具体刷新逻辑由上层组件通过 chatStore 完成
  }, []);

  /** 会话关闭：重置状态 */
  const handleSessionClosed = useCallback(() => {
    updateSessionState('idle');
    storeSetVoiceMode(false);
    storeSetIsRecording(false);
    storeSetIsConnected(false);
  }, [updateSessionState, storeSetVoiceMode, storeSetIsRecording, storeSetIsConnected]);

  /** 错误事件处理 */
  const handleError = useCallback((data: Record<string, unknown>) => {
    const message = (data.message as string) || '语音服务异常';
    const recoverable = (data.recoverable as boolean) ?? false;

    if (recoverable) {
      // 可恢复错误：显示错误但回到 listening 继续等待
      updateError(message);
      if (sessionStateRef.current === 'recording') {
        storeSetIsRecording(false);
        stopCapture();
      }
      updateSessionState('listening');
    } else {
      // 不可恢复错误：进入 error 状态，需要用户重新开启语音模式
      updateError(message);
      updateSessionState('error');
      storeSetIsRecording(false);
      storeSetVoiceMode(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [updateSessionState, updateError, storeSetIsRecording, storeSetVoiceMode]);

  // ─── WebSocket Hook ───
  const {
    isConnected,
    connect,
    disconnect,
    configure,
    sendAudio,
    cancelResponse: wsCancelResponse,
    closeSession,
    error: wsError,
  } = useVoiceWebSocket({
    onSessionConfigured: handleSessionConfigured,
    onSessionClosed: handleSessionClosed,
    onVadSpeechStart: handleVadSpeechStart,
    onVadSpeechEnd: handleVadSpeechEnd,
    onResponseStart: handleResponseStart,
    onResponseDelta: handleResponseDelta,
    onResponseEnd: handleResponseEnd,
    onTranscriptionComplete: handleTranscriptionComplete,
    onTranscriptionFailed: handleTranscriptionFailed,
    onMessageSaved: handleMessageSaved,
    onError: handleError,
  });

  // ─── PCM 采集 Hook ───

  /** PCM 帧回调：将音频数据通过 WebSocket 发送到服务端 */
  const handleAudioData = useCallback((pcmData: ArrayBuffer) => {
    sendAudio(pcmData);
  }, [sendAudio]);

  /** 音量变化回调：更新本地音量状态 */
  const handleVolumeLevel = useCallback((level: number) => {
    setVolumeLevel(level);
  }, []);

  const {
    duration,
    error: captureError,
    startCapture,
    stopCapture,
  } = usePCMAudioCapture({
    onAudioData: handleAudioData,
    onVolumeLevel: handleVolumeLevel,
    maxDuration: 30,
  });

  // ─── 同步 WebSocket 连接状态到 voiceStore ───
  useEffect(() => {
    storeSetIsConnected(isConnected);
  }, [isConnected, storeSetIsConnected]);

  // ─── 同步 WebSocket 错误 ───
  useEffect(() => {
    if (wsError) {
      updateError(wsError);
    }
  }, [wsError, updateError]);

  // ─── 同步采集错误 ───
  useEffect(() => {
    if (captureError) {
      updateError(captureError);
    }
  }, [captureError, updateError]);

  // ─── 录音模式支持（hold / toggle） ───
  // hold 模式下的事件由上层 UI 组件通过 startCapture/stopCapture 触发
  // toggle 模式由 VAD 事件自动驱动
  // 此处 recordingMode 的值保存在 ref 中，供 handleVadSpeechStart 等回调使用
  const recordingModeRef = useRef(recordingMode);
  useEffect(() => {
    recordingModeRef.current = recordingMode;
  }, [recordingMode]);

  // ─── 公开方法 ───

  /**
   * 开启语音模式
   *
   * 流程: connect WebSocket → 发送 session.configure → 等待 session.configured
   */
  const enableVoiceMode = useCallback(() => {
    if (sessionStateRef.current !== 'idle' && sessionStateRef.current !== 'error') {
      return;
    }

    // 重置状态
    updateError(null);
    setCurrentResponse('');
    currentResponseRef.current = '';
    setCurrentTranscription('');
    setCurrentResponseId(null);
    currentResponseIdRef.current = null;
    setVolumeLevel(0);

    // 更新全局 store
    storeSetVoiceMode(true);
    storeSetCurrentTranscription('');
    storeSetError(null);

    // 进入 configuring 状态
    updateSessionState('configuring');

    // 连接 WebSocket
    connect();
  }, [connect, updateSessionState, updateError, storeSetVoiceMode, storeSetCurrentTranscription, storeSetError]);

  /**
   * WebSocket 连接建立后自动发送配置
   *
   * 当 isConnected 变为 true 且处于 configuring 状态时，发送 session.configure。
   */
  useEffect(() => {
    if (isConnected && sessionStateRef.current === 'configuring') {
      configure({
        mode: 'voice_chat',
        vad_threshold: storeSettings?.vadSensitivity ?? 0.5,
        recording_mode: recordingModeRef.current,
      });
    }
  }, [isConnected, configure, storeSettings]);

  /**
   * 关闭语音模式
   *
   * 流程: closeSession → disconnect → 停止采集 → reset store
   */
  const disableVoiceMode = useCallback(() => {
    // 发送关闭会话指令
    closeSession();
    // 断开 WebSocket
    disconnect();
    // 停止音频采集
    stopCapture();

    // 重置本地状态
    updateSessionState('idle');
    setCurrentResponse('');
    currentResponseRef.current = '';
    setCurrentTranscription('');
    setCurrentResponseId(null);
    currentResponseIdRef.current = null;
    setVolumeLevel(0);
    updateError(null);

    // 重置 voiceStore
    storeReset();
  }, [closeSession, disconnect, stopCapture, updateSessionState, updateError, storeReset]);

  /**
   * 取消当前响应（打断 AI 回复）
   *
   * 向服务端发送 response.cancel，进入 interrupted 状态后自动恢复到 listening。
   */
  const cancelCurrentResponse = useCallback(() => {
    if (sessionStateRef.current !== 'responding') {
      return;
    }

    if (currentResponseIdRef.current) {
      wsCancelResponse(currentResponseIdRef.current);
    }

    // 进入 interrupted 状态
    updateSessionState('interrupted');

    // 短暂停留后恢复到 listening
    setTimeout(() => {
      if (sessionStateRef.current === 'interrupted') {
        updateSessionState('listening');
        currentResponseIdRef.current = null;
        setCurrentResponseId(null);
      }
    }, 300);
  }, [wsCancelResponse, updateSessionState]);

  /**
   * 手动开始录音（hold 模式）
   *
   * 在 listening 状态下手动启动 PCM 采集和状态切换。
   */
  const manualStartRecording = useCallback(() => {
    if (sessionStateRef.current !== 'listening') {
      return;
    }
    updateSessionState('recording');
    storeSetIsRecording(true);
    startCapture();
  }, [updateSessionState, storeSetIsRecording, startCapture]);

  /**
   * 手动停止录音（hold 模式）
   *
   * 停止 PCM 采集，切换到 processing 状态。
   */
  const manualStopRecording = useCallback(() => {
    if (sessionStateRef.current !== 'recording') {
      return;
    }
    updateSessionState('processing');
    storeSetIsRecording(false);
    stopCapture();
  }, [updateSessionState, storeSetIsRecording, stopCapture]);

  // ─── 组件卸载清理 ───
  useEffect(() => {
    return () => {
      if (sessionStateRef.current !== 'idle') {
        closeSession();
        disconnect();
        stopCapture();
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ─── 计算派生状态 ───
  const isActive = sessionState !== 'idle' && sessionState !== 'error';
  const isRecording = sessionState === 'recording';

  // 同步 isRecording 到 voiceStore
  useEffect(() => {
    storeSetIsRecording(isRecording);
  }, [isRecording, storeSetIsRecording]);

  return {
    isActive,
    sessionState,
    isRecording,
    volumeLevel,
    duration,
    currentResponse,
    currentTranscription,
    currentResponseId,
    error,
    enableVoiceMode,
    disableVoiceMode,
    cancelCurrentResponse,
    manualStartRecording,
    manualStopRecording,
  };
}
