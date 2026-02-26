/**
 * useVoiceMode Hook 单元测试
 *
 * 测试内容:
 * - 状态机流转: idle → configuring → listening → recording → processing → responding → listening
 * - enableVoiceMode / disableVoiceMode / cancelCurrentResponse
 * - WebSocket 事件驱动状态变更
 * - voiceStore 同步
 */
import { renderHook, act } from '@testing-library/react';

// ========== Mock 依赖 ==========

// ─── Mock useVoiceWebSocket ───
const mockConnect = jest.fn();
const mockDisconnect = jest.fn();
const mockConfigure = jest.fn();
const mockSendAudio = jest.fn();
const mockWsCancelResponse = jest.fn();
const mockCloseSession = jest.fn();
const mockSendReconnect = jest.fn();

/** 保存各事件回调的引用，测试中可通过此对象触发回调 */
let wsCallbacks: Record<string, ((data: Record<string, unknown>) => void) | undefined> = {};
let wsIsConnected = false;
let wsError: string | null = null;

jest.mock('@/hooks/useVoiceWebSocket', () => ({
  useVoiceWebSocket: (options: Record<string, unknown>) => {
    // 保存回调引用，供测试触发
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
      sendReconnect: mockSendReconnect,
      error: wsError,
    };
  },
}));

// ─── Mock usePCMAudioCapture ───
const mockStartCapture = jest.fn();
const mockStopCapture = jest.fn();
let captureIsCapturing = false;
let captureDuration = 0;
let captureError: string | null = null;

jest.mock('@/hooks/usePCMAudioCapture', () => ({
  usePCMAudioCapture: () => {
    return {
      isCapturing: captureIsCapturing,
      duration: captureDuration,
      error: captureError,
      startCapture: mockStartCapture,
      stopCapture: mockStopCapture,
    };
  },
}));

// ─── Mock voiceStore ───
const mockSetVoiceMode = jest.fn();
const mockSetSessionState = jest.fn();
const mockSetIsRecording = jest.fn();
const mockSetCurrentTranscription = jest.fn();
const mockSetError = jest.fn();
const mockSetIsConnected = jest.fn();
const mockReset = jest.fn();

const mockVoiceStoreState = {
  voiceMode: false,
  sessionState: 'idle' as const,
  isRecording: false,
  currentTranscription: '',
  recordingMode: 'toggle' as const,
  settings: null as { vadSensitivity: number } | null,
  error: null as string | null,
  isConnected: false,
  currentSpeakerId: null,
  setVoiceMode: mockSetVoiceMode,
  setSessionState: mockSetSessionState,
  setIsRecording: mockSetIsRecording,
  setCurrentTranscription: mockSetCurrentTranscription,
  setRecordingMode: jest.fn(),
  setSettings: jest.fn(),
  setError: mockSetError,
  setIsConnected: mockSetIsConnected,
  setCurrentSpeakerId: jest.fn(),
  reset: mockReset,
};

jest.mock('@/stores/voiceStore', () => ({
  useVoiceStore: (selector?: (state: typeof mockVoiceStoreState) => unknown) => {
    if (selector) {
      return selector(mockVoiceStoreState);
    }
    return mockVoiceStoreState;
  },
}));

// 必须在 mock 之后导入
import { useVoiceMode } from '@/hooks/useVoiceMode';

// ─── 测试前后重置 ───

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();

  // 重置 WebSocket mock 状态
  wsIsConnected = false;
  wsError = null;
  wsCallbacks = {};

  // 重置 capture mock 状态
  captureIsCapturing = false;
  captureDuration = 0;
  captureError = null;

  // 重置 store mock 状态
  mockVoiceStoreState.settings = null;
  mockVoiceStoreState.recordingMode = 'toggle';
});

afterEach(() => {
  jest.useRealTimers();
});

// ========== 测试用例 ==========

