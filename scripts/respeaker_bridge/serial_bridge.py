#!/usr/bin/env python3
"""reSpeaker Serial Bridge - COM 串口 → WebSocket 音频转发器。

从 ESP32-S3 USB Serial (CDC) 读取 32-bit/2ch PCM 音频，
转换为 16-bit/1ch 后通过 WebSocket 转发到 LinChat ambient 语音端点。

依赖安装 (Windows):
  pip install pyserial websockets

用法:
  python serial_bridge.py --list                    # 列出 COM 口
  python serial_bridge.py --port COM3 --token TOKEN  # 启动桥接
"""

import argparse
import asyncio
import json
import logging
import struct
import sys
import time
from typing import Optional

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("请安装 pyserial: pip install pyserial")
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
logger = logging.getLogger("serial_bridge")

# 帧同步头（与 ESP32 固件一致）
SYNC_HEADER = bytes([0xAA, 0x55, 0x04, 0x00])
FRAME_SIZE = 1024  # 32-bit/2ch PCM 帧大小（128 samples × 2ch × 4bytes）


def list_com_ports():
    """列出所有可用 COM 口。"""
    ports = serial.tools.list_ports.comports()
    print("\n=== 可用 COM 口 ===\n")
    if not ports:
        print("  未找到 COM 口！")
    for p in ports:
        print(f"  [{p.device}] {p.description}  (VID:PID={p.vid}:{p.pid})")
    print()


def convert_32bit_2ch_to_16bit_1ch(pcm_32bit_2ch: bytes) -> bytes:
    """32-bit/2ch PCM → 16-bit/1ch PCM。

    提取右声道（Ch1 = ASR 波束），32-bit 右移 16 位截断为 16-bit。
    输入: 128 samples × 2ch × 4bytes = 1024 bytes
    输出: 128 samples × 1ch × 2bytes = 256 bytes
    """
    sample_count = len(pcm_32bit_2ch) // 8  # 每个立体声样本 8 bytes
    output = bytearray(sample_count * 2)

    for i in range(sample_count):
        offset = i * 8 + 4  # 右声道偏移（左0-3, 右4-7）
        val_32 = struct.unpack_from("<i", pcm_32bit_2ch, offset)[0]
        val_16 = max(-32768, min(32767, val_32 >> 16))
        struct.pack_into("<h", output, i * 2, val_16)

    return bytes(output)


