/**
 * AudioPlayer 组件测试 (T084)
 *
 * 覆盖: 播放/暂停、进度条、打断清理
 */
import { render, screen, fireEvent, act } from '@testing-library/react';
import { createRef } from 'react';
import { AudioPlayer, AudioPlayerRef } from '@/components/chat/AudioPlayer';

describe('AudioPlayer', () => {
  let mockPlay: jest.SpyInstance;
  let mockPause: jest.SpyInstance;

  beforeEach(() => {
    mockPlay = jest
      .spyOn(HTMLMediaElement.prototype, 'play')
      .mockResolvedValue(undefined);
    mockPause = jest
      .spyOn(HTMLMediaElement.prototype, 'pause')
      .mockImplementation(() => {});
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  describe('渲染', () => {
    it('应渲染播放按钮', () => {
      const { container } = render(<AudioPlayer src="test.mp3" />);
      const button = container.querySelector('button');
      expect(button).toBeInTheDocument();
    });

    it('应渲染隐藏的 audio 元素', () => {
      const { container } = render(<AudioPlayer src="test.mp3" />);
      const audio = container.querySelector('audio');
      expect(audio).toBeInTheDocument();
      expect(audio).toHaveAttribute('src', 'test.mp3');
      expect(audio).toHaveAttribute('preload', 'metadata');
    });

    it('应显示初始时长 0:00', () => {
      render(<AudioPlayer src="test.mp3" />);
      expect(screen.getByText(/0:00/)).toBeInTheDocument();
    });

    it('传入 duration 应显示总时长', () => {
      render(<AudioPlayer src="test.mp3" duration={90} />);
      // 90s → "1:30"
      expect(screen.getByText(/1:30/)).toBeInTheDocument();
    });
  });

  describe('播放/暂停', () => {
    it('点击按钮应调用 play', () => {
      const { container } = render(<AudioPlayer src="test.mp3" />);
      const button = container.querySelector('button')!;
      fireEvent.click(button);
      expect(mockPlay).toHaveBeenCalled();
    });

    it('播放中再次点击应调用 pause', () => {
      const { container } = render(<AudioPlayer src="test.mp3" />);
      const audio = container.querySelector('audio')!;

      // Simulate play event to set isPlaying=true
      fireEvent(audio, new Event('play'));

      const button = container.querySelector('button')!;
      fireEvent.click(button);
      expect(mockPause).toHaveBeenCalled();
    });

    it('播放结束后应重置状态', () => {
      const { container } = render(<AudioPlayer src="test.mp3" />);
      const audio = container.querySelector('audio')!;

      // Simulate play then ended
      fireEvent(audio, new Event('play'));
      fireEvent(audio, new Event('ended'));

      // Should be able to play again (no pause called after ended)
      const button = container.querySelector('button')!;
      fireEvent.click(button);
      expect(mockPlay).toHaveBeenCalled();
    });
  });

  describe('进度条', () => {
    it('初始进度应为 0%', () => {
      const { container } = render(
        <AudioPlayer src="test.mp3" duration={30} />
      );
      const progressBar = container.querySelector('[style*="width"]');
      expect(progressBar).toHaveStyle({ width: '0%' });
    });
  });

  describe('stopAndClear', () => {
    it('调用 stopAndClear 应暂停并重置', () => {
      const ref = createRef<AudioPlayerRef>();
      const { container } = render(
        <AudioPlayer ref={ref} src="test.mp3" />
      );

      // Simulate playing
      const audio = container.querySelector('audio')!;
      fireEvent(audio, new Event('play'));

      // Call stopAndClear
      act(() => {
        ref.current?.stopAndClear();
      });

      expect(mockPause).toHaveBeenCalled();
    });

    it('stopAndClear 后应能重新播放', () => {
      const ref = createRef<AudioPlayerRef>();
      const { container } = render(
        <AudioPlayer ref={ref} src="test.mp3" />
      );

      // Play, then stop
      act(() => {
        ref.current?.stopAndClear();
      });

      // Should be in stopped state, click to play
      const button = container.querySelector('button')!;
      fireEvent.click(button);
      expect(mockPlay).toHaveBeenCalled();
    });
  });

  describe('loadedmetadata', () => {
    it('应从 audio 元素读取时长', () => {
      const { container } = render(<AudioPlayer src="test.mp3" />);
      const audio = container.querySelector('audio')!;

      // Mock duration property
      Object.defineProperty(audio, 'duration', {
        value: 45,
        configurable: true,
      });

      fireEvent(audio, new Event('loadedmetadata'));
      // 45s → "0:45"
      expect(screen.getByText(/0:45/)).toBeInTheDocument();
    });
  });
});
