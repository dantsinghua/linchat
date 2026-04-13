#!/usr/bin/env python3
"""reSpeaker USB Bridge - Windows USB 麦克风 → WebSocket 音频转发器。

通过 USB 连接 reSpeaker 设备（识别为 USB 麦克风），采集 PCM 音频，
通过 WebSocket 转发到 LinChat ambient 语音端点。

依赖安装 (Windows):
  pip install pyaudio websockets

用法:
  python usb_bridge.py                           # 列出音频设备
  python usb_bridge.py --device 1 --ws-url wss://www.greydan.xin/linchat/ws/voice/ --token YOUR_TOKEN
  python usb_bridge.py --device 1 --ws-url ws://localhost:8002/ws/voice/ --token YOUR_TOKEN

参数:
  --device      音频设备索引（先不带此参数运行查看设备列表）
  --ws-url      LinChat WebSocket 地址
  --token       设备 API Token（通过 POST /api/v1/voice/devices/ 获取）
  --sample-rate 采样率（默认 16000）
  --channels    声道数（默认 1，reSpeaker XVF3800 USB 模式输出处理后的单声道）
  --chunk-size  每帧采样数（默认 1600，即 100ms@16kHz）
"""

import argparse
import asyncio
import json
import logging
import signal
import struct
import sys
import threading
import time
from typing import Optional

try:
    import pyaudio
except ImportError:
    print("请安装 pyaudio: pip install pyaudio")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("请安装 websockets: pip install websockets")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("usb_bridge")


def list_audio_devices():
    """列出所有可用的音频输入设备。"""
    pa = pyaudio.PyAudio()
    print("\n=== 可用音频输入设备 ===\n")
    found = False
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0:
            found = True
            name = info["name"]
            rate = int(info["defaultSampleRate"])
            ch = info["maxInputChannels"]
            print(f"  [{i}] {name}  (channels={ch}, rate={rate}Hz)")
    if not found:
        print("  未找到音频输入设备！")
    print()
    pa.terminate()


