/**
 * VoiceprintRecorder 单元测试 (T048)
 *
 * 015-family-multiuser Phase 6:
 * - 初始状态显示"开始录音"按钮
 * - 点击"开始录音"请求麦克风权限
 * - 录音中显示倒计时
 * - 录音不足 10 秒时"完成录音"按钮禁用
 * - 录音完成后触发 onRecordingComplete 回调
 */
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';

// ========== Mock MediaRecorder 和 getUserMedia ==========

let mockMediaRecorderInstance: {
  start: jest.Mock;
  stop: jest.Mock;
  ondataavailable: ((e: { data: Blob }) => void) | null;
  onstop: (() => void) | null;
  state: string;
};

function createMockMediaRecorder() {
  mockMediaRecorderInstance = {
    start: jest.fn().mockImplementation(function (this: typeof mockMediaRecorderInstance) {
      this.state = 'recording';
    }),
    stop: jest.fn().mockImplementation(function (this: typeof mockMediaRecorderInstance) {
      this.state = 'inactive';
      // 模拟 dataavailable 事件
      if (this.ondataavailable) {
        this.ondataavailable({ data: new Blob(['audio-data'], { type: 'audio/webm' }) });
      }
      // 模拟 onstop 事件
      if (this.onstop) {
        this.onstop();
      }
    }),
    ondataavailable: null,
    onstop: null,
    state: 'inactive',
  };
  return mockMediaRecorderInstance;
}

// Mock MediaRecorder constructor
const MockMediaRecorderClass = jest.fn().mockImplementation(() => {
  return createMockMediaRecorder();
});

Object.defineProperty(global, 'MediaRecorder', {
  writable: true,
  value: MockMediaRecorderClass,
});

// Mock navigator.mediaDevices.getUserMedia
const mockGetUserMedia = jest.fn();

Object.defineProperty(global.navigator, 'mediaDevices', {
  writable: true,
  value: {
    getUserMedia: mockGetUserMedia,
  },
});

// Mock MediaStream
const mockMediaStream = {
  getTracks: jest.fn().mockReturnValue([
    { stop: jest.fn() },
  ]),
};

import { VoiceprintRecorder } from '@/components/members/VoiceprintRecorder';

// ========== 测试辅助 ==========

const defaultProps = {
  onRecordingComplete: jest.fn(),
  disabled: false,
};

function renderRecorder(props = {}) {
  return render(<VoiceprintRecorder {...defaultProps} {...props} />);
}

// ========== 测试用例 ==========

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  mockGetUserMedia.mockResolvedValue(mockMediaStream);

  // 重置 Date.now 以便控制时间
  const startTime = 1710000000000;
  jest.spyOn(Date, 'now').mockReturnValue(startTime);
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe('VoiceprintRecorder (T048)', () => {
  // ─── 初始状态 ───

  describe('初始状态', () => {
    it('应显示"开始录音"按钮', () => {
      renderRecorder();

      const startButton = screen.getByRole('button', { name: /开始录音/i });
      expect(startButton).toBeInTheDocument();
    });

    it('应显示录音时长说明', () => {
      renderRecorder();

      expect(screen.getByText(/最少 10 秒/)).toBeInTheDocument();
      expect(screen.getByText(/最多 30 秒/)).toBeInTheDocument();
    });

    it('disabled=true 时按钮应被禁用', () => {
      renderRecorder({ disabled: true });

      const startButton = screen.getByRole('button', { name: /开始录音/i });
      expect(startButton).toBeDisabled();
    });
  });

  // ─── 点击"开始录音"请求麦克风权限 ───

  describe('开始录音', () => {
    it('点击"开始录音"应请求麦克风权限', async () => {
      renderRecorder();

      const startButton = screen.getByRole('button', { name: /开始录音/i });

      await act(async () => {
        fireEvent.click(startButton);
      });

      expect(mockGetUserMedia).toHaveBeenCalledWith({ audio: true });
    });

    it('麦克风权限被拒绝时应显示错误信息', async () => {
      mockGetUserMedia.mockRejectedValueOnce(new Error('Permission denied'));

      renderRecorder();

      const startButton = screen.getByRole('button', { name: /开始录音/i });

      await act(async () => {
        fireEvent.click(startButton);
      });

      expect(screen.getByText(/无法访问麦克风/)).toBeInTheDocument();
    });
  });

  // ─── 录音中显示倒计时 ───

  describe('录音中倒计时', () => {
    it('录音中应显示时长倒计时', async () => {
      const startTime = 1710000000000;
      let currentTime = startTime;
      (Date.now as jest.Mock).mockImplementation(() => currentTime);

      renderRecorder();

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /开始录音/i }));
      });

      // 模拟 3 秒过去
      currentTime = startTime + 3000;
      act(() => {
        jest.advanceTimersByTime(3000);
      });

      // 应显示 3s / 30s 格式
      expect(screen.getByText(/3s/)).toBeInTheDocument();
      expect(screen.getByText(/30s/)).toBeInTheDocument();
    });
  });

  // ─── 录音不足 10 秒时完成按钮禁用 ───

  describe('完成录音按钮禁用状态', () => {
    it('录音不足 10 秒时"完成录音"按钮应禁用', async () => {
      const startTime = 1710000000000;
      let currentTime = startTime;
      (Date.now as jest.Mock).mockImplementation(() => currentTime);

      renderRecorder();

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /开始录音/i }));
      });

      // 模拟 5 秒（不足 10 秒）
      currentTime = startTime + 5000;
      act(() => {
        jest.advanceTimersByTime(5000);
      });

      const finishButton = screen.getByRole('button', { name: /完成录音/i });
      expect(finishButton).toBeDisabled();
    });

    it('录音达到 10 秒时"完成录音"按钮应可点击', async () => {
      const startTime = 1710000000000;
      let currentTime = startTime;
      (Date.now as jest.Mock).mockImplementation(() => currentTime);

      renderRecorder();

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /开始录音/i }));
      });

      // 模拟 10 秒
      currentTime = startTime + 10000;
      act(() => {
        jest.advanceTimersByTime(10000);
      });

      const finishButton = screen.getByRole('button', { name: /完成录音/i });
      expect(finishButton).not.toBeDisabled();
    });
  });

  // ─── 录音完成后触发回调 ───

  describe('录音完成回调', () => {
    it('手动完成录音后应触发 onRecordingComplete 回调', async () => {
      const onRecordingComplete = jest.fn();
      const startTime = 1710000000000;
      let currentTime = startTime;
      (Date.now as jest.Mock).mockImplementation(() => currentTime);

      renderRecorder({ onRecordingComplete });

      // 开始录音
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /开始录音/i }));
      });

      // 模拟 12 秒（超过最低 10 秒）
      currentTime = startTime + 12000;
      act(() => {
        jest.advanceTimersByTime(12000);
      });

      // 点击完成录音
      await act(async () => {
        const finishButton = screen.getByRole('button', { name: /完成录音/i });
        fireEvent.click(finishButton);
      });

      // 等待 onstop 回调执行
      await waitFor(() => {
        expect(onRecordingComplete).toHaveBeenCalledTimes(1);
      });

      // 验证传递了 Blob 对象
      const blob = onRecordingComplete.mock.calls[0][0];
      expect(blob).toBeInstanceOf(Blob);
    });
  });
});
