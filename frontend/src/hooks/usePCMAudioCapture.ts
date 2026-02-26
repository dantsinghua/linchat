/**
 * AudioWorklet PCM16 音频采集 Hook (T020)
 *
 * 基于 AudioWorklet 实现低延迟 PCM16 音频采集：
 * - 固定采样率 16000Hz，每帧 480 samples（30ms）
 * - 输出 Int16Array PCM16 帧数据（960 bytes/帧）
 * - 实时 RMS 音量计算（归一化到 0.0~1.0）
 * - 30 秒最大录音时长自动停止（FR-007）
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/** PCM 采集配置选项 */
interface UsePCMAudioCaptureOptions {
  /** PCM16 帧回调，每 30ms 触发一次，传入 960 bytes ArrayBuffer */
  onAudioData?: (pcmData: ArrayBuffer) => void;
  /** 音量级别回调，归一化到 0.0~1.0，供波形显示组件使用 */
  onVolumeLevel?: (level: number) => void;
  /** 最大录音时长（秒），默认 30 秒 */
  maxDuration?: number;
}

/** PCM 采集返回值 */
interface UsePCMAudioCaptureReturn {
  /** 是否正在采集 */
  isCapturing: boolean;
  /** 已录制秒数 */
  duration: number;
  /** 错误信息 */
  error: string | null;
  /** 开始采集 */
  startCapture: () => Promise<void>;
  /** 停止采集 */
  stopCapture: () => void;
}

/** 默认最大录音时长（秒） */
const DEFAULT_MAX_DURATION = 30;

/** 采样率 */
const SAMPLE_RATE = 16000;

/**
 * AudioWorklet Processor 内联代码
 *
 * 每帧累积 480 个 Float32 样本（30ms @ 16000Hz），
 * 转换为 Int16Array 后通过 MessagePort 发送给主线程。
 * 同时计算 RMS 音量并一并发送。
 */
const WORKLET_PROCESSOR_CODE = `
class PCMCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(480);
    this._bufferIndex = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) {
      return true;
    }

    const channelData = input[0];
    let i = 0;

    while (i < channelData.length) {
      const remaining = 480 - this._bufferIndex;
      const toCopy = Math.min(remaining, channelData.length - i);

      this._buffer.set(channelData.subarray(i, i + toCopy), this._bufferIndex);
      this._bufferIndex += toCopy;
      i += toCopy;

      if (this._bufferIndex >= 480) {
        // 计算 RMS 音量
        let sumSquares = 0;
        for (let s = 0; s < 480; s++) {
          sumSquares += this._buffer[s] * this._buffer[s];
        }
        const rms = Math.sqrt(sumSquares / 480);
        // 归一化到 0.0~1.0（Float32 音频范围 -1.0~1.0，RMS 最大值为 1.0）
        const volume = Math.min(1.0, rms);

        // Float32 转 Int16
        const pcm16 = new Int16Array(480);
        for (let s = 0; s < 480; s++) {
          const sample = Math.max(-1, Math.min(1, this._buffer[s]));
          pcm16[s] = sample * 0x7FFF;
        }

        this.port.postMessage({
          pcmData: pcm16.buffer,
          volume: volume,
        }, [pcm16.buffer]);

        this._bufferIndex = 0;
      }
    }

    return true;
  }
}

registerProcessor('pcm-capture-processor', PCMCaptureProcessor);
`;

/**
 * AudioWorklet PCM16 音频采集 Hook
 *
 * @param options - 采集配置选项
 * @returns 采集控制接口
 */