describe('useVoiceMode', () => {
  // ─── 初始状态 ───

  describe('初始状态', () => {
    it('初始状态应为 idle', () => {
      const { result } = renderHook(() => useVoiceMode());

      expect(result.current.sessionState).toBe('idle');
      expect(result.current.isActive).toBe(false);
      expect(result.current.isRecording).toBe(false);
      expect(result.current.volumeLevel).toBe(0);
      expect(result.current.duration).toBe(0);
      expect(result.current.currentResponse).toBe('');
      expect(result.current.currentTranscription).toBe('');
      expect(result.current.currentResponseId).toBeNull();
      expect(result.current.error).toBeNull();
    });
  });

  // ─── enableVoiceMode ───

  describe('enableVoiceMode()', () => {
    it('应将状态变为 configuring 并调用 connect()', () => {
      const { result } = renderHook(() => useVoiceMode());

      act(() => {
        result.current.enableVoiceMode();
      });

      expect(result.current.sessionState).toBe('configuring');
      expect(result.current.isActive).toBe(true);
      expect(mockConnect).toHaveBeenCalled();
      expect(mockSetVoiceMode).toHaveBeenCalledWith(true);
    });

    it('非 idle 状态下调用应被忽略', () => {
      const { result } = renderHook(() => useVoiceMode());

      // 先进入 configuring 状态
      act(() => {
        result.current.enableVoiceMode();
      });
      mockConnect.mockClear();

      // 再次调用应被忽略
      act(() => {
        result.current.enableVoiceMode();
      });

      expect(mockConnect).not.toHaveBeenCalled();
    });
  });

  // ─── WebSocket 连接后自动配置 ───

  describe('WebSocket 连接后自动配置', () => {
    it('isConnected 变为 true 且处于 configuring 时应发送 configure', () => {
      // 设置语音设置
      mockVoiceStoreState.settings = { vadSensitivity: 0.6 };

      const { result, rerender } = renderHook(() => useVoiceMode());

      act(() => {
        result.current.enableVoiceMode();
      });

      expect(result.current.sessionState).toBe('configuring');

      // 模拟 WebSocket 连接成功
      wsIsConnected = true;
      rerender();

      expect(mockConfigure).toHaveBeenCalledWith({
        mode: 'voice_chat',
        vad_threshold: 0.6,
        recording_mode: 'toggle',
      });
    });
  });

  // ─── session.configured 事件 ───

  describe('session.configured 事件', () => {
    it('configuring → listening', () => {
      const { result } = renderHook(() => useVoiceMode());

      act(() => {
        result.current.enableVoiceMode();
      });
      expect(result.current.sessionState).toBe('configuring');

      // 触发 session.configured 回调
      act(() => {
        wsCallbacks.onSessionConfigured?.({});
      });

      expect(result.current.sessionState).toBe('listening');
    });
  });

  // ─── vad.speech_start 事件 ───

  describe('vad.speech_start 事件', () => {
    it('listening → recording，启动 PCM 采集', () => {
      const { result } = renderHook(() => useVoiceMode());

      // 进入 listening 状态
      act(() => {
        result.current.enableVoiceMode();
      });
      act(() => {
        wsCallbacks.onSessionConfigured?.({});
      });
      expect(result.current.sessionState).toBe('listening');

      // VAD 检测到语音
      act(() => {
        wsCallbacks.onVadSpeechStart?.({});
      });

      expect(result.current.sessionState).toBe('recording');
      expect(result.current.isRecording).toBe(true);
      expect(mockSetIsRecording).toHaveBeenCalledWith(true);
      expect(mockStartCapture).toHaveBeenCalled();
    });
  });

  // ─── vad.speech_end 事件 ───

  describe('vad.speech_end 事件', () => {
    it('recording → processing，停止 PCM 采集', () => {
      const { result } = renderHook(() => useVoiceMode());

      // 进入 recording 状态
      act(() => {
        result.current.enableVoiceMode();
      });
      act(() => {
        wsCallbacks.onSessionConfigured?.({});
      });
      act(() => {
        wsCallbacks.onVadSpeechStart?.({});
      });
      expect(result.current.sessionState).toBe('recording');

      // VAD 检测到语音结束
      act(() => {
        wsCallbacks.onVadSpeechEnd?.({});
      });

      expect(result.current.sessionState).toBe('processing');
      expect(result.current.isRecording).toBe(false);
      expect(mockStopCapture).toHaveBeenCalled();
    });
  });

  // ─── response.start 事件 ───

  describe('response.start 事件', () => {
    it('processing → responding', () => {
      const { result } = renderHook(() => useVoiceMode());

      // 进入 processing 状态
      act(() => {
        result.current.enableVoiceMode();
      });
      act(() => {
        wsCallbacks.onSessionConfigured?.({});
      });
      act(() => {
        wsCallbacks.onVadSpeechStart?.({});
      });
      act(() => {
        wsCallbacks.onVadSpeechEnd?.({});
      });
      expect(result.current.sessionState).toBe('processing');

      // 收到响应开始
      act(() => {
        wsCallbacks.onResponseStart?.({ response_id: 'resp-1' });
      });

      expect(result.current.sessionState).toBe('responding');
      expect(result.current.currentResponseId).toBe('resp-1');
      expect(result.current.currentResponse).toBe('');
    });
  });

  // ─── response.delta 事件 ───

  describe('response.delta 事件', () => {
    it('responding 状态下应累积响应内容', () => {
      const { result } = renderHook(() => useVoiceMode());

      // 进入 responding 状态
      act(() => result.current.enableVoiceMode());
      act(() => wsCallbacks.onSessionConfigured?.({}));
      act(() => wsCallbacks.onVadSpeechStart?.({}));
      act(() => wsCallbacks.onVadSpeechEnd?.({}));
      act(() => wsCallbacks.onResponseStart?.({ response_id: 'resp-1' }));
      expect(result.current.sessionState).toBe('responding');

      // 收到增量内容
      act(() => {
        wsCallbacks.onResponseDelta?.({
          type: 'response.delta',
          delta: { content: '你好' },
        });
      });

      expect(result.current.currentResponse).toBe('你好');

      // 继续收到增量
      act(() => {
        wsCallbacks.onResponseDelta?.({
          type: 'response.delta',
          delta: { content: '，世界！' },
        });
      });

      expect(result.current.currentResponse).toBe('你好，世界！');
    });
  });

  // ─── response.end 事件 ───

  describe('response.end 事件', () => {
    it('responding → listening', () => {
      const { result } = renderHook(() => useVoiceMode());

      // 进入 responding 状态
      act(() => result.current.enableVoiceMode());
      act(() => wsCallbacks.onSessionConfigured?.({}));
      act(() => wsCallbacks.onVadSpeechStart?.({}));
      act(() => wsCallbacks.onVadSpeechEnd?.({}));
      act(() => wsCallbacks.onResponseStart?.({ response_id: 'resp-1' }));
      expect(result.current.sessionState).toBe('responding');

      // 响应结束
      act(() => {
        wsCallbacks.onResponseEnd?.({});
      });

      expect(result.current.sessionState).toBe('listening');
      expect(result.current.currentResponseId).toBeNull();
    });
  });

  // ─── transcription.complete 事件 ───

  describe('transcription.complete 事件', () => {
    it('应更新转写文本', () => {
      const { result } = renderHook(() => useVoiceMode());

      act(() => result.current.enableVoiceMode());
      act(() => wsCallbacks.onSessionConfigured?.({}));

      act(() => {
        wsCallbacks.onTranscriptionComplete?.({ text: '今天天气怎么样' });
      });

      expect(result.current.currentTranscription).toBe('今天天气怎么样');
      expect(mockSetCurrentTranscription).toHaveBeenCalledWith('今天天气怎么样');
    });
  });

  // ─── disableVoiceMode ───

  describe('disableVoiceMode()', () => {
    it('应重置为 idle 状态并清理所有资源', () => {
      const { result } = renderHook(() => useVoiceMode());

      // 先启用语音模式
      act(() => result.current.enableVoiceMode());
      act(() => wsCallbacks.onSessionConfigured?.({}));

      // 关闭语音模式
      act(() => {
        result.current.disableVoiceMode();
      });

      expect(result.current.sessionState).toBe('idle');
      expect(result.current.isActive).toBe(false);
      expect(result.current.currentResponse).toBe('');
      expect(result.current.currentTranscription).toBe('');
      expect(result.current.error).toBeNull();

      // 验证调用了清理方法
      expect(mockCloseSession).toHaveBeenCalled();
      expect(mockDisconnect).toHaveBeenCalled();
      expect(mockStopCapture).toHaveBeenCalled();
      expect(mockReset).toHaveBeenCalled();
    });
  });

  // ─── cancelCurrentResponse ───

  describe('cancelCurrentResponse()', () => {
    it('responding → interrupted → listening', () => {
      const { result } = renderHook(() => useVoiceMode());

      // 进入 responding 状态
      act(() => result.current.enableVoiceMode());
      act(() => wsCallbacks.onSessionConfigured?.({}));
      act(() => wsCallbacks.onVadSpeechStart?.({}));
      act(() => wsCallbacks.onVadSpeechEnd?.({}));
      act(() => wsCallbacks.onResponseStart?.({ response_id: 'resp-1' }));
      expect(result.current.sessionState).toBe('responding');

      // 取消当前响应
      act(() => {
        result.current.cancelCurrentResponse();
      });

      expect(result.current.sessionState).toBe('interrupted');
      expect(mockWsCancelResponse).toHaveBeenCalledWith('resp-1');

      // 300ms 后应恢复到 listening
      act(() => {
        jest.advanceTimersByTime(300);
      });

      expect(result.current.sessionState).toBe('listening');
    });

    it('非 responding 状态下调用应被忽略', () => {
      const { result } = renderHook(() => useVoiceMode());

      act(() => result.current.enableVoiceMode());
      act(() => wsCallbacks.onSessionConfigured?.({}));
      expect(result.current.sessionState).toBe('listening');

      act(() => {
        result.current.cancelCurrentResponse();
      });

      // 状态不变
      expect(result.current.sessionState).toBe('listening');
      expect(mockWsCancelResponse).not.toHaveBeenCalled();
    });
  });

  // ─── 完整状态机流转 ───

  describe('完整状态机流转', () => {
    it('idle → configuring → listening → recording → processing → responding → listening', () => {
      const { result } = renderHook(() => useVoiceMode());

      // idle
      expect(result.current.sessionState).toBe('idle');

      // idle → configuring
      act(() => result.current.enableVoiceMode());
      expect(result.current.sessionState).toBe('configuring');

      // configuring → listening
      act(() => wsCallbacks.onSessionConfigured?.({}));
      expect(result.current.sessionState).toBe('listening');

      // listening → recording
      act(() => wsCallbacks.onVadSpeechStart?.({}));
      expect(result.current.sessionState).toBe('recording');

      // recording → processing
      act(() => wsCallbacks.onVadSpeechEnd?.({}));
      expect(result.current.sessionState).toBe('processing');

      // processing → responding
      act(() => wsCallbacks.onResponseStart?.({ response_id: 'resp-1' }));
      expect(result.current.sessionState).toBe('responding');

      // responding → listening
      act(() => wsCallbacks.onResponseEnd?.({}));
      expect(result.current.sessionState).toBe('listening');
    });
  });

  // ─── error 事件 ───

  describe('error 事件', () => {
    it('可恢复错误应回到 listening', () => {
      const { result } = renderHook(() => useVoiceMode());

      act(() => result.current.enableVoiceMode());
      act(() => wsCallbacks.onSessionConfigured?.({}));
      act(() => wsCallbacks.onVadSpeechStart?.({}));
      expect(result.current.sessionState).toBe('recording');

      // 可恢复错误
      act(() => {
        wsCallbacks.onError?.({
          message: '临时错误',
          recoverable: true,
        });
      });

      expect(result.current.sessionState).toBe('listening');
      expect(result.current.error).toBe('临时错误');
    });

    it('不可恢复错误应进入 error 状态', () => {
      const { result } = renderHook(() => useVoiceMode());

      act(() => result.current.enableVoiceMode());
      act(() => wsCallbacks.onSessionConfigured?.({}));

      // 不可恢复错误
      act(() => {
        wsCallbacks.onError?.({
          message: '致命错误',
          recoverable: false,
        });
      });

      expect(result.current.sessionState).toBe('error');
      expect(result.current.isActive).toBe(false);
      expect(result.current.error).toBe('致命错误');
      expect(mockSetVoiceMode).toHaveBeenCalledWith(false);
    });
  });

  // ─── session.closed 事件 ───

  describe('session.closed 事件', () => {
    it('应重置为 idle 状态', () => {
      const { result } = renderHook(() => useVoiceMode());

      act(() => result.current.enableVoiceMode());
      act(() => wsCallbacks.onSessionConfigured?.({}));

      act(() => {
        wsCallbacks.onSessionClosed?.({});
      });

      expect(result.current.sessionState).toBe('idle');
      expect(mockSetVoiceMode).toHaveBeenCalledWith(false);
      expect(mockSetIsRecording).toHaveBeenCalledWith(false);
      expect(mockSetIsConnected).toHaveBeenCalledWith(false);
    });
  });

  // ─── 语音打断 ───

  describe('语音打断（responding 时 speech_start）', () => {
    it('responding 状态下收到 speech_start 应取消当前响应并进入 recording', () => {
      const { result } = renderHook(() => useVoiceMode());

      // 进入 responding 状态
      act(() => result.current.enableVoiceMode());
      act(() => wsCallbacks.onSessionConfigured?.({}));
      act(() => wsCallbacks.onVadSpeechStart?.({}));
      act(() => wsCallbacks.onVadSpeechEnd?.({}));
      act(() => wsCallbacks.onResponseStart?.({ response_id: 'resp-1' }));
      expect(result.current.sessionState).toBe('responding');

      // 用户打断
      act(() => wsCallbacks.onVadSpeechStart?.({}));

      expect(result.current.sessionState).toBe('recording');
      expect(mockWsCancelResponse).toHaveBeenCalledWith('resp-1');
      expect(mockStartCapture).toHaveBeenCalled();
    });
  });

  // ─── isActive 计算 ───

  describe('isActive 派生状态', () => {
    it('idle 状态 isActive 为 false', () => {
      const { result } = renderHook(() => useVoiceMode());
      expect(result.current.isActive).toBe(false);
    });

    it('listening 状态 isActive 为 true', () => {
      const { result } = renderHook(() => useVoiceMode());

      act(() => result.current.enableVoiceMode());
      act(() => wsCallbacks.onSessionConfigured?.({}));

      expect(result.current.isActive).toBe(true);
    });

    it('error 状态 isActive 为 false', () => {
      const { result } = renderHook(() => useVoiceMode());

      act(() => result.current.enableVoiceMode());
      act(() => wsCallbacks.onSessionConfigured?.({}));
      act(() => {
        wsCallbacks.onError?.({ message: '致命', recoverable: false });
      });

      expect(result.current.isActive).toBe(false);
    });
  });

  // ─── error 状态下可重新启用 ───

  describe('error 状态下重新启用', () => {
    it('error 状态下应允许重新 enableVoiceMode', () => {
      const { result } = renderHook(() => useVoiceMode());

      act(() => result.current.enableVoiceMode());
      act(() => wsCallbacks.onSessionConfigured?.({}));
      act(() => {
        wsCallbacks.onError?.({ message: '错误', recoverable: false });
      });
      expect(result.current.sessionState).toBe('error');

      // 重新启用
      mockConnect.mockClear();
      act(() => {
        result.current.enableVoiceMode();
      });

      expect(result.current.sessionState).toBe('configuring');
      expect(mockConnect).toHaveBeenCalled();
    });
  });
});
