/**
 * VoiceWaveform 组件测试
 *
 * 测试 Canvas 波形渲染、动画帧管理和录音状态指示器。
 */
import { render, screen, cleanup } from '@testing-library/react';

import { VoiceWaveform } from '../VoiceWaveform';

// ============ Mock 设置 ============

/** 创建 mock CanvasRenderingContext2D */
function createMockContext(): Partial<CanvasRenderingContext2D> {
  return {
    clearRect: jest.fn(),
    fillRect: jest.fn(),
    beginPath: jest.fn(),
    closePath: jest.fn(),
    moveTo: jest.fn(),
    lineTo: jest.fn(),
    arcTo: jest.fn(),
    rect: jest.fn(),
    fill: jest.fn(),
    stroke: jest.fn(),
    save: jest.fn(),
    restore: jest.fn(),
    scale: jest.fn(),
    fillStyle: '',
  };
}

describe('VoiceWaveform', () => {
  let mockCtx: Partial<CanvasRenderingContext2D>;
  let rafCallbacks: ((time: number) => void)[];
  let rafIdCounter: number;
  let cancelledRafIds: Set<number>;

  beforeEach(() => {
    mockCtx = createMockContext();
    rafCallbacks = [];
    rafIdCounter = 0;
    cancelledRafIds = new Set();

    // Mock getContext 返回 mock context
    jest.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      mockCtx as CanvasRenderingContext2D
    );

    // Mock requestAnimationFrame：收集回调但不自动执行
    jest.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      rafIdCounter++;
      rafCallbacks.push(cb);
      return rafIdCounter;
    });

    // Mock cancelAnimationFrame
    jest.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
      cancelledRafIds.add(id);
    });

    // Mock devicePixelRatio
    Object.defineProperty(window, 'devicePixelRatio', {
      value: 2,
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    cleanup();
    jest.restoreAllMocks();
  });

  /** 执行一帧动画 */
  function flushOneFrame() {
    const cb = rafCallbacks.shift();
    if (cb) cb(performance.now());
  }

  // ---------- Canvas 元素渲染 ----------

  describe('Canvas 元素渲染', () => {
    it('应渲染 canvas 元素', () => {
      const { container } = render(
        <VoiceWaveform volumeLevel={0} isRecording={false} />
      );

      const canvas = container.querySelector('canvas');
      expect(canvas).toBeInTheDocument();
    });

    it('应使用默认尺寸 300x120', () => {
      const { container } = render(
        <VoiceWaveform volumeLevel={0} isRecording={false} />
      );

      const wrapper = container.firstElementChild as HTMLElement;
      expect(wrapper.style.width).toBe('300px');
      expect(wrapper.style.height).toBe('120px');
    });

    it('canvas 物理尺寸应考虑 devicePixelRatio', () => {
      const { container } = render(
        <VoiceWaveform volumeLevel={0} isRecording={false} />
      );

      const canvas = container.querySelector('canvas') as HTMLCanvasElement;
      // devicePixelRatio = 2, 默认 width=300, height=120
      expect(canvas.width).toBe(600);
      expect(canvas.height).toBe(240);
    });
  });

  // ---------- 自定义尺寸 ----------

  describe('自定义尺寸', () => {
    it('应支持自定义 width 和 height', () => {
      const { container } = render(
        <VoiceWaveform
          volumeLevel={0.5}
          isRecording={true}
          width={200}
          height={80}
        />
      );

      const wrapper = container.firstElementChild as HTMLElement;
      expect(wrapper.style.width).toBe('200px');
      expect(wrapper.style.height).toBe('80px');

      const canvas = container.querySelector('canvas') as HTMLCanvasElement;
      // devicePixelRatio = 2
      expect(canvas.width).toBe(400);
      expect(canvas.height).toBe(160);
    });
  });

  // ---------- 录音状态指示器 ----------

  describe('录音状态指示器', () => {
    it('isRecording=true 时应显示 REC 指示器', () => {
      render(
        <VoiceWaveform volumeLevel={0.5} isRecording={true} />
      );

      expect(screen.getByText('REC')).toBeInTheDocument();
    });

    it('isRecording=false 时不应显示 REC 指示器', () => {
      render(
        <VoiceWaveform volumeLevel={0} isRecording={false} />
      );

      expect(screen.queryByText('REC')).not.toBeInTheDocument();
    });

    it('REC 指示器应包含红色圆点', () => {
      const { container } = render(
        <VoiceWaveform volumeLevel={0.8} isRecording={true} />
      );

      // 红色圆点使用 bg-red-500 类
      const redDot = container.querySelector('.bg-red-500');
      expect(redDot).toBeInTheDocument();
    });
  });

  // ---------- 动画帧管理 ----------

  describe('动画帧管理', () => {
    it('组件挂载时应调用 requestAnimationFrame 启动动画', () => {
      render(
        <VoiceWaveform volumeLevel={0} isRecording={false} />
      );

      expect(window.requestAnimationFrame).toHaveBeenCalled();
    });

    it('动画帧执行后应再次调用 requestAnimationFrame（持续循环）', () => {
      render(
        <VoiceWaveform volumeLevel={0.5} isRecording={true} />
      );

      const initialCallCount = (window.requestAnimationFrame as jest.Mock)
        .mock.calls.length;

      // 执行一帧
      flushOneFrame();

      // 应再次请求下一帧
      expect(
        (window.requestAnimationFrame as jest.Mock).mock.calls.length
      ).toBeGreaterThan(initialCallCount);
    });

    it('组件卸载时应调用 cancelAnimationFrame 清理动画', () => {
      const { unmount } = render(
        <VoiceWaveform volumeLevel={0} isRecording={false} />
      );

      unmount();

      expect(window.cancelAnimationFrame).toHaveBeenCalled();
    });
  });

  // ---------- Canvas 绘制调用 ----------

  describe('Canvas 绘制', () => {
    it('动画帧执行时应调用 clearRect 清空画布', () => {
      render(
        <VoiceWaveform volumeLevel={0.5} isRecording={true} />
      );

      flushOneFrame();

      expect(mockCtx.clearRect).toHaveBeenCalled();
    });

    it('动画帧执行时应调用 save 和 restore', () => {
      render(
        <VoiceWaveform volumeLevel={0.5} isRecording={true} />
      );

      flushOneFrame();

      expect(mockCtx.save).toHaveBeenCalled();
      expect(mockCtx.restore).toHaveBeenCalled();
    });

    it('应根据 devicePixelRatio 调用 scale', () => {
      render(
        <VoiceWaveform volumeLevel={0.5} isRecording={true} />
      );

      flushOneFrame();

      // devicePixelRatio = 2
      expect(mockCtx.scale).toHaveBeenCalledWith(2, 2);
    });
  });
});