class SerialBridge:
    """COM 串口音频采集 + WebSocket 转发。"""

    def __init__(self, port: str, ws_url: str, token: str, baud: int = 115200):
        self.port = port
        self.ws_url = ws_url
        self.token = token
        self.baud = baud

        self._running = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._serial: Optional[serial.Serial] = None
        self._audio_queue: asyncio.Queue = asyncio.Queue(maxsize=200)

        # 统计
        self._frames_sent = 0
        self._frames_dropped = 0
        self._bytes_sent = 0
        self._sync_errors = 0
        self._stats_start = 0.0

    def _open_serial(self):
        """打开串口。"""
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            timeout=1,
        )
        # 清空缓冲区
        self._serial.reset_input_buffer()
        logger.info("串口已打开: %s @ %d baud", self.port, self.baud)

    def _close_serial(self):
        """关闭串口。"""
        if self._serial and self._serial.is_open:
            self._serial.close()
            logger.info("串口已关闭")

    def _sync_to_header(self) -> bool:
        """同步到帧头。逐字节读取直到找到 SYNC_HEADER。"""
        buf = bytearray()
        max_search = FRAME_SIZE * 4  # 最多搜索 4 帧的数据量
        searched = 0

        while searched < max_search:
            b = self._serial.read(1)
            if not b:
                return False
            buf.append(b[0])
            searched += 1

            if len(buf) >= 4:
                if buf[-4:] == bytearray(SYNC_HEADER):
                    return True
                if len(buf) > 4:
                    buf = buf[-4:]  # 只保留最后 4 字节

        return False

    def _read_serial_thread(self):
        """串口读取线程（阻塞 IO，在单独线程运行）。"""
        logger.info("正在同步帧头...")
        if not self._sync_to_header():
            logger.error("无法同步帧头，检查固件是否正确")
            return

        logger.info("帧头同步成功，开始接收音频")

        while self._running:
            try:
                # 读取一帧 PCM 数据
                pcm_data = self._serial.read(FRAME_SIZE)
                if len(pcm_data) != FRAME_SIZE:
                    if self._running:
                        logger.warning("帧不完整: %d/%d bytes", len(pcm_data), FRAME_SIZE)
                    continue

                # 读取下一帧的同步头
                header = self._serial.read(4)
                if header != SYNC_HEADER:
                    self._sync_errors += 1
                    if self._sync_errors % 10 == 1:
                        logger.warning("帧同步丢失 (累计 %d 次)，重新同步...", self._sync_errors)
                    if not self._sync_to_header():
                        logger.error("重新同步失败")
                        break
                    continue

                # 转换 32-bit/2ch → 16-bit/1ch
                pcm_16bit = convert_32bit_2ch_to_16bit_1ch(pcm_data)

                try:
                    self._audio_queue.put_nowait(pcm_16bit)
                except asyncio.QueueFull:
                    self._frames_dropped += 1

            except serial.SerialException as e:
                if self._running:
                    logger.error("串口异常: %s", e)
                break
            except Exception as e:
                if self._running:
                    logger.error("读取异常: %s", e)

        logger.info("串口读取线程已退出")

    async def _connect_ws(self) -> bool:
        """连接 WebSocket 并发送 session.configure。"""
        # token 通过 query string 传递（LinChat 设备认证方式）
        sep = "&" if "?" in self.ws_url else "?"
        url = f"{self.ws_url}{sep}token={self.token}"
        try:
            self._ws = await websockets.connect(
                url,
                ping_interval=30,
                ping_timeout=10,
            )
            logger.info("WebSocket 已连接: %s", self.ws_url)

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
                            logger.info("✅ 会话已配置: mode=%s", data.get("mode"))
                        elif evt_type == "transcription.completed":
                            logger.info("🎙️  ASR: \"%s\"", data.get("text", ""))
                        elif evt_type == "aggregation.utterance_added":
                            logger.info("📝 话语添加: buffer=%d", data.get("buffer_count", 0))
                        elif evt_type == "aggregation.completed":
                            logger.info("📦 聚合: \"%s\"", data.get("text", "")[:80])
                        elif evt_type == "decision.result":
                            logger.info("🤖 决策: %s (%s)", data.get("decision"), data.get("reason"))
                        elif evt_type == "response.start":
                            logger.info("💬 Agent 回复中...")
                        elif evt_type == "response.delta":
                            print(data.get("content", ""), end="", flush=True)
                        elif evt_type == "response.end":
                            print()
                            logger.info("💬 回复完成")
                        elif evt_type == "error":
                            logger.error("❌ %s", data.get("message", msg))
                        elif evt_type in ("vad.speech_start", "vad.speech_end"):
                            logger.debug("VAD: %s", evt_type)
                        else:
                            logger.info("📨 %s", evt_type)
                    except json.JSONDecodeError:
                        pass
                else:
                    logger.debug("TTS 音频: %d bytes", len(msg))
        except websockets.ConnectionClosed as e:
            logger.warning("WebSocket 断开: %s", e)
        except Exception as e:
            logger.error("事件接收异常: %s", e)

    async def _forward_audio(self):
        """从队列取 PCM 帧通过 WebSocket 发送。"""
        while self._running:
            try:
                pcm_data = await asyncio.wait_for(self._audio_queue.get(), timeout=1.0)
                if self._ws:
                    await self._ws.send(pcm_data)
                    self._frames_sent += 1
                    self._bytes_sent += len(pcm_data)
            except asyncio.TimeoutError:
                continue
            except websockets.ConnectionClosed:
                logger.warning("发送失败: WebSocket 已断开")
                break
            except Exception as e:
                logger.error("转发异常: %s", e)

    async def _print_stats(self):
        """每 30 秒输出统计。"""
        while self._running:
            await asyncio.sleep(30)
            elapsed = time.monotonic() - self._stats_start
            logger.info("📊 sent=%d, dropped=%d, sync_err=%d, bytes=%s, %.0fs",
                       self._frames_sent, self._frames_dropped,
                       self._sync_errors, f"{self._bytes_sent/1024:.0f}KB", elapsed)

    async def run(self):
        """主运行循环。"""
        self._running = True
        self._stats_start = time.monotonic()

        # 连接 WebSocket
        if not await self._connect_ws():
            return

        # 打开串口
        try:
            self._open_serial()
        except serial.SerialException as e:
            logger.error("无法打开串口 %s: %s", self.port, e)
            return

        # 启动串口读取线程
        import threading
        serial_thread = threading.Thread(target=self._read_serial_thread, daemon=True)
        serial_thread.start()

        # 启动异步任务
        try:
            tasks = [
                asyncio.create_task(self._receive_events()),
                asyncio.create_task(self._forward_audio()),
                asyncio.create_task(self._print_stats()),
            ]
            logger.info("🎤 Serial Bridge 已启动，对着 reSpeaker 说话...")
            logger.info("   按 Ctrl+C 退出")

            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            self._close_serial()
            if self._ws:
                await self._ws.close()
            logger.info("Serial Bridge 已停止")


def main():
    parser = argparse.ArgumentParser(description="reSpeaker Serial Bridge")
    parser.add_argument("--list", action="store_true", help="列出 COM 口")
    parser.add_argument("--port", default=None, help="COM 口 (如 COM3)")
    parser.add_argument("--baud", type=int, default=115200, help="波特率")
    parser.add_argument("--ws-url", default="wss://www.greydan.xin/linchat/ws/voice/",
                       help="LinChat WebSocket URL")
    parser.add_argument("--token", default=None, help="设备 API Token")
    parser.add_argument("--debug", action="store_true", help="DEBUG 日志")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.list or args.port is None:
        list_com_ports()
        if args.port is None:
            print("使用示例:")
            print(f"  python {sys.argv[0]} --port COM3 --token <TOKEN>")
        return

    if not args.token:
        print("错误: 请提供 --token 参数")
        sys.exit(1)

    bridge = SerialBridge(
        port=args.port,
        ws_url=args.ws_url,
        token=args.token,
        baud=args.baud,
    )

    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        logger.info("Ctrl+C，退出...")


if __name__ == "__main__":
    main()
