/**
 * 语音模式容器组件 (T055b)
 *
 * 封装 useVoiceMode hook 和 VoiceModePanel，
 * 整体动态导入以减小聊天页面首次加载体积。
 *
 * 通过 voiceStore 与 ChatPage 共享语音模式开关状态：
 * - ChatPage 设置 voiceStore.voiceMode = true 表示用户点击了语音按钮
 * - 本组件监听该变化，自动调用 enableVoiceMode() 启动 WebSocket 连接
 * - 关闭时调用 disableVoiceMode()，内部会重置 voiceStore.voiceMode = false
 */
'use client';

import { useCallback, useEffect, useRef } from 'react';

import { useVoiceMode } from '@/hooks/useVoiceMode';
import { useVoiceStore } from '@/stores/voiceStore';
import { VoiceModePanel } from './VoiceModePanel';

export function VoiceModeContainer() {
  const voiceMode = useVoiceStore((s) => s.voiceMode);
  const recordingMode = useVoiceStore((s) => s.recordingMode);

  const {
    isActive,
    sessionState,
    isRecording,
    volumeLevel,
    duration,
    currentResponse,
    currentTranscription,
    error,
    enableVoiceMode,
    disableVoiceMode,
    cancelCurrentResponse,
    manualStartRecording,
    manualStopRecording,
  } = useVoiceMode();

  // 跟踪上一次的 voiceMode 值，避免初始化时误触发
  const prevVoiceModeRef = useRef(voiceMode);

  // 监听 voiceStore.voiceMode 变化，同步到 useVoiceMode
  useEffect(() => {
    const prev = prevVoiceModeRef.current;
    prevVoiceModeRef.current = voiceMode;

    if (voiceMode && !prev && !isActive) {
      // voiceStore.voiceMode 从 false → true，启动语音模式
      enableVoiceMode();
    } else if (!voiceMode && prev && isActive) {
      // voiceStore.voiceMode 从 true → false，关闭语音模式
      disableVoiceMode();
    }
  }, [voiceMode, isActive, enableVoiceMode, disableVoiceMode]);

  const handleClose = useCallback(() => {
    disableVoiceMode();
  }, [disableVoiceMode]);

  if (!voiceMode) {
    return null;
  }

  return (
    <VoiceModePanel
      sessionState={sessionState}
      isRecording={isRecording}
      volumeLevel={volumeLevel}
      duration={duration}
      currentResponse={currentResponse}
      currentTranscription={currentTranscription}
      error={error}
      recordingMode={recordingMode}
      onClose={handleClose}
      onStartRecording={manualStartRecording}
      onStopRecording={manualStopRecording}
      onCancelResponse={cancelCurrentResponse}
    />
  );
}