class USBBridge:
    """USB 麦克风音频采集 + WebSocket 转发。"""

    def __init__(self, device_index: int, ws_url: str, token: str,
                 sample_rate: int = 16000, channels: int = 1, chunk_size: int = 1600):
        self.device_index = device_index
        self.ws_url = ws_url
        self.token = token
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size  # samples per frame

        self._running = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._audio_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream = None

        # 统计
        self._frames_sent = 0
        self._frames_dropped = 0
        self._bytes_sent = 0
        self._stats_start = 0.0

    def _audio_callback(self, in_data, frame_count, time_info, status):
        """PyAudio 回调（在单独线程中运行）。"""
        if status:
            logger.warning("PyAudio status: %s", status)
        if self._running and in_data:
            try:
                self._audio_queue.put_nowait(in_data)
            except asyncio.QueueFull:
                self._frames_dropped += 1
        return (None, pyaudio.paContinue)

    def _start_audio_capture(self):
        """启动 PyAudio 音频采集。"""
        self._pa = pyaudio.PyAudio()
        info = self._pa.get_device_info_by_index(self.device_index)
        logger.info("打开音频设备: [%d] %s", self.device_index, info["name"])

        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=self.device_index,
            frames_per_buffer=self.chunk_size,
            stream_callback=self._audio_callback,
        )
        self._stream.start_stream()
        logger.info("音频采集已启动: %dHz, %dch, %d samples/frame",
                     self.sample_rate, self.channels, self.chunk_size)

    def _stop_audio_capture(self):
        """停止音频采集。"""
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._pa:
            self._pa.terminate()
            self._pa = None
        logger.info("音频采集已停止")

    async def _connect_ws(self) -> bool:
        """连接 WebSocket 并发送 session.configure。"""
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            self._ws = await websockets.connect(
                self.ws_url,
                additional_headers=headers,
                ping_interval=30,
                ping_timeout=10,
            )
            logger.info("WebSocket 已连接: %s", self.ws_url)

            # 发送 ambient 模式配置
            await self._ws.send(json.dumps({
                "type": "session.configure",
                "data": {"mode": "ambient"}
            }))
            logger.info("已发送 session.configure (ambient)")
            return True
        except Exception as e:
            logger.error("WebSocket 连接失败: %s", e)
            return False

    async def _receive_events(self):
        """接收并打印 LinChat 事件。"""
        try:
            async for msg in self._ws:
                if isinstance(msg, str):
                    try:
                        event = json.loads(msg)
                        evt_type = event.get("type", "unknown")
                        data = event.get("data", {})

                        if evt_type == "session.configured":
                            logger.info("✅ 会话已配置: mode=%s, features=%s",
                                       data.get("mode"), data.get("features"))
                        elif evt_type == "transcription.completed":
                            text = data.get("text", "")
                            logger.info("🎙️  ASR 转录: \"%s\"", text)
                        elif evt_type == "aggregation.utterance_added":
                            logger.info("📝 话语已添加: buffer=%d, timeout=%.1fs",
                                       data.get("buffer_count", 0), data.get("timeout_remaining", 0))
                        elif evt_type == "aggregation.completed":
                            logger.info("📦 聚合完成: \"%s\"", data.get("text", "")[:100])
                        elif evt_type == "decision.result":
                            decision = data.get("decision", "")
                            reason = data.get("reason", "")
                            logger.info("🤖 决策结果: %s (reason=%s)", decision, reason)
                        elif evt_type == "response.start":
                            logger.info("💬 Agent 开始回复...")
                        elif evt_type == "response.delta":
                            content = data.get("content", "")
                            print(content, end="", flush=True)
                        elif evt_type == "response.end":
                            print()  # 换行
                            logger.info("💬 Agent 回复完成")
                        elif evt_type == "error":
                            logger.error("❌ 错误: %s", data.get("message", msg))
                        elif evt_type in ("vad.speech_start", "vad.speech_end"):
                            logger.debug("VAD: %s", evt_type)
                        else:
                            logger.info("📨 事件: %s", evt_type)
                    except json.JSONDecodeError:
                        logger.warning("非 JSON 消息: %s", msg[:100])
                else:
                    # 二进制帧（TTS 音频）— USB 模式下忽略
                    logger.debug("收到 TTS 音频帧: %d bytes", len(msg))
        except websockets.ConnectionClosed as e:
            logger.warning("WebSocket 断开: code=%s, reason=%s", e.code, e.reason)
        except Exception as e:
            logger.error("事件接收异常: %s", e)

    async def _forward_audio(self):
        """从队列取 PCM 帧并通过 WebSocket 发送。"""
        while self._running:
            try:
                pcm_data = await asyncio.wait_for(self._audio_queue.get(), timeout=1.0)
                if self._ws and self._ws.open:
                    await self._ws.send(pcm_data)
                    self._frames_sent += 1
                    self._bytes_sent += len(pcm_data)
            except asyncio.TimeoutError:
                continue
            except websockets.ConnectionClosed:
                logger.warning("发送失败: WebSocket 已断开")
                break
            except Exception as e:
                logger.error("音频转发异常: %s", e)

    async def _print_stats(self):
        """每 60 秒输出统计信息。"""
        while self._running:
            await asyncio.sleep(60)
            elapsed = time.monotonic() - self._stats_start
            if elapsed > 0:
                logger.info("📊 统计: sent=%d, dropped=%d, bytes=%s, elapsed=%.0fs",
                           self._frames_sent, self._frames_dropped,
                           f"{self._bytes_sent / 1024:.0f}KB", elapsed)

    async def run(self):
        """主运行循环。"""
        self._running = True
        self._stats_start = time.monotonic()

        # 连接 WebSocket
        if not await self._connect_ws():
            logger.error("无法连接 WebSocket，退出")
            return

        # 启动音频采集
        self._start_audio_capture()

        # 启动并发任务
        try:
            tasks = [
                asyncio.create_task(self._receive_events()),
                asyncio.create_task(self._forward_audio()),
                asyncio.create_task(self._print_stats()),
            ]
            logger.info("🎤 USB Bridge 已启动，对着麦克风说话...")
            logger.info("   按 Ctrl+C 退出")

            # 等待任何一个任务结束
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            self._stop_audio_capture()
            if self._ws:
                await self._ws.close()
            logger.info("USB Bridge 已停止")


def main():
    parser = argparse.ArgumentParser(description="reSpeaker USB Bridge")
    parser.add_argument("--device", type=int, default=None, help="音频设备索引")
    parser.add_argument("--ws-url", default="wss://www.greydan.xin/linchat/ws/voice/",
                       help="LinChat WebSocket URL")
    parser.add_argument("--token", default=None, help="设备 API Token")
    parser.add_argument("--sample-rate", type=int, default=16000, help="采样率")
    parser.add_argument("--channels", type=int, default=1, help="声道数")
    parser.add_argument("--chunk-size", type=int, default=1600, help="每帧采样数")
    parser.add_argument("--debug", action="store_true", help="启用 DEBUG 日志")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # 无 --device 参数时列出设备
    if args.device is None:
        list_audio_devices()
        print("使用示例:")
        print(f"  python {sys.argv[0]} --device <INDEX> --token <TOKEN>")
        print(f"  python {sys.argv[0]} --device <INDEX> --token <TOKEN> --ws-url ws://localhost:8002/ws/voice/")
        return

    if not args.token:
        print("错误: 请提供 --token 参数（通过 POST /api/v1/voice/devices/ 获取）")
        sys.exit(1)

    bridge = USBBridge(
        device_index=args.device,
        ws_url=args.ws_url,
        token=args.token,
        sample_rate=args.sample_rate,
        channels=args.channels,
        chunk_size=args.chunk_size,
    )

    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在退出...")


if __name__ == "__main__":
    main()
