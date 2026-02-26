/**
 * VoiceModePanel 组件测试
 *
 * 测试语音控制面板的交互行为、状态显示和录音模式切换。
 */
import { render, screen, fireEvent, act } from '@testing-library/react';

import type { VoiceSessionState, RecordingMode } from '@/types/voice';

// Mock VoiceWaveform 子组件，避免 Canvas 渲染问题
jest.mock('@/components/voice/VoiceWaveform', () => ({
  VoiceWaveform: (props: Record<string, unknown>) => (
    <div data-testid="voice-waveform" data-volume={props.volumeLevel} />
  ),
}));

// 导入被测组件（必须在 mock 之后）
import { VoiceModePanel } from '../VoiceModePanel';

// ============ 辅助函数 ============

/** 生成默认 props */
function createDefaultProps(overrides?: Partial<Parameters<typeof VoiceModePanel>[0]>) {
  return {
    sessionState: 'idle' as VoiceSessionState,
    isRecording: false,
    volumeLevel: 0,
    duration: 0,
    currentResponse: '',
    currentTranscription: '',
    error: null,
    recordingMode: 'hold' as RecordingMode,
    onClose: jest.fn(),
    onStartRecording: jest.fn(),
    onStopRecording: jest.fn(),
    onCancelResponse: jest.fn(),
    ...overrides,
  };
}

// ============ 测试用例 ============

