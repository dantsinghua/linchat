#!/usr/bin/env python3
"""reSpeaker XVF3800 WiFi Bridge Service - UDP->WebSocket 音频转发器。

接收 ESP32 通过 UDP 发送的 32-bit/2ch PCM 音频数据，
转换为 16-bit/1ch 后通过 WebSocket 转发到 LinChat 语音端点。

架构:
  ESP32 (UDP 16kHz/32-bit/2ch) -> AudioConverter -> Queue -> WebSocket -> LinChat

用法:
  python bridge.py                     # 使用 .env 配置
  DEVICE_TOKEN=xxx python bridge.py    # 环境变量配置
"""

import asyncio
import json
import logging
import signal
import sys
import time
from typing import Optional

import websockets

from audio_converter import AudioConverter
from config import BridgeConfig

logger = logging.getLogger("respeaker_bridge")


class _UDPProtocol(asyncio.DatagramProtocol):
    """UDP 数据报接收协议，将音频帧放入异步队列。"""

    def __init__(self, bridge: "ReSpeakerBridge") -> None:
        self._bridge = bridge

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """收到 UDP 数据包时回调。

        仅在 WebSocket 已连接时入队，否则静默丢弃。
        """
        self._bridge._last_udp_time = time.monotonic()
        if self._bridge._udp_interrupted:
            self._bridge._udp_interrupted = False
            logger.info("UDP 数据流已恢复")
        if self._bridge.ws_connected:
            try:
                self._bridge.audio_queue.put_nowait(data)
            except asyncio.QueueFull:
                # 队列满时丢弃最旧的帧，避免延迟累积
                try:
                    self._bridge.audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self._bridge.audio_queue.put_nowait(data)

    def error_received(self, exc: Exception) -> None:
        logger.warning("UDP 接收错误: %s", exc)


