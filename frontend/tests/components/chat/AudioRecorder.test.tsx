/**
 * AudioRecorder 组件测试 (T083)
 *
 * 覆盖: 开始/停止录音、时长限制、最短1秒校验、格式输出
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { AudioRecorder } from '@/components/chat/AudioRecorder';

// Mock useAudioRecorder hook
const mockStartRecording = jest.fn();
const mockStopRecording = jest.fn();
const mockReset = jest.fn();

let mockStatus: 'idle' | 'recording' | 'stopped' = 'idle';
let mockDuration = 0;
let mockAudioBlob: Blob | null = null;
let mockAudioUrl: string | null = null;
let mockError: string | null = null;

jest.mock('@/hooks/useAudioRecorder', () => ({
  useAudioRecorder: () => ({
    status: mockStatus,
    duration: mockDuration,
    audioBlob: mockAudioBlob,
    audioUrl: mockAudioUrl,
    startRecording: mockStartRecording,
    stopRecording: mockStopRecording,
    reset: mockReset,
    error: mockError,
  }),
}));

describe('AudioRecorder', () => {
  const mockOnRecordingComplete = jest.fn();
  const mockOnCancel = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    mockStatus = 'idle';
    mockDuration = 0;
    mockAudioBlob = null;
    mockAudioUrl = null;
    mockError = null;
  });

  describe('idle 状态', () => {
    it('应显示"开始录音"按钮', () => {
      render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      expect(screen.getByText('开始录音')).toBeInTheDocument();
    });

    it('点击开始录音应调用 startRecording', () => {
      render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      fireEvent.click(screen.getByText('开始录音'));
      expect(mockStartRecording).toHaveBeenCalled();
    });

    it('disabled 状态下按钮应禁用', () => {
      render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
          disabled={true}
        />
      );
      expect(screen.getByText('开始录音')).toBeDisabled();
    });
  });

  describe('recording 状态', () => {
    beforeEach(() => {
      mockStatus = 'recording';
      mockDuration = 15;
    });

    it('应显示停止按钮', () => {
      render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      expect(screen.getByText('停止')).toBeInTheDocument();
    });

    it('应显示录音时长和最大时长', () => {
      render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      // duration=15 → "0:15", max=60 → "1:00"
      expect(screen.getByText(/0:15/)).toBeInTheDocument();
      expect(screen.getByText(/1:00/)).toBeInTheDocument();
    });

    it('点击停止应调用 stopRecording', () => {
      render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      fireEvent.click(screen.getByText('停止'));
      expect(mockStopRecording).toHaveBeenCalled();
    });

    it('应显示录音动画指示器', () => {
      const { container } = render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      const pulsingDot = container.querySelector('.animate-pulse');
      expect(pulsingDot).toBeInTheDocument();
    });
  });

  describe('stopped 状态', () => {
    beforeEach(() => {
      mockStatus = 'stopped';
      mockDuration = 5;
      mockAudioBlob = new Blob(['audio-data'], { type: 'audio/webm' });
      mockAudioUrl = 'blob:mock-audio-url';
    });

    it('应显示预览播放器', () => {
      const { container } = render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      const audio = container.querySelector('audio');
      expect(audio).toBeInTheDocument();
      expect(audio).toHaveAttribute('src', 'blob:mock-audio-url');
    });

    it('应显示发送按钮和时长', () => {
      render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      expect(screen.getByText('发送')).toBeInTheDocument();
      expect(screen.getByText('0:05')).toBeInTheDocument();
    });

    it('发送应调用 onRecordingComplete 并 reset', () => {
      render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      fireEvent.click(screen.getByText('发送'));
      expect(mockOnRecordingComplete).toHaveBeenCalledWith(mockAudioBlob, 5);
      expect(mockReset).toHaveBeenCalled();
    });

    it('duration < 1 秒时不应发送', () => {
      mockDuration = 0;
      render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      fireEvent.click(screen.getByText('发送'));
      expect(mockOnRecordingComplete).not.toHaveBeenCalled();
    });
  });

  describe('取消按钮', () => {
    it('点击取消应调用 reset 和 onCancel', () => {
      render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      fireEvent.click(screen.getByText('取消'));
      expect(mockReset).toHaveBeenCalled();
      expect(mockOnCancel).toHaveBeenCalled();
    });

    it('录音中取消也应生效', () => {
      mockStatus = 'recording';
      mockDuration = 3;
      render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      fireEvent.click(screen.getByText('取消'));
      expect(mockReset).toHaveBeenCalled();
      expect(mockOnCancel).toHaveBeenCalled();
    });
  });

  describe('错误显示', () => {
    it('应显示错误信息', () => {
      mockError = '无法访问麦克风，请检查权限设置';
      render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      expect(screen.getByText('无法访问麦克风，请检查权限设置')).toBeInTheDocument();
    });

    it('无错误时不显示错误信息', () => {
      mockError = null;
      render(
        <AudioRecorder
          onRecordingComplete={mockOnRecordingComplete}
          onCancel={mockOnCancel}
        />
      );
      const errorElements = document.querySelectorAll('.text-red-500');
      // 只有录音动画的红色点，不应有错误文本
      const errorTexts = Array.from(errorElements).filter(
        (el) => el.textContent && el.textContent.length > 5
      );
      expect(errorTexts.length).toBe(0);
    });
  });
});
