'use client';

import { memo, useRef, useEffect, useCallback } from 'react';

interface VoiceWaveformProps {
  /** 当前音量级别 (0.0~1.0) */
  volumeLevel: number;
  /** 是否正在录音 */
  isRecording: boolean;
  /** 宽度 (px) */
  width?: number;
  /** 高度 (px) */
  height?: number;
}

/** 历史数组最大长度 */
const MAX_HISTORY = 40;

/** 柱子占可用宽度的比例（其余为间距） */
const BAR_WIDTH_RATIO = 0.6;

/** 柱子最大高度占 canvas 高度的比例 */
const BAR_HEIGHT_RATIO = 0.8;

/** 每帧衰减系数（isRecording=false 时） */
const DECAY_FACTOR = 0.92;

/** 主色调 #3B82F6 的 RGB 分量 */
const PRIMARY_R = 59;
const PRIMARY_G = 130;
const PRIMARY_B = 246;

export const VoiceWaveform = memo(function VoiceWaveform({
  volumeLevel,
  isRecording,
  width = 300,
  height = 120,
}: VoiceWaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const historyRef = useRef<number[]>(new Array(MAX_HISTORY).fill(0));
  const animationFrameRef = useRef<number>(0);
  const isRecordingRef = useRef(isRecording);

  // 保持 isRecording 的最新值在 ref 中，供动画回调读取
  useEffect(() => {
    isRecordingRef.current = isRecording;
  }, [isRecording]);

  // 当 volumeLevel 变化时，将其推入历史数组
  useEffect(() => {
    const history = historyRef.current;

    if (isRecording) {
      // 正常推入新值
      history.push(Math.max(0, Math.min(1, volumeLevel)));
      if (history.length > MAX_HISTORY) {
        history.shift();
      }
    }
  }, [volumeLevel, isRecording]);

  /** 绘制单帧波形 */
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const w = canvas.width / dpr;
    const h = canvas.height / dpr;
    const history = historyRef.current;

    // 清空画布
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 如果不在录音状态，对历史数据做衰减
    if (!isRecordingRef.current) {
      for (let i = 0; i < history.length; i++) {
        history[i] = (history[i] ?? 0) * DECAY_FACTOR;
        if ((history[i] ?? 0) < 0.005) {
          history[i] = 0;
        }
      }
    }

    const barCount = history.length;
    const totalBarWidth = w / barCount;
    const barWidth = totalBarWidth * BAR_WIDTH_RATIO;
    const gap = totalBarWidth * (1 - BAR_WIDTH_RATIO);
    const centerY = h / 2;
    const maxBarHeight = h * BAR_HEIGHT_RATIO * 0.5; // 单侧最大高度

    ctx.save();
    ctx.scale(dpr, dpr);

    for (let i = 0; i < barCount; i++) {
      const vol = history[i] ?? 0;

      // 柱子高度（单侧）——给一个小的最小高度让静默时也有微弱线条
      const barHeight = Math.max(vol * maxBarHeight, 1);

      // 透明度：越新（索引越大）越亮
      const alpha = 0.3 + 0.7 * (i / (barCount - 1));

      const x = i * totalBarWidth + gap / 2;

      // 绘制圆角矩形柱子，从中心向上下对称
      ctx.fillStyle = `rgba(${PRIMARY_R}, ${PRIMARY_G}, ${PRIMARY_B}, ${alpha})`;
      ctx.beginPath();

      const radius = Math.min(barWidth / 2, barHeight, 3);

      // 上半部分
      drawRoundedRect(ctx, x, centerY - barHeight, barWidth, barHeight, radius);
      ctx.fill();

      // 下半部分
      ctx.beginPath();
      drawRoundedRect(ctx, x, centerY, barWidth, barHeight, radius);
      ctx.fill();
    }

    ctx.restore();

    // 持续循环动画
    animationFrameRef.current = requestAnimationFrame(draw);
  }, []);

  // 设置 canvas DPR 并启动动画循环
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    // 启动动画
    animationFrameRef.current = requestAnimationFrame(draw);

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [width, height, draw]);

  return (
    <div
      className="relative flex items-center justify-center rounded-lg bg-gray-900"
      style={{ width, height }}
    >
      <canvas ref={canvasRef} className="block" />

      {/* 录音状态指示器 */}
      {isRecording && (
        <div className="absolute right-3 top-3 flex items-center gap-1.5">
          <div className="h-2.5 w-2.5 animate-pulse rounded-full bg-red-500" />
          <span className="text-xs text-red-400">REC</span>
        </div>
      )}
    </div>
  );
});

/** 绘制圆角矩形路径（不自动 fill/stroke） */
function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  if (h < 1) {
    // 极小高度时直接画矩形
    ctx.rect(x, y, w, h);
    return;
  }

  const clampedR = Math.min(r, w / 2, h / 2);

  ctx.moveTo(x + clampedR, y);
  ctx.lineTo(x + w - clampedR, y);
  ctx.arcTo(x + w, y, x + w, y + clampedR, clampedR);
  ctx.lineTo(x + w, y + h - clampedR);
  ctx.arcTo(x + w, y + h, x + w - clampedR, y + h, clampedR);
  ctx.lineTo(x + clampedR, y + h);
  ctx.arcTo(x, y + h, x, y + h - clampedR, clampedR);
  ctx.lineTo(x, y + clampedR);
  ctx.arcTo(x, y, x + clampedR, y, clampedR);
  ctx.closePath();
}