describe('VoiceModePanel', () => {
  // 每次测试前清理 cookie mock
  let cookieStore: string;

  beforeEach(() => {
    jest.useFakeTimers();
    cookieStore = '';

    // Mock document.cookie
    Object.defineProperty(document, 'cookie', {
      get: jest.fn(() => cookieStore),
      set: jest.fn((value: string) => {
        // 简易 cookie 追加模拟（测试用）
        const name = value.split('=')[0];
        // 移除同名旧 cookie
        const cookies = cookieStore
          .split('; ')
          .filter((c) => c && !c.startsWith(`${name}=`));
        cookies.push(value.split(';')[0]);
        cookieStore = cookies.join('; ');
      }),
      configurable: true,
    });

    // Mock requestAnimationFrame（入场动画使用）
    jest.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      cb(0);
      return 0;
    });
    jest.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
  });

  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  // ---------- 状态文字显示 ----------

  describe('状态文字映射', () => {
    const statusTextCases: [VoiceSessionState, string][] = [
      ['idle', ''],
      ['configuring', '连接中...'],
      ['listening', '等待说话...'],
      ['recording', '录音中'],
      ['processing', '处理中...'],
      ['responding', 'AI 回复中...'],
      ['interrupted', '已中断'],
      ['error', '出错了'],
    ];

    it.each(statusTextCases)(
      '状态 %s 时应显示 "%s"',
      (state, expectedText) => {
        const props = createDefaultProps({ sessionState: state });
        render(<VoiceModePanel {...props} />);

        if (expectedText) {
          expect(screen.getByText(expectedText)).toBeInTheDocument();
        }
      }
    );

    it('idle 状态时状态文字区域应为空', () => {
      const props = createDefaultProps({ sessionState: 'idle' });
      render(<VoiceModePanel {...props} />);
      // idle 对应空字符串，不应有其他状态文字
      expect(screen.queryByText('连接中...')).not.toBeInTheDocument();
      expect(screen.queryByText('录音中')).not.toBeInTheDocument();
    });
  });

  // ---------- 按钮禁用状态 ----------

  describe('按钮禁用状态', () => {
    it('configuring 状态下录音按钮应被禁用', () => {
      const props = createDefaultProps({ sessionState: 'configuring' });
      render(<VoiceModePanel {...props} />);

      const button = screen.getByRole('button', { name: '开始录音' });
      expect(button).toBeDisabled();
    });

    it('processing 状态下录音按钮应被禁用', () => {
      const props = createDefaultProps({ sessionState: 'processing' });
      render(<VoiceModePanel {...props} />);

      const button = screen.getByRole('button', { name: '开始录音' });
      expect(button).toBeDisabled();
    });

    it('listening 状态下录音按钮应可用', () => {
      const props = createDefaultProps({ sessionState: 'listening' });
      render(<VoiceModePanel {...props} />);

      const button = screen.getByRole('button', { name: '开始录音' });
      expect(button).not.toBeDisabled();
    });
  });

  // ---------- hold 模式交互 ----------

  describe('hold 模式交互', () => {
    it('pointerdown 应触发 onStartRecording', () => {
      const props = createDefaultProps({
        sessionState: 'listening',
        recordingMode: 'hold',
      });
      render(<VoiceModePanel {...props} />);

      const button = screen.getByRole('button', { name: '开始录音' });
      fireEvent.pointerDown(button);

      expect(props.onStartRecording).toHaveBeenCalledTimes(1);
    });

    it('pointerup 应触发 onStopRecording（录音中）', () => {
      const props = createDefaultProps({
        sessionState: 'recording',
        recordingMode: 'hold',
        isRecording: true,
      });
      render(<VoiceModePanel {...props} />);

      const button = screen.getByRole('button', { name: '停止录音' });
      fireEvent.pointerUp(button);

      expect(props.onStopRecording).toHaveBeenCalledTimes(1);
    });

    it('pointerleave 应触发 onStopRecording（录音中 hold 模式）', () => {
      const props = createDefaultProps({
        sessionState: 'recording',
        recordingMode: 'hold',
        isRecording: true,
      });
      render(<VoiceModePanel {...props} />);

      const button = screen.getByRole('button', { name: '停止录音' });
      fireEvent.pointerLeave(button);

      expect(props.onStopRecording).toHaveBeenCalledTimes(1);
    });

    it('hold 模式 + 未录音时 pointerup 不触发 onStopRecording', () => {
      const props = createDefaultProps({
        sessionState: 'listening',
        recordingMode: 'hold',
        isRecording: false,
      });
      render(<VoiceModePanel {...props} />);

      const button = screen.getByRole('button', { name: '开始录音' });
      fireEvent.pointerUp(button);

      expect(props.onStopRecording).not.toHaveBeenCalled();
    });

    it('hold 模式空闲时应显示 "按住说话" 提示', () => {
      const props = createDefaultProps({
        sessionState: 'listening',
        recordingMode: 'hold',
        isRecording: false,
      });
      render(<VoiceModePanel {...props} />);

      expect(screen.getByText('按住说话')).toBeInTheDocument();
    });
  });

  // ---------- toggle 模式交互 ----------

  describe('toggle 模式交互', () => {
    it('未录音时点击应触发 onStartRecording', () => {
      const props = createDefaultProps({
        sessionState: 'listening',
        recordingMode: 'toggle',
        isRecording: false,
      });
      render(<VoiceModePanel {...props} />);

      const button = screen.getByRole('button', { name: '开始录音' });
      fireEvent.pointerDown(button);

      expect(props.onStartRecording).toHaveBeenCalledTimes(1);
    });

    it('录音中点击应触发 onStopRecording', () => {
      const props = createDefaultProps({
        sessionState: 'recording',
        recordingMode: 'toggle',
        isRecording: true,
      });
      render(<VoiceModePanel {...props} />);

      const button = screen.getByRole('button', { name: '停止录音' });
      fireEvent.pointerDown(button);

      expect(props.onStopRecording).toHaveBeenCalledTimes(1);
    });

    it('toggle 模式空闲时应显示 "点击开始录音" 提示', () => {
      const props = createDefaultProps({
        sessionState: 'listening',
        recordingMode: 'toggle',
        isRecording: false,
      });
      render(<VoiceModePanel {...props} />);

      expect(screen.getByText('点击开始录音')).toBeInTheDocument();
    });
  });

  // ---------- responding 状态 ----------

  describe('responding 状态交互', () => {
    it('responding 时点击按钮应触发 onCancelResponse', () => {
      const props = createDefaultProps({ sessionState: 'responding' });
      render(<VoiceModePanel {...props} />);

      const button = screen.getByRole('button', { name: '停止回复' });
      fireEvent.pointerDown(button);

      expect(props.onCancelResponse).toHaveBeenCalledTimes(1);
      // 不应触发 onStartRecording
      expect(props.onStartRecording).not.toHaveBeenCalled();
    });

    it('responding 时按钮 aria-label 应为 "停止回复"', () => {
      const props = createDefaultProps({ sessionState: 'responding' });
      render(<VoiceModePanel {...props} />);

      expect(
        screen.getByRole('button', { name: '停止回复' })
      ).toBeInTheDocument();
    });
  });

  // ---------- 关闭按钮 ----------

  describe('关闭按钮', () => {
    it('点击关闭按钮应在 300ms 延迟后调用 onClose', () => {
      const props = createDefaultProps();
      render(<VoiceModePanel {...props} />);

      const closeButton = screen.getByRole('button', { name: '关闭语音模式' });
      fireEvent.click(closeButton);

      // 立即检查：还未调用 onClose
      expect(props.onClose).not.toHaveBeenCalled();

      // 快进 300ms
      act(() => {
        jest.advanceTimersByTime(300);
      });

      expect(props.onClose).toHaveBeenCalledTimes(1);
    });
  });

  // ---------- 错误信息显示 ----------

  describe('错误信息显示', () => {
    it('有错误时应显示错误文字', () => {
      const props = createDefaultProps({ error: '麦克风权限被拒绝' });
      render(<VoiceModePanel {...props} />);

      expect(screen.getByText('麦克风权限被拒绝')).toBeInTheDocument();
    });

    it('无错误时不应显示错误区域', () => {
      const props = createDefaultProps({ error: null });
      render(<VoiceModePanel {...props} />);

      expect(screen.queryByText('麦克风权限被拒绝')).not.toBeInTheDocument();
    });
  });

  // ---------- 录音时长格式化 ----------

  describe('录音时长格式化', () => {
    it('应以 M:SS 格式显示录音时长', () => {
      const props = createDefaultProps({
        sessionState: 'recording',
        isRecording: true,
        duration: 65,
      });
      render(<VoiceModePanel {...props} />);

      // 65 秒 = 1:05
      expect(screen.getByText('1:05')).toBeInTheDocument();
    });

    it('0 秒应显示为 0:00', () => {
      const props = createDefaultProps({
        sessionState: 'recording',
        isRecording: true,
        duration: 0,
      });
      render(<VoiceModePanel {...props} />);

      expect(screen.getByText('0:00')).toBeInTheDocument();
    });

    it('未录音时不应显示时长', () => {
      const props = createDefaultProps({
        sessionState: 'listening',
        isRecording: false,
        duration: 30,
      });
      render(<VoiceModePanel {...props} />);

      expect(screen.queryByText('0:30')).not.toBeInTheDocument();
    });
  });

  // ---------- 转写文本和 AI 回复预览 ----------

  describe('转写文本和 AI 回复预览', () => {
    it('有转写文本时应显示', () => {
      const props = createDefaultProps({
        currentTranscription: '你好世界',
      });
      render(<VoiceModePanel {...props} />);

      expect(screen.getByText('你好世界')).toBeInTheDocument();
    });

    it('有 AI 回复且处于 responding 状态时应显示', () => {
      const props = createDefaultProps({
        sessionState: 'responding',
        currentResponse: 'AI 正在回答...',
      });
      render(<VoiceModePanel {...props} />);

      expect(screen.getByText('AI 正在回答...')).toBeInTheDocument();
    });

    it('有 AI 回复但非 responding 状态时不应显示回复', () => {
      const props = createDefaultProps({
        sessionState: 'idle',
        currentResponse: 'AI 正在回答...',
        currentTranscription: '',
      });
      render(<VoiceModePanel {...props} />);

      // currentResponse 不为空但 sessionState 不是 responding，
      // 而 currentTranscription 为空，所以整个预览区域不渲染
      expect(screen.queryByText('AI 正在回答...')).not.toBeInTheDocument();
    });

    it('无转写文本和回复时不渲染预览区域', () => {
      const props = createDefaultProps({
        currentTranscription: '',
        currentResponse: '',
      });
      const { container } = render(<VoiceModePanel {...props} />);

      // 预览区域容器（max-w-md）不应存在
      expect(
        container.querySelector('.max-w-md')
      ).not.toBeInTheDocument();
    });
  });

  // ---------- VoiceWaveform 子组件 ----------

  describe('VoiceWaveform 子组件', () => {
    it('录音中应渲染 VoiceWaveform', () => {
      const props = createDefaultProps({
        sessionState: 'recording',
        isRecording: true,
      });
      render(<VoiceModePanel {...props} />);

      expect(screen.getByTestId('voice-waveform')).toBeInTheDocument();
    });

    it('listening 状态应渲染 VoiceWaveform', () => {
      const props = createDefaultProps({
        sessionState: 'listening',
      });
      render(<VoiceModePanel {...props} />);

      expect(screen.getByTestId('voice-waveform')).toBeInTheDocument();
    });

    it('idle 状态不应渲染 VoiceWaveform', () => {
      const props = createDefaultProps({
        sessionState: 'idle',
        isRecording: false,
      });
      render(<VoiceModePanel {...props} />);

      expect(screen.queryByTestId('voice-waveform')).not.toBeInTheDocument();
    });
  });

  // ---------- 声纹注册提示 ----------

  describe('声纹注册提示', () => {
    it('首次进入时应显示声纹注册提示', () => {
      const props = createDefaultProps();
      render(<VoiceModePanel {...props} />);

      expect(
        screen.getByText('建议注册声纹以支持共享设备使用')
      ).toBeInTheDocument();
    });

    it('已关闭过提示时不应再显示', () => {
      cookieStore = 'linchat_speaker_enroll_dismissed=true';
      const props = createDefaultProps();
      render(<VoiceModePanel {...props} />);

      expect(
        screen.queryByText('建议注册声纹以支持共享设备使用')
      ).not.toBeInTheDocument();
    });

    it('点击关闭提示按钮应隐藏提示并记录到 Cookie', () => {
      const props = createDefaultProps();
      render(<VoiceModePanel {...props} />);

      // 找到关闭提示按钮
      const dismissButton = screen.getByRole('button', { name: '关闭提示' });
      fireEvent.click(dismissButton);

      // 提示应消失
      expect(
        screen.queryByText('建议注册声纹以支持共享设备使用')
      ).not.toBeInTheDocument();

      // Cookie 应包含 dismissed 标记
      expect(cookieStore).toContain('linchat_speaker_enroll_dismissed=true');
    });
  });
});
