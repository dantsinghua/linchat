/**
 * 语音模式 ambient 配置测试 (T054)
 *
 * 015-family-multiuser:
 * - 验证 configure 调用时 mode 固定为 "ambient"
 * - T051 统一语音模式为 ambient，不再区分 voice_chat/voice_chat_enriched
 */
import { renderHook, act } from '@testing-library/react';

// ========== Mock 依赖 ==========

const mockConnect = jest.fn();
const mockDisconnect = jest.fn();
const mockConfigure = jest.fn();
const mockSendAudio = jest.fn();
const mockWsCancelResponse = jest.fn();
const mockCloseSession = jest.fn();

// eslint-disable-next-line @typescript-eslint/no-unused-vars
let wsCallbacks: Record<string, ((data: Record<string, unknown>) => void) | undefined> = {};
let wsIsConnected = false;

jest.mock('@/hooks/useVoiceWebSocket', () => ({
  useVoiceWebSocket: (options: Record<string, unknown>) => {
    wsCallbacks = {
      onSessionConfigured: options.onSessionConfigured as never,
      onSessionClosed: options.onSessionClosed as never,
      onVadSpeechStart: options.onVadSpeechStart as never,
      onVadSpeechEnd: options.onVadSpeechEnd as never,
      onResponseStart: options.onResponseStart as never,
      onResponseDelta: options.onResponseDelta as never,
      onResponseEnd: options.onResponseEnd as never,
      onTranscriptionComplete: options.onTranscriptionComplete as never,
      onTranscriptionFailed: options.onTranscriptionFailed as never,
      onMessageSaved: options.onMessageSaved as never,
      onError: options.onError as never,
    };

    return {
      isConnected: wsIsConnected,
      connect: mockConnect,
      disconnect: mockDisconnect,
      configure: mockConfigure,
      sendAudio: mockSendAudio,
      cancelResponse: mockWsCancelResponse,
      closeSession: mockCloseSession,
      error: null,
    };
  },
}));

jest.mock('@/hooks/usePCMAudioCapture', () => ({
  usePCMAudioCapture: () => ({
    isCapturing: false,
    duration: 0,
    error: null,
    startCapture: jest.fn(),
    stopCapture: jest.fn(),
  }),
}));

// voiceStore mock — 需要支持 getState() 静态方法
const mockVoiceStoreState = {
  voiceMode: false,
  sessionState: 'idle' as const,
  isRecording: false,
  currentTranscription: '',
  recordingMode: 'toggle' as const,
  settings: { vadSensitivity: 0.5 } as { vadSensitivity: number } | null,
  error: null as string | null,
  isConnected: false,
  currentSpeakerId: null,
  hasSpeakerProfile: false,
  setVoiceMode: jest.fn(),
  setSessionState: jest.fn(),
  setIsRecording: jest.fn(),
  setCurrentTranscription: jest.fn(),
  setRecordingMode: jest.fn(),
  setSettings: jest.fn(),
  setError: jest.fn(),
  setIsConnected: jest.fn(),
  setCurrentSpeakerId: jest.fn(),
  setHasSpeakerProfile: jest.fn(),
  reset: jest.fn(),
};

const useVoiceStoreMock = (selector?: (state: typeof mockVoiceStoreState) => unknown) => {
  if (selector) {
    return selector(mockVoiceStoreState);
  }
  return mockVoiceStoreState;
};
// 静态 getState 方法，useVoiceMode.ts 中 `useVoiceStore.getState()` 需要此方法
useVoiceStoreMock.getState = () => mockVoiceStoreState;

jest.mock('@/stores/voiceStore', () => ({
  useVoiceStore: useVoiceStoreMock,
}));

jest.mock('@/services/voiceApi', () => ({
  getSpeakerProfile: jest.fn().mockResolvedValue({ data: null }),
}));

// 必须在 mock 之后导入
import { useVoiceMode } from '@/hooks/useVoiceMode';

// ========== 测试用例 ==========

beforeEach(() => {
  jest.clearAllMocks();
  wsIsConnected = false;
  wsCallbacks = {};
  mockVoiceStoreState.settings = { vadSensitivity: 0.5 };
  mockVoiceStoreState.recordingMode = 'toggle';
  mockVoiceStoreState.hasSpeakerProfile = false;
});

describe('useVoiceMode ambient 配置 (T054)', () => {
  it('configure 调用时 mode 应为 "ambient"', async () => {
    const { result, rerender } = renderHook(() => useVoiceMode());

    // 启用语音模式
    await act(async () => {
      result.current.enableVoiceMode();
    });

    expect(result.current.sessionState).toBe('configuring');

    // 模拟 WebSocket 连接成功
    wsIsConnected = true;
    rerender();

    // 验证 configure 被调用，mode 为 'ambient'
    expect(mockConfigure).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: 'ambient',
      })
    );
  });

  it('无论是否有声纹档案，mode 都应为 "ambient"', async () => {
    // 模拟用户有声纹档案
    mockVoiceStoreState.hasSpeakerProfile = true;

    const { result, rerender } = renderHook(() => useVoiceMode());

    await act(async () => {
      result.current.enableVoiceMode();
    });

    wsIsConnected = true;
    rerender();

    expect(mockConfigure).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: 'ambient',
      })
    );

    // 确保不包含 voice_chat 或 voice_chat_enriched
    const configArg = mockConfigure.mock.calls[0][0];
    expect(configArg.mode).not.toBe('voice_chat');
    expect(configArg.mode).not.toBe('voice_chat_enriched');
  });

  it('configure 应包含完整参数', async () => {
    mockVoiceStoreState.settings = { vadSensitivity: 0.7 };
    mockVoiceStoreState.recordingMode = 'hold';

    const { result, rerender } = renderHook(() => useVoiceMode());

    await act(async () => {
      result.current.enableVoiceMode();
    });

    wsIsConnected = true;
    rerender();

    expect(mockConfigure).toHaveBeenCalledWith({
      mode: 'ambient',
      vad_threshold: 0.7,
      speaker_identify: false,
      recording_mode: 'hold',
    });
  });
});
