/**
 * 语音状态管理
 *
 * 使用 Zustand 管理语音交互状态
 * 参考: specs/009-voice-interaction/spec.md
 */
import { create } from 'zustand';

import type { VoiceSessionState, RecordingMode, VoiceSettings } from '@/types/voice';

interface VoiceState {
  // 语音模式开关
  voiceMode: boolean;
  // 会话状态
  sessionState: VoiceSessionState;
  // 是否正在录音
  isRecording: boolean;
  // 当前转写文本
  currentTranscription: string;
  // 录音模式
  recordingMode: RecordingMode;
  // 语音设置（从后端加载）
  settings: VoiceSettings | null;
  // 错误信息
  error: string | null;
  // WebSocket 连接状态
  isConnected: boolean;
  // 当前识别的说话人
  currentSpeakerId: string | null;
  // 用户是否已注册声纹
  hasSpeakerProfile: boolean;

  // Actions
  setVoiceMode: (enabled: boolean) => void;
  setSessionState: (state: VoiceSessionState) => void;
  setIsRecording: (recording: boolean) => void;
  setCurrentTranscription: (text: string) => void;
  setRecordingMode: (mode: RecordingMode) => void;
  setSettings: (settings: VoiceSettings) => void;
  setError: (error: string | null) => void;
  setIsConnected: (connected: boolean) => void;
  setCurrentSpeakerId: (speakerId: string | null) => void;
  setHasSpeakerProfile: (has: boolean) => void;
  reset: () => void;
}

const initialState = {
  voiceMode: false,
  sessionState: 'idle' as VoiceSessionState,
  isRecording: false,
  currentTranscription: '',
  recordingMode: 'toggle' as RecordingMode,
  settings: null as VoiceSettings | null,
  error: null as string | null,
  isConnected: false,
  currentSpeakerId: null as string | null,
  hasSpeakerProfile: false,
};

export const useVoiceStore = create<VoiceState>((set) => ({
  ...initialState,

  setVoiceMode: (enabled) => set({ voiceMode: enabled }),
  setSessionState: (state) => set({ sessionState: state }),
  setIsRecording: (recording) => set({ isRecording: recording }),
  setCurrentTranscription: (text) => set({ currentTranscription: text }),
  setRecordingMode: (mode) => set({ recordingMode: mode }),
  setSettings: (settings) => set({ settings }),
  setError: (error) => set({ error }),
  setIsConnected: (connected) => set({ isConnected: connected }),
  setCurrentSpeakerId: (speakerId) => set({ currentSpeakerId: speakerId }),
  setHasSpeakerProfile: (has) => set({ hasSpeakerProfile: has }),
  reset: () => set(initialState),
}));