export function usePCMAudioCapture(
  options: UsePCMAudioCaptureOptions = {},
): UsePCMAudioCaptureReturn {
  const { onAudioData, onVolumeLevel, maxDuration = DEFAULT_MAX_DURATION } = options;

  const [isCapturing, setIsCapturing] = useState(false);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // Refs 保存回调，避免闭包过期问题
  const onAudioDataRef = useRef(onAudioData);
  const onVolumeLevelRef = useRef(onVolumeLevel);
  const maxDurationRef = useRef(maxDuration);

  // 资源 Refs
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const sourceNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const startTimeRef = useRef<number>(0);
  const workletBlobUrlRef = useRef<string | null>(null);
  const isCapturingRef = useRef(false);

  // 同步更新回调 Refs
  useEffect(() => {
    onAudioDataRef.current = onAudioData;
  }, [onAudioData]);

  useEffect(() => {
    onVolumeLevelRef.current = onVolumeLevel;
  }, [onVolumeLevel]);

  useEffect(() => {
    maxDurationRef.current = maxDuration;
  }, [maxDuration]);

  /** 清理计时器 */
  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  /** 释放所有音频资源 */
  const releaseResources = useCallback(() => {
    clearTimer();

    // 断开 WorkletNode
    if (workletNodeRef.current) {
      workletNodeRef.current.port.onmessage = null;
      workletNodeRef.current.disconnect();
      workletNodeRef.current = null;
    }

    // 断开 SourceNode
    if (sourceNodeRef.current) {
      sourceNodeRef.current.disconnect();
      sourceNodeRef.current = null;
    }

    // 停止所有音轨
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }

    // 关闭 AudioContext
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => {
        // 忽略关闭错误
      });
      audioContextRef.current = null;
    }

    // 释放 Blob URL
    if (workletBlobUrlRef.current) {
      URL.revokeObjectURL(workletBlobUrlRef.current);
      workletBlobUrlRef.current = null;
    }
  }, [clearTimer]);

  /** 停止采集 */
  const stopCapture = useCallback(() => {
    if (!isCapturingRef.current) {
      return;
    }

    isCapturingRef.current = false;
    releaseResources();
    setIsCapturing(false);
  }, [releaseResources]);

  /** 开始采集 */
  const startCapture = useCallback(async () => {
    // 防止重复启动
    if (isCapturingRef.current) {
      return;
    }

    setError(null);
    setDuration(0);

    try {
      // 1. 请求麦克风权限
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: SAMPLE_RATE,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      mediaStreamRef.current = stream;

      // 2. 创建 AudioContext（固定 16000Hz 采样率）
      const audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
      audioContextRef.current = audioContext;

      // 3. 创建 Blob URL 并注册 AudioWorklet Processor
      const blob = new Blob([WORKLET_PROCESSOR_CODE], { type: 'application/javascript' });
      const blobUrl = URL.createObjectURL(blob);
      workletBlobUrlRef.current = blobUrl;

      await audioContext.audioWorklet.addModule(blobUrl);

      // 4. 创建 AudioWorkletNode
      const workletNode = new AudioWorkletNode(audioContext, 'pcm-capture-processor');
      workletNodeRef.current = workletNode;

      // 5. 监听 Processor 发来的 PCM 帧和音量数据
      workletNode.port.onmessage = (event: MessageEvent) => {
        const { pcmData, volume } = event.data;

        if (onAudioDataRef.current && pcmData) {
          onAudioDataRef.current(pcmData);
        }

        if (onVolumeLevelRef.current && typeof volume === 'number') {
          onVolumeLevelRef.current(volume);
        }
      };

      // 6. 连接音频处理链路：麦克风 → WorkletNode
      const sourceNode = audioContext.createMediaStreamSource(stream);
      sourceNodeRef.current = sourceNode;
      sourceNode.connect(workletNode);
      // 不连接到 destination，避免回放采集音频

      // 7. 标记采集状态
      isCapturingRef.current = true;
      setIsCapturing(true);
      startTimeRef.current = Date.now();

      // 8. 启动时长计时器（每秒更新）
      timerRef.current = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTimeRef.current) / 1000);
        setDuration(elapsed);

        // 到达最大录音时长自动停止（FR-007）
        if (elapsed >= maxDurationRef.current) {
          stopCapture();
        }
      }, 1000);
    } catch {
      // 权限拒绝或设备不可用
      releaseResources();
      setError('无法访问麦克风，请检查权限设置');
      setIsCapturing(false);
      isCapturingRef.current = false;
    }
  }, [stopCapture, releaseResources]);

  // 组件卸载时清理所有资源
  useEffect(() => {
    return () => {
      if (isCapturingRef.current) {
        isCapturingRef.current = false;
        releaseResources();
      }
    };
  }, [releaseResources]);

  return {
    isCapturing,
    duration,
    error,
    startCapture,
    stopCapture,
  };
}
