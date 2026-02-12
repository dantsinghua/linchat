/**
 * 音频播放器组件 (T062)
 *
 * 功能：播放/暂停/进度/打断
 * - 暴露 stopAndClear() 供外部调用
 * - 打断时清空播放队列并重置状态
 *
 * 参考: specs/008-multimodal-minicpm/tasks.md T062
 */
'use client';

import {
  forwardRef,
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';

import { formatDuration } from '@/types/media';

export interface AudioPlayerRef {
  /** 停止播放并清空队列 */
  stopAndClear: () => void;
}

interface AudioPlayerProps {
  /** 音频 URL */
  src: string;
  /** 可选：总时长（秒） */
  duration?: number;
}

export const AudioPlayer = memo(
  forwardRef<AudioPlayerRef, AudioPlayerProps>(function AudioPlayer(
    { src, duration: initialDuration },
    ref
  ) {
    const audioRef = useRef<HTMLAudioElement>(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(initialDuration || 0);

    useImperativeHandle(ref, () => ({
      stopAndClear: () => {
        if (audioRef.current) {
          audioRef.current.pause();
          audioRef.current.currentTime = 0;
        }
        setIsPlaying(false);
        setCurrentTime(0);
      },
    }));

    const togglePlay = useCallback(() => {
      if (!audioRef.current) return;

      if (isPlaying) {
        audioRef.current.pause();
      } else {
        audioRef.current.play().catch(() => {
          setIsPlaying(false);
        });
      }
    }, [isPlaying]);

    useEffect(() => {
      const audio = audioRef.current;
      if (!audio) return;

      const onPlay = () => setIsPlaying(true);
      const onPause = () => setIsPlaying(false);
      const onEnded = () => {
        setIsPlaying(false);
        setCurrentTime(0);
      };
      const onTimeUpdate = () => setCurrentTime(audio.currentTime);
      const onLoadedMetadata = () => {
        if (audio.duration && isFinite(audio.duration)) {
          setDuration(audio.duration);
        }
      };

      audio.addEventListener('play', onPlay);
      audio.addEventListener('pause', onPause);
      audio.addEventListener('ended', onEnded);
      audio.addEventListener('timeupdate', onTimeUpdate);
      audio.addEventListener('loadedmetadata', onLoadedMetadata);

      return () => {
        audio.removeEventListener('play', onPlay);
        audio.removeEventListener('pause', onPause);
        audio.removeEventListener('ended', onEnded);
        audio.removeEventListener('timeupdate', onTimeUpdate);
        audio.removeEventListener('loadedmetadata', onLoadedMetadata);
      };
    }, []);

    const progress = duration > 0 ? (currentTime / duration) * 100 : 0;

    return (
      <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-600 dark:bg-gray-700">
        {/* 播放/暂停按钮 */}
        <button
          onClick={togglePlay}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary-500 text-white transition-colors hover:bg-primary-600"
        >
          {isPlaying ? (
            <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
              <rect x="6" y="4" width="4" height="16" />
              <rect x="14" y="4" width="4" height="16" />
            </svg>
          ) : (
            <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7z" />
            </svg>
          )}
        </button>

        {/* 进度条 */}
        <div className="flex flex-1 flex-col gap-0.5">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-500">
            <div
              className="h-full rounded-full bg-primary-500 transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
          <span className="text-[10px] text-gray-400">
            {formatDuration(currentTime)} / {formatDuration(duration)}
          </span>
        </div>

        {/* 隐藏的 audio 元素 */}
        <audio ref={audioRef} src={src} preload="metadata" />
      </div>
    );
  })
);