class ReSpeakerBridge:
    """reSpeaker WiFi 桥接服务主类。

    负责:
    1. 启动 UDP 服务器接收 ESP32 音频帧
    2. 连接 LinChat WebSocket 语音端点
    3. 音频格式转换（32-bit/2ch -> 16-bit/1ch）
    4. 通过 WebSocket 转发音频 + 接收事件
    5. 定时帧统计日志
    """

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.converter = AudioConverter()
        self.audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
        self.ws_connected: bool = False
        self._ws: Optional[websockets.ClientConnection] = None
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._tasks: list[asyncio.Task] = []
        self._running: bool = False

        # 帧统计
        self._stats_frames: int = 0
        self._stats_bytes: int = 0
        self._stats_latency_sum: float = 0.0
        self._stats_last_time: float = 0.0

        # UDP 流中断检测 (FR-006)
        self._last_udp_time: float = 0.0
        self._udp_interrupted: bool = False

        # WS 重连参数 (FR-005)
        self._RECONNECT_INTERVALS: list[int] = [3, 6, 9, 12, 15]
        self._RECONNECT_RESET_WAIT: int = 60

    async def run(self) -> None:
        """主入口，启动 UDP 服务器和 WebSocket 客户端。"""
        self._running = True
        self._stats_last_time = time.monotonic()

        # 注册信号处理（优雅退出）
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._shutdown(s)))

        logger.info(
            "桥接服务启动: UDP=%d, WS=%s, LOG=%s",
            self.config.UDP_PORT,
            self.config.WS_URL,
            self.config.LOG_LEVEL,
        )

        # 启动 UDP 服务器
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _UDPProtocol(self),
            local_addr=("0.0.0.0", self.config.UDP_PORT),
        )
        self._transport = transport
        logger.info("UDP 服务器已绑定: 0.0.0.0:%d", self.config.UDP_PORT)

        # 启动并发任务
        self._tasks = [
            asyncio.create_task(self._ws_connection_loop(), name="ws_connection"),
            asyncio.create_task(self._audio_forward_loop(), name="audio_forward"),
            asyncio.create_task(self._stats_loop(), name="stats"),
            asyncio.create_task(self._udp_watchdog_loop(), name="udp_watchdog"),
        ]

        try:
            # 等待所有任务完成（正常情况下不会返回）
            await asyncio.gather(*self._tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        finally:
            await self._cleanup()

    async def _ws_connection_loop(self) -> None:
        """WebSocket 连接循环，断线自动重连（FR-005）。

        重连策略：线性递增间隔 3/6/9/12/15 秒，5 次全部失败后
        等待 60 秒重置计数器，重新开始（无限循环，不退出进程）。
        """
        ws_url = f"{self.config.WS_URL}?token={self.config.DEVICE_TOKEN}"

        while self._running:
            try:
                logger.info("正在连接 WebSocket: %s", self.config.WS_URL)
                async with websockets.connect(
                    ws_url,
                    max_size=1024 * 1024,  # 1MB
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self.ws_connected = True
                    logger.info("WebSocket 已连接")

                    # 发送 session.configure 进入 ambient 模式
                    configure_msg = json.dumps({
                        "type": "session.configure",
                        "data": {"mode": "ambient"},
                    })
                    await ws.send(configure_msg)
                    logger.info("已发送 session.configure (mode=ambient)")

                    # 接收事件循环（正常连接期间阻塞在此）
                    await self._ws_receive_loop(ws)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning("WebSocket 连接关闭: code=%s, reason=%s", e.code, e.reason)
            except OSError as e:
                logger.error("WebSocket 连接失败: %s", e)
            except Exception as e:
                logger.error("WebSocket 异常: %s", e, exc_info=True)
            finally:
                self.ws_connected = False
                self._ws = None

            if not self._running:
                break

            # FR-005: 线性递增重连 3/6/9/12/15s，5 次全失败后等 60s 重置
            reconnected = False
            for attempt, delay in enumerate(self._RECONNECT_INTERVALS, 1):
                if not self._running:
                    return
                logger.info("重连尝试 %d/5，%d 秒后重试...", attempt, delay)
                await asyncio.sleep(delay)
                if not self._running:
                    return
                try:
                    ws = await websockets.connect(
                        ws_url, max_size=1024 * 1024, close_timeout=5,
                    )
                    self._ws = ws
                    self.ws_connected = True
                    logger.info("WebSocket 重连成功 (尝试 %d/5)", attempt)
                    # 重连后重发 session.configure 恢复 ambient 会话
                    await ws.send(json.dumps({
                        "type": "session.configure",
                        "data": {"mode": "ambient"},
                    }))
                    logger.info("重连后已重发 session.configure (mode=ambient)")
                    reconnected = True
                    # 进入接收循环，断开后回到外层 while
                    try:
                        await self._ws_receive_loop(ws)
                    except websockets.exceptions.ConnectionClosed as e:
                        logger.warning("WebSocket 连接关闭: code=%s, reason=%s", e.code, e.reason)
                    except Exception as e:
                        logger.error("WebSocket 异常: %s", e, exc_info=True)
                    finally:
                        self.ws_connected = False
                        self._ws = None
                    break
                except Exception as e:
                    logger.warning("重连尝试 %d/5 失败: %s", attempt, e)

            if reconnected:
                continue  # 回到外层 while 重新开始连接循环

            if not self._running:
                break
            # 5 次全部失败，等待 60 秒后重置计数器重新开始
            logger.error("5 次重连全部失败，%d 秒后重置计数器重试", self._RECONNECT_RESET_WAIT)
            await asyncio.sleep(self._RECONNECT_RESET_WAIT)

    async def _ws_receive_loop(self, ws: websockets.ClientConnection) -> None:
        """接收并记录 WebSocket JSON 事件。"""
        async for message in ws:
            if isinstance(message, str):
                try:
                    event = json.loads(message)
                    event_type = event.get("type", "unknown")
                    event_data = event.get("data", {})

                    if event_type == "session.configured":
                        logger.info(
                            "会话已配置: mode=%s, session_id=%s",
                            event_data.get("mode"),
                            event_data.get("session_id"),
                        )
                    elif event_type == "transcription.completed":
                        logger.info("转录完成: %s", event_data.get("text", "")[:100])
                    elif event_type == "aggregation.completed":
                        logger.info(
                            "聚合完成: text=%s, count=%s",
                            event_data.get("aggregated_text", "")[:100],
                            event_data.get("utterance_count"),
                        )
                    elif event_type == "decision.result":
                        logger.info(
                            "决策结果: decision=%s, reason=%s",
                            event_data.get("decision"),
                            event_data.get("reason"),
                        )
                    elif event_type == "error":
                        logger.error(
                            "服务端错误: code=%s, message=%s",
                            event_data.get("code"),
                            event_data.get("message"),
                        )
                    else:
                        logger.debug("收到事件: type=%s", event_type)

                except json.JSONDecodeError:
                    logger.warning("收到非 JSON 文本消息: %s", message[:100])
            else:
                # 二进制消息（如 TTS 音频），桥接服务忽略
                logger.debug("收到二进制消息: %d 字节", len(message))

    async def _audio_forward_loop(self) -> None:
        """从队列取出音频帧，转换后通过 WebSocket 发送。"""
        while self._running:
            try:
                raw_data = await asyncio.wait_for(
                    self.audio_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            if not self.ws_connected or self._ws is None:
                continue

            t0 = time.monotonic()
            converted = self.converter.convert(raw_data)
            if converted is None:
                # 首包校验失败，跳过
                continue

            try:
                await self._ws.send(converted)
                elapsed = time.monotonic() - t0
                self._stats_frames += 1
                self._stats_bytes += len(converted)
                self._stats_latency_sum += elapsed
            except Exception as e:
                logger.warning("音频帧发送失败: %s", e)

    async def _udp_watchdog_loop(self) -> None:
        """UDP 流中断检测（FR-006）：30 秒无数据记录 WARNING，恢复时记录 INFO。"""
        while self._running:
            await asyncio.sleep(5)  # 每 5 秒检查一次
            if self._last_udp_time > 0 and not self._udp_interrupted:
                elapsed = time.monotonic() - self._last_udp_time
                if elapsed >= 30:
                    self._udp_interrupted = True
                    logger.warning("UDP 数据流中断: 已 %.0f 秒无数据", elapsed)

    async def _stats_loop(self) -> None:
        """每 60 秒输出帧统计日志。"""
        while self._running:
            await asyncio.sleep(60)
            if self._stats_frames > 0:
                avg_latency_ms = (
                    self._stats_latency_sum / self._stats_frames * 1000
                )
                logger.info(
                    "帧统计: frames=%d, bytes=%d, avg_latency=%.2fms",
                    self._stats_frames,
                    self._stats_bytes,
                    avg_latency_ms,
                )
            else:
                logger.info("帧统计: frames=0, bytes=0, avg_latency=0.00ms")

            # 重置统计
            self._stats_frames = 0
            self._stats_bytes = 0
            self._stats_latency_sum = 0.0

    async def _shutdown(self, sig: signal.Signals) -> None:
        """优雅关闭：关闭 WS -> 停止 UDP -> 取消任务。"""
        logger.info("收到信号 %s，正在关闭...", sig.name)
        self._running = False

        # 关闭 WebSocket
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

        # 取消所有后台任务
        for task in self._tasks:
            if not task.done():
                task.cancel()

    async def _cleanup(self) -> None:
        """清理资源。"""
        if self._transport:
            self._transport.close()
            logger.info("UDP 服务器已关闭")

        logger.info("桥接服务已停止")


def main() -> None:
    """入口函数。"""
    # 加载配置
    config = BridgeConfig.load()

    # 配置日志
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # 启动桥接服务
    bridge = ReSpeakerBridge(config)
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
