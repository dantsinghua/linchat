"""reSpeaker Bridge 全模块单元测试。

覆盖:
  - audio_converter: 格式转换、通道提取、首包校验
  - config: 默认值、.env 覆盖、缺失 Token 校验
  - bridge: UDP 接收转发、WS 事件接收、session.configure、WS 断连丢帧
"""

import asyncio
import json
import os
import struct
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import websockets

import pytest

# 将 bridge 包目录加入 sys.path，使模块可直接导入
_bridge_dir = str(Path(__file__).resolve().parent.parent)
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)


class _AsyncIterFromList:
    """将列表包装为异步迭代器，用于模拟 websockets async for 消息接收。"""

    def __init__(self, items: list) -> None:
        self._items = items
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


class _MockWS(_AsyncIterFromList):
    """模拟 websockets 连接：支持 async for 迭代 + send() 调用记录。"""

    def __init__(self, items: list | None = None) -> None:
        super().__init__(items or [])
        self.send = AsyncMock()
        self.close = AsyncMock()


from audio_converter import (
    EXPECTED_FRAME_SIZE,
    AudioConverter,
    convert_frame,
)
from config import BridgeConfig, _parse_env_file


# ========== audio_converter 测试 ==========


class TestConvertFrame:
    """convert_frame() 纯函数测试。"""

    def _make_stereo_frame(
        self, left_values: list[int], right_values: list[int]
    ) -> bytes:
        """构造 32-bit/2ch 交错立体声帧。

        left_values 和 right_values 长度必须相同（= 样本数）。
        交错顺序: L0, R0, L1, R1, ...
        """
        assert len(left_values) == len(right_values)
        samples = []
        for l_val, r_val in zip(left_values, right_values):
            samples.append(l_val)
            samples.append(r_val)
        return struct.pack(f"<{len(samples)}i", *samples)

    def test_convert_known_data(self) -> None:
        """已知数据转换: 32-bit/2ch -> 16-bit/1ch 右声道。"""
        # 构造 128 样本的立体声帧
        num_samples = 128
        # 左声道全 0，右声道为 i * 65536（右移 16 位后得到 i）
        left = [0] * num_samples
        right = [i * 65536 for i in range(num_samples)]  # 高 16 位 = i

        raw = self._make_stereo_frame(left, right)
        assert len(raw) == EXPECTED_FRAME_SIZE  # 1024 字节

        result = convert_frame(raw)
        assert result is not None

        # 解包验证：应得到 128 个 16-bit 值 = i
        values = struct.unpack(f"<{num_samples}h", result)
        for i in range(num_samples):
            assert values[i] == i, f"样本 {i}: 期望 {i}, 实际 {values[i]}"

    def test_right_channel_extraction(self) -> None:
        """验证提取的是右声道（奇数索引），而非左声道。"""
        num_samples = 128
        # 左声道 = 1000 * 65536，右声道 = -500 * 65536
        left = [1000 * 65536] * num_samples
        right = [-500 * 65536] * num_samples

        raw = self._make_stereo_frame(left, right)
        result = convert_frame(raw)
        assert result is not None

        values = struct.unpack(f"<{num_samples}h", result)
        # 所有值应为 -500（右声道），而非 1000（左声道）
        for i, val in enumerate(values):
            assert val == -500, f"样本 {i}: 期望 -500, 实际 {val}"

    def test_output_size(self) -> None:
        """输出大小: 128 样本 x 2 字节 = 256 字节。"""
        raw = b"\x00" * EXPECTED_FRAME_SIZE
        result = convert_frame(raw)
        assert result is not None
        assert len(result) == 128 * 2  # 256 字节

    def test_clamp_to_int16_range(self) -> None:
        """32-bit 值超出 int16 范围时应被钳位。"""
        # 构造一个超出 int16 范围的右声道值
        # 0x7FFFFFFF >> 16 = 32767（刚好在范围内）
        num_samples = 4
        left = [0] * num_samples
        right = [0x7FFFFFFF, -0x80000000, 0x00010000, -0x00010000]

        raw = self._make_stereo_frame(left, right)
        result = convert_frame(raw)
        assert result is not None

        values = struct.unpack(f"<{num_samples}h", result)
        assert values[0] == 32767   # 0x7FFFFFFF >> 16 = 32767
        assert values[1] == -32768  # -0x80000000 >> 16 = -32768
        assert values[2] == 1       # 0x00010000 >> 16 = 1
        assert values[3] == -1      # -0x00010000 >> 16 = -1


class TestAudioConverter:
    """AudioConverter 类（带首包校验）测试。"""

    def test_first_frame_validation_pass(self) -> None:
        """首包 1024 字节校验通过。"""
        converter = AudioConverter()
        raw = b"\x00" * EXPECTED_FRAME_SIZE
        result = converter.convert(raw)
        assert result is not None
        assert converter._validated is True

    def test_first_frame_validation_fail_wrong_size(self) -> None:
        """首包大小不匹配（512 字节）校验失败，返回 None。"""
        converter = AudioConverter()
        raw = b"\x00" * 512
        result = converter.convert(raw)
        assert result is None
        assert converter._validated is False

    def test_subsequent_frames_skip_validation(self) -> None:
        """首包校验通过后，后续帧不再校验大小。"""
        converter = AudioConverter()

        # 首包 1024 字节，通过校验
        first = b"\x00" * EXPECTED_FRAME_SIZE
        result1 = converter.convert(first)
        assert result1 is not None
        assert converter._validated is True

        # 后续帧（即使大小不同）也能正常处理
        # 注意: 这里传 512 字节（64 样本 x 2ch x 4B），convert_frame 仍能处理
        second = b"\x00" * 512
        result2 = converter.convert(second)
        assert result2 is not None  # 不会返回 None，因为已跳过校验


# ========== config 测试 ==========


class TestParseEnvFile:
    """_parse_env_file() 测试。"""

    def test_parse_basic(self, tmp_path: Path) -> None:
        """基本键值对解析。"""
        env_file = tmp_path / ".env"
        env_file.write_text('KEY1=value1\nKEY2="value2"\nKEY3=\'value3\'\n')

        result = _parse_env_file(env_file)
        assert result == {"KEY1": "value1", "KEY2": "value2", "KEY3": "value3"}

    def test_skip_comments_and_empty(self, tmp_path: Path) -> None:
        """跳过注释和空行。"""
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nKEY=val\n  \n")

        result = _parse_env_file(env_file)
        assert result == {"KEY": "val"}

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """文件不存在返回空字典。"""
        result = _parse_env_file(tmp_path / "nonexistent")
        assert result == {}

    def test_value_with_equals(self, tmp_path: Path) -> None:
        """值中包含 = 号。"""
        env_file = tmp_path / ".env"
        env_file.write_text("URL=ws://host:8002/path?a=1&b=2\n")

        result = _parse_env_file(env_file)
        assert result == {"URL": "ws://host:8002/path?a=1&b=2"}


class TestBridgeConfig:
    """BridgeConfig.load() 测试。"""

    def test_defaults_with_token_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量设置 DEVICE_TOKEN，其余使用默认值。"""
        monkeypatch.setenv("DEVICE_TOKEN", "test-token-123")
        # 确保不读取真实 .env 文件
        monkeypatch.setattr(
            "config.Path.__truediv__",
            lambda self, other: Path("/nonexistent/.env"),
        )

        config = BridgeConfig.load()
        assert config.DEVICE_TOKEN == "test-token-123"
        assert config.UDP_PORT == 12345
        assert config.WS_URL == "ws://localhost:8002/ws/voice/"
        assert config.LOG_LEVEL == "INFO"

    def test_env_file_overrides(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """.env 文件覆盖默认值。"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            'UDP_PORT=54321\n'
            'WS_URL="ws://192.168.3.119:8002/ws/voice/"\n'
            'DEVICE_TOKEN=file-token-abc\n'
            'LOG_LEVEL=DEBUG\n'
        )
        # 清除环境变量中可能存在的值
        for key in ("UDP_PORT", "WS_URL", "DEVICE_TOKEN", "LOG_LEVEL"):
            monkeypatch.delenv(key, raising=False)

        # mock Path 使其指向临时 .env 文件
        with patch("config.Path.__truediv__", return_value=env_file):
            config = BridgeConfig.load()

        assert config.UDP_PORT == 54321
        assert config.WS_URL == "ws://192.168.3.119:8002/ws/voice/"
        assert config.DEVICE_TOKEN == "file-token-abc"
        assert config.LOG_LEVEL == "DEBUG"

    def test_env_var_overrides_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量优先于 .env 文件。"""
        env_file = tmp_path / ".env"
        env_file.write_text("DEVICE_TOKEN=file-token\nUDP_PORT=11111\n")
        monkeypatch.setenv("DEVICE_TOKEN", "env-token")
        monkeypatch.setenv("UDP_PORT", "22222")

        with patch("config.Path.__truediv__", return_value=env_file):
            config = BridgeConfig.load()

        assert config.DEVICE_TOKEN == "env-token"
        assert config.UDP_PORT == 22222

    def test_missing_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DEVICE_TOKEN 未设置时抛出 ValueError。"""
        monkeypatch.delenv("DEVICE_TOKEN", raising=False)
        monkeypatch.setattr(
            "config.Path.__truediv__",
            lambda self, other: Path("/nonexistent/.env"),
        )

        with pytest.raises(ValueError, match="DEVICE_TOKEN"):
            BridgeConfig.load()


# ========== bridge 核心测试 ==========


class TestBridgeUDPToWS:
    """UDP 接收 -> 音频转换 -> WS 发送链路测试。"""

    @pytest.mark.asyncio
    async def test_udp_frame_triggers_convert_and_ws_send(self) -> None:
        """UDP 收到帧 -> AudioConverter.convert() -> WS.send(binary)。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge.ws_connected = True

        # mock WebSocket
        mock_ws = AsyncMock()
        bridge._ws = mock_ws

        # mock converter
        mock_converter = MagicMock()
        mock_converter.convert.return_value = b"\x00\x01" * 128  # 256 字节
        bridge.converter = mock_converter

        # 放入一帧原始数据
        raw_frame = b"\x00" * EXPECTED_FRAME_SIZE
        bridge.audio_queue.put_nowait(raw_frame)

        # 运行 _audio_forward_loop 一次迭代
        bridge._running = True

        async def run_one_iteration() -> None:
            """执行一次转发循环迭代后停止。"""
            original_get = bridge.audio_queue.get

            call_count = 0

            async def get_then_stop() -> bytes:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return await original_get()
                # 第二次调用时停止循环
                bridge._running = False
                raise asyncio.TimeoutError

            with patch.object(bridge.audio_queue, "get", side_effect=get_then_stop):
                await bridge._audio_forward_loop()

        await run_one_iteration()

        # 验证: converter.convert 被调用
        mock_converter.convert.assert_called_once_with(raw_frame)
        # 验证: WS 发送了转换后的二进制数据
        mock_ws.send.assert_called_once_with(b"\x00\x01" * 128)

    @pytest.mark.asyncio
    async def test_frame_discarded_when_ws_not_connected(self) -> None:
        """WS 未连接时 UDP 帧被丢弃，无异常。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge.ws_connected = False  # WS 未连接

        # UDP 协议实例
        protocol = _UDPProtocol(bridge)
        protocol.connection_made(MagicMock())

        # 发送数据 - 不应入队
        protocol.datagram_received(b"\x00" * 1024, ("192.168.3.100", 50000))
        assert bridge.audio_queue.empty()

    @pytest.mark.asyncio
    async def test_audio_forward_skips_when_ws_disconnects_mid_loop(self) -> None:
        """队列中有帧但 WS 断开时，帧被跳过不发送。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge.ws_connected = False  # 断开
        bridge._ws = None
        bridge._running = True

        # 放入帧
        bridge.audio_queue.put_nowait(b"\x00" * 1024)

        call_count = 0
        original_get = bridge.audio_queue.get

        async def get_then_stop() -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return await original_get()
            bridge._running = False
            raise asyncio.TimeoutError

        with patch.object(bridge.audio_queue, "get", side_effect=get_then_stop):
            await bridge._audio_forward_loop()

        # converter 不应被调用（WS 未连接时跳过转换）
        assert bridge._stats_frames == 0


class TestBridgeWSEvents:
    """WebSocket 事件接收测试。"""

    @pytest.mark.asyncio
    async def test_ws_receive_json_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """WS 收到 JSON 事件时正确记录日志。"""
        import logging

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)

        # 模拟 WS 消息迭代器
        events = [
            json.dumps({
                "type": "transcription.completed",
                "data": {"text": "你好世界"},
            }),
            json.dumps({
                "type": "decision.result",
                "data": {"decision": "RESPOND", "reason": "唤醒词匹配"},
            }),
        ]

        mock_ws = _AsyncIterFromList(events)

        with caplog.at_level(logging.INFO, logger="respeaker_bridge"):
            await bridge._ws_receive_loop(mock_ws)

        log_text = caplog.text
        assert "转录完成" in log_text
        assert "你好世界" in log_text
        assert "决策结果" in log_text
        assert "RESPOND" in log_text

    @pytest.mark.asyncio
    async def test_ws_receive_error_event(self, caplog: pytest.LogCaptureFixture) -> None:
        """WS 收到 error 事件时记录 ERROR 级别日志。"""
        import logging

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)

        events = [
            json.dumps({
                "type": "error",
                "data": {"code": "ASR_FAILED", "message": "ASR 服务不可用"},
            }),
        ]

        mock_ws = _AsyncIterFromList(events)

        with caplog.at_level(logging.ERROR, logger="respeaker_bridge"):
            await bridge._ws_receive_loop(mock_ws)

        assert "服务端错误" in caplog.text
        assert "ASR_FAILED" in caplog.text


class TestBridgeSessionConfigure:
    """session.configure 消息发送测试。"""

    @pytest.mark.asyncio
    async def test_configure_sent_on_connect(self) -> None:
        """WS 连接后自动发送 session.configure (mode=ambient)。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True

        # 模拟 websockets 连接：支持 send() + async for 空迭代
        mock_ws = _MockWS([])

        # websockets.connect() 返回异步上下文管理器（非协程）
        mock_connect_cm = MagicMock()
        mock_connect_cm.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect_cm.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def fake_connect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                bridge._running = False
                raise OSError("模拟停止")
            return mock_connect_cm

        with patch("bridge.websockets.connect", side_effect=fake_connect):
            await bridge._ws_connection_loop()

        # 验证 session.configure 消息
        send_calls = mock_ws.send.call_args_list
        assert len(send_calls) >= 1
        first_msg = json.loads(send_calls[0][0][0])
        assert first_msg["type"] == "session.configure"
        assert first_msg["data"]["mode"] == "ambient"


class TestUDPProtocol:
    """UDP 协议测试。"""

    def test_datagram_queued_when_ws_connected(self) -> None:
        """WS 已连接时 UDP 帧入队。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge.ws_connected = True

        protocol = _UDPProtocol(bridge)
        protocol.connection_made(MagicMock())

        data = b"\x00" * 1024
        protocol.datagram_received(data, ("192.168.3.100", 50000))

        assert not bridge.audio_queue.empty()
        assert bridge.audio_queue.get_nowait() == data

    def test_datagram_discarded_when_ws_not_connected(self) -> None:
        """WS 未连接时 UDP 帧被静默丢弃。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge.ws_connected = False

        protocol = _UDPProtocol(bridge)
        protocol.connection_made(MagicMock())

        protocol.datagram_received(b"\x00" * 1024, ("192.168.3.100", 50000))
        assert bridge.audio_queue.empty()


class TestReconnectLogic:
    """T018d: WebSocket 重连逻辑测试。"""

    @pytest.mark.asyncio
    async def test_reconnect_intervals_are_linear(self) -> None:
        """重连间隔应为线性递增 3/6/9/12/15 秒。"""
        config = BridgeConfig(
            UDP_PORT=12345, WS_URL="ws://localhost:8002/ws/voice/", DEVICE_TOKEN="tok",
        )
        bridge = ReSpeakerBridge(config)
        assert bridge._RECONNECT_INTERVALS == [3, 6, 9, 12, 15]
        assert bridge._RECONNECT_RESET_WAIT == 60

    @pytest.mark.asyncio
    async def test_reconnect_five_attempts_then_reset(self) -> None:
        """5 次重连失败后等待 60 ��重置计数器。"""
        config = BridgeConfig(
            UDP_PORT=12345, WS_URL="ws://localhost:8002/ws/voice/", DEVICE_TOKEN="tok",
        )
        bridge = ReSpeakerBridge(config)
        assert len(bridge._RECONNECT_INTERVALS) == 5
        assert bridge._RECONNECT_RESET_WAIT == 60
        for i, expected in enumerate([3, 6, 9, 12, 15]):
            assert bridge._RECONNECT_INTERVALS[i] == expected

    @pytest.mark.asyncio
    async def test_udp_frames_discarded_during_reconnect(self) -> None:
        """重连期间 UDP 帧应被丢弃不缓存。"""
        config = BridgeConfig(
            UDP_PORT=12345, WS_URL="ws://localhost:8002/ws/voice/", DEVICE_TOKEN="tok",
        )
        bridge = ReSpeakerBridge(config)
        bridge.ws_connected = False  # 模拟断连状态

        protocol = _UDPProtocol(bridge)
        protocol.connection_made(MagicMock())
        protocol.datagram_received(b"\x00" * 1024, ("192.168.3.100", 50000))

        assert bridge.audio_queue.empty(), "重连期间帧不应入队"


class TestUDPWatchdog:
    """T019b: UDP 流中断检测测试。"""

    @pytest.mark.asyncio
    async def test_udp_timeout_triggers_warning(self) -> None:
        """30 秒无 UDP 数据应标记为中断。"""
        config = BridgeConfig(
            UDP_PORT=12345, WS_URL="ws://localhost:8002/ws/voice/", DEVICE_TOKEN="tok",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True
        bridge._last_udp_time = time.monotonic() - 35  # 模拟 35 秒前的最后一帧

        # 运行一次 watchdog 检查周期
        with patch("bridge.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            async def stop_after_one_check(*args):
                bridge._running = False

            mock_sleep.side_effect = stop_after_one_check
            await bridge._udp_watchdog_loop()

        assert bridge._udp_interrupted is True

    @pytest.mark.asyncio
    async def test_udp_recovery_resets_flag(self) -> None:
        """UDP 数据恢复后中断标志应被重置。"""
        config = BridgeConfig(
            UDP_PORT=12345, WS_URL="ws://localhost:8002/ws/voice/", DEVICE_TOKEN="tok",
        )
        bridge = ReSpeakerBridge(config)
        bridge._udp_interrupted = True
        bridge.ws_connected = True

        protocol = _UDPProtocol(bridge)
        protocol.connection_made(MagicMock())
        protocol.datagram_received(b"\x00" * 1024, ("192.168.3.100", 50000))

        assert bridge._udp_interrupted is False, "收到数据后中断标志应重置"
        assert bridge._last_udp_time > 0

    @pytest.mark.asyncio
    async def test_no_warning_when_data_flowing(self) -> None:
        """正常接收数据时不应触发中断告警。"""
        config = BridgeConfig(
            UDP_PORT=12345, WS_URL="ws://localhost:8002/ws/voice/", DEVICE_TOKEN="tok",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True
        bridge._last_udp_time = time.monotonic() - 5  # 5 秒前有数据，正常

        with patch("bridge.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            async def stop_after_one(*args):
                bridge._running = False
            mock_sleep.side_effect = stop_after_one
            await bridge._udp_watchdog_loop()

        assert bridge._udp_interrupted is False


# 导入需要在 sys.path 配置后
from bridge import ReSpeakerBridge, _UDPProtocol


# ========== UDP 队列满 / error_received 测试 ==========


class TestUDPProtocolEdgeCases:
    """UDP 协议边界测试：队列满、error_received。"""

    def test_queue_full_drops_oldest_frame(self) -> None:
        """队列满时丢弃最旧帧，新帧入队。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        # 用 maxsize=1 的队列模拟满队列
        bridge.audio_queue = asyncio.Queue(maxsize=1)
        bridge.ws_connected = True

        protocol = _UDPProtocol(bridge)
        protocol.connection_made(MagicMock())

        old_data = b"\xAA" * 1024
        new_data = b"\xBB" * 1024

        # 先填满队列
        bridge.audio_queue.put_nowait(old_data)
        assert bridge.audio_queue.full()

        # 再发一帧，旧帧应被丢弃，新帧入队
        protocol.datagram_received(new_data, ("192.168.3.100", 50000))

        result = bridge.audio_queue.get_nowait()
        assert result == new_data, "队列满时应丢弃旧帧、保留新帧"

    def test_error_received_logs_warning(self, caplog) -> None:
        """error_received() 应记录 WARNING 日志。"""
        import logging

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        protocol = _UDPProtocol(bridge)
        protocol.connection_made(MagicMock())

        with caplog.at_level(logging.WARNING, logger="respeaker_bridge"):
            protocol.error_received(OSError("UDP 测试错误"))

        assert "UDP 接收错误" in caplog.text


# ========== run() 方法测试 ==========


class TestBridgeRun:
    """ReSpeakerBridge.run() 主入口测试。"""

    @pytest.mark.asyncio
    async def test_run_starts_and_cleans_up(self) -> None:
        """run() 启动后立即取消时，完成 cleanup。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)

        mock_transport = MagicMock()

        # 模拟 loop.create_datagram_endpoint 返回 (transport, protocol)
        async def fake_gather(*tasks, **kwargs):
            # 模拟所有任务被取消
            for t in tasks:
                t.cancel()
            raise asyncio.CancelledError

        with patch("bridge.asyncio.get_running_loop") as mock_loop_fn:
            mock_loop = MagicMock()
            mock_loop_fn.return_value = mock_loop

            # create_datagram_endpoint 返回 coroutine
            async def fake_endpoint(factory, local_addr):
                return (mock_transport, MagicMock())

            mock_loop.create_datagram_endpoint = fake_endpoint
            mock_loop.add_signal_handler = MagicMock()

            # 让 gather 快速结束
            with patch("bridge.asyncio.gather", side_effect=fake_gather):
                with patch("bridge.asyncio.create_task") as mock_create_task:
                    mock_task = MagicMock()
                    mock_task.done.return_value = True
                    mock_create_task.return_value = mock_task

                    await bridge.run()

        # cleanup 应关闭 transport
        mock_transport.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_registers_signal_handlers(self) -> None:
        """run() 应注册 SIGTERM 和 SIGINT 信号处理器。"""
        import signal as signal_module

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)

        mock_transport = MagicMock()
        signal_handlers = {}

        with patch("bridge.asyncio.get_running_loop") as mock_loop_fn:
            mock_loop = MagicMock()
            mock_loop_fn.return_value = mock_loop

            async def fake_endpoint(factory, local_addr):
                return (mock_transport, MagicMock())

            mock_loop.create_datagram_endpoint = fake_endpoint

            def record_signal(sig, handler):
                signal_handlers[sig] = handler

            mock_loop.add_signal_handler = record_signal

            with patch("bridge.asyncio.gather", side_effect=asyncio.CancelledError):
                with patch("bridge.asyncio.create_task") as mock_create_task:
                    mock_task = MagicMock()
                    mock_task.done.return_value = True
                    mock_create_task.return_value = mock_task

                    await bridge.run()

        assert signal_module.SIGTERM in signal_handlers
        assert signal_module.SIGINT in signal_handlers


# ========== WS 重连逻辑扩展测试 ==========


class TestWSConnectionLoopReconnect:
    """WS 连接循环重连路径测试。"""

    @pytest.mark.asyncio
    async def test_connection_closed_triggers_reconnect(self) -> None:
        """ConnectionClosed 后进入重连逻辑。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True

        mock_ws = _MockWS([])

        # 第一次 connect 成功但 receive_loop 抛 ConnectionClosed
        # 第二次重连时停止
        call_count = [0]

        mock_connect_cm = MagicMock()
        mock_connect_cm.__aenter__ = AsyncMock(return_value=mock_ws)

        async def fake_exit(*args):
            return False

        mock_connect_cm.__aexit__ = fake_exit

        async def fake_receive(ws):
            raise websockets.exceptions.ConnectionClosed(
                websockets.frames.Close(1001, "going away"), None
            )

        sleep_count = [0]

        async def fake_sleep(seconds):
            sleep_count[0] += 1
            # 停止在第一次重连 sleep 之后
            bridge._running = False

        def fake_connect(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_connect_cm
            raise OSError("stopped")

        with patch("bridge.websockets.connect", side_effect=fake_connect):
            with patch.object(bridge, "_ws_receive_loop", side_effect=fake_receive):
                with patch("bridge.asyncio.sleep", new_callable=AsyncMock, side_effect=fake_sleep):
                    await bridge._ws_connection_loop()

        assert sleep_count[0] >= 1, "连接关闭后应进入重连等待"

    @pytest.mark.asyncio
    async def test_oserror_triggers_reconnect(self) -> None:
        """OSError 连接失败后进入重连逻辑。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True

        sleep_count = [0]

        async def fake_sleep(seconds):
            sleep_count[0] += 1
            bridge._running = False

        def fake_connect(url, **kwargs):
            raise OSError("连接被拒绝")

        with patch("bridge.websockets.connect", side_effect=fake_connect):
            with patch("bridge.asyncio.sleep", new_callable=AsyncMock, side_effect=fake_sleep):
                await bridge._ws_connection_loop()

        assert sleep_count[0] >= 1

    @pytest.mark.asyncio
    async def test_generic_exception_triggers_reconnect(self) -> None:
        """未知异常后进入重连逻辑。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True

        call_count = [0]
        sleep_count = [0]

        mock_connect_cm = MagicMock()
        mock_connect_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("未知错误"))
        mock_connect_cm.__aexit__ = AsyncMock(return_value=False)

        async def fake_sleep(seconds):
            sleep_count[0] += 1
            bridge._running = False

        def fake_connect(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("未知异常")
            raise OSError("stopped")

        with patch("bridge.websockets.connect", side_effect=fake_connect):
            with patch("bridge.asyncio.sleep", new_callable=AsyncMock, side_effect=fake_sleep):
                await bridge._ws_connection_loop()

        assert sleep_count[0] >= 1

    @pytest.mark.asyncio
    async def test_reconnect_success_sends_session_configure(self) -> None:
        """重连成功后重发 session.configure (mode=ambient)。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True
        # 缩短重连列表以加速测试
        bridge._RECONNECT_INTERVALS = [0]

        reconnect_ws = _MockWS([])

        # 第一次 context manager 连接成功 -> receive_loop 抛 ConnectionClosed
        mock_connect_cm = MagicMock()
        first_ws = _MockWS([])
        mock_connect_cm.__aenter__ = AsyncMock(return_value=first_ws)
        mock_connect_cm.__aexit__ = AsyncMock(return_value=False)

        call_count = [0]
        stopped = [False]

        async def fake_receive(ws):
            if ws is first_ws:
                raise websockets.exceptions.ConnectionClosed(
                    websockets.frames.Close(1001, "gone"), None
                )
            # 重连后的 ws，停止循环
            stopped[0] = True
            bridge._running = False

        async def fake_sleep(seconds):
            pass  # 不延迟

        def fake_connect(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_connect_cm
            # 重连时直接返回 reconnect_ws（非 context manager）
            return reconnect_ws

        # 重连路径中 websockets.connect() 被直接 await，非 async with
        reconnect_ws_coro = AsyncMock(return_value=reconnect_ws)

        with patch("bridge.websockets.connect") as mock_connect:
            connect_call_count = [0]

            def side_effect(url, **kwargs):
                connect_call_count[0] += 1
                if connect_call_count[0] == 1:
                    # 首次用 async with
                    return mock_connect_cm
                else:
                    # 重连时直接 await
                    async def _coro():
                        return reconnect_ws
                    return _coro()

            mock_connect.side_effect = side_effect

            with patch.object(bridge, "_ws_receive_loop", side_effect=fake_receive):
                with patch("bridge.asyncio.sleep", new_callable=AsyncMock, side_effect=fake_sleep):
                    await bridge._ws_connection_loop()

        # 验证重连 ws 发送了 session.configure
        assert reconnect_ws.send.called
        msg = json.loads(reconnect_ws.send.call_args[0][0])
        assert msg["type"] == "session.configure"
        assert msg["data"]["mode"] == "ambient"

    @pytest.mark.asyncio
    async def test_all_reconnect_attempts_fail_then_reset_wait(self) -> None:
        """5 次重连全失败后等待 RECONNECT_RESET_WAIT 秒。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True
        bridge._RECONNECT_INTERVALS = [0, 0, 0, 0, 0]
        bridge._RECONNECT_RESET_WAIT = 0

        sleep_delays = []

        outer_loop_count = [0]

        async def fake_sleep(seconds):
            sleep_delays.append(seconds)
            outer_loop_count[0] += 1
            # 第一轮5次重连的sleep + reset sleep后停止
            if outer_loop_count[0] >= 6:
                bridge._running = False

        def fake_connect(url, **kwargs):
            # 首次用 async with 也失败
            raise OSError("连接失败")

        with patch("bridge.websockets.connect", side_effect=fake_connect):
            with patch("bridge.asyncio.sleep", new_callable=AsyncMock, side_effect=fake_sleep):
                await bridge._ws_connection_loop()

        # 5次重连 sleep + 1次 reset sleep
        assert len(sleep_delays) >= 6

    @pytest.mark.asyncio
    async def test_reconnect_connection_closed_continues(self) -> None:
        """重连成功后 receive_loop 再次 ConnectionClosed，继续外层循环。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True
        bridge._RECONNECT_INTERVALS = [0]

        mock_connect_cm = MagicMock()
        first_ws = _MockWS([])
        mock_connect_cm.__aenter__ = AsyncMock(return_value=first_ws)
        mock_connect_cm.__aexit__ = AsyncMock(return_value=False)

        reconnect_ws = _MockWS([])
        receive_call_count = [0]
        outer_calls = [0]

        async def fake_receive(ws):
            receive_call_count[0] += 1
            if receive_call_count[0] <= 2:
                raise websockets.exceptions.ConnectionClosed(
                    websockets.frames.Close(1001, "gone"), None
                )
            bridge._running = False

        sleep_count = [0]

        async def fake_sleep(seconds):
            sleep_count[0] += 1

        def fake_connect(url, **kwargs):
            outer_calls[0] += 1
            if outer_calls[0] == 1:
                return mock_connect_cm
            # 后续重连（包括重连里的 await connect()）返回 reconnect_ws
            async def _coro():
                return reconnect_ws
            return _coro()

        with patch("bridge.websockets.connect", side_effect=fake_connect):
            with patch.object(bridge, "_ws_receive_loop", side_effect=fake_receive):
                with patch("bridge.asyncio.sleep", new_callable=AsyncMock, side_effect=fake_sleep):
                    await bridge._ws_connection_loop()

    @pytest.mark.asyncio
    async def test_reconnect_generic_exception_continues(self) -> None:
        """重连成功后 receive_loop 抛通用异常，继续循环。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True
        bridge._RECONNECT_INTERVALS = [0]

        mock_connect_cm = MagicMock()
        first_ws = _MockWS([])
        mock_connect_cm.__aenter__ = AsyncMock(return_value=first_ws)
        mock_connect_cm.__aexit__ = AsyncMock(return_value=False)

        reconnect_ws = _MockWS([])
        receive_call_count = [0]

        async def fake_receive(ws):
            receive_call_count[0] += 1
            if receive_call_count[0] == 1:
                raise websockets.exceptions.ConnectionClosed(
                    websockets.frames.Close(1001, "gone"), None
                )
            if receive_call_count[0] == 2:
                raise RuntimeError("接收异常")
            bridge._running = False

        outer_calls = [0]

        async def fake_sleep(seconds):
            pass

        def fake_connect(url, **kwargs):
            outer_calls[0] += 1
            if outer_calls[0] == 1:
                return mock_connect_cm
            async def _coro():
                return reconnect_ws
            return _coro()

        with patch("bridge.websockets.connect", side_effect=fake_connect):
            with patch.object(bridge, "_ws_receive_loop", side_effect=fake_receive):
                with patch("bridge.asyncio.sleep", new_callable=AsyncMock, side_effect=fake_sleep):
                    await bridge._ws_connection_loop()


# ========== WS 事件接收扩展测试 ==========


class TestWSReceiveLoopEdgeCases:
    """WS 接收循环边界情况测试。"""

    @pytest.mark.asyncio
    async def test_session_configured_event(self, caplog) -> None:
        """session.configured 事件应记录 INFO 日志。"""
        import logging

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)

        events = [
            json.dumps({
                "type": "session.configured",
                "data": {"mode": "ambient", "session_id": "sess-001"},
            }),
        ]
        mock_ws = _AsyncIterFromList(events)

        with caplog.at_level(logging.INFO, logger="respeaker_bridge"):
            await bridge._ws_receive_loop(mock_ws)

        assert "会话已配置" in caplog.text
        assert "ambient" in caplog.text

    @pytest.mark.asyncio
    async def test_aggregation_completed_event(self, caplog) -> None:
        """aggregation.completed 事件应记录 INFO 日志。"""
        import logging

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)

        events = [
            json.dumps({
                "type": "aggregation.completed",
                "data": {"aggregated_text": "聚合文本内容", "utterance_count": 3},
            }),
        ]
        mock_ws = _AsyncIterFromList(events)

        with caplog.at_level(logging.INFO, logger="respeaker_bridge"):
            await bridge._ws_receive_loop(mock_ws)

        assert "聚合完成" in caplog.text
        assert "聚合文本内容" in caplog.text

    @pytest.mark.asyncio
    async def test_unknown_event_type_debug_logged(self, caplog) -> None:
        """未知事件类型应记录 DEBUG 日志。"""
        import logging

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)

        events = [
            json.dumps({"type": "custom.unknown", "data": {}}),
        ]
        mock_ws = _AsyncIterFromList(events)

        with caplog.at_level(logging.DEBUG, logger="respeaker_bridge"):
            await bridge._ws_receive_loop(mock_ws)

        assert "custom.unknown" in caplog.text

    @pytest.mark.asyncio
    async def test_invalid_json_logs_warning(self, caplog) -> None:
        """非 JSON 文本消息应记录 WARNING。"""
        import logging

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)

        mock_ws = _AsyncIterFromList(["not-valid-json{{{"])

        with caplog.at_level(logging.WARNING, logger="respeaker_bridge"):
            await bridge._ws_receive_loop(mock_ws)

        assert "非 JSON" in caplog.text

    @pytest.mark.asyncio
    async def test_binary_message_debug_logged(self, caplog) -> None:
        """二进制消息应记录 DEBUG 日志。"""
        import logging

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)

        mock_ws = _AsyncIterFromList([b"\x00\x01\x02\x03"])  # bytes

        with caplog.at_level(logging.DEBUG, logger="respeaker_bridge"):
            await bridge._ws_receive_loop(mock_ws)

        assert "二进制消息" in caplog.text


# ========== audio_forward_loop 边界测试 ==========


class TestAudioForwardLoopEdgeCases:
    """音频转发循环边界测试。"""

    @pytest.mark.asyncio
    async def test_converter_returns_none_skips_send(self) -> None:
        """converter.convert() 返回 None 时跳过 ws.send()。"""
        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge.ws_connected = True
        mock_ws = AsyncMock()
        bridge._ws = mock_ws

        # converter 返回 None（首包校验失败）
        mock_converter = MagicMock()
        mock_converter.convert.return_value = None
        bridge.converter = mock_converter

        bridge.audio_queue.put_nowait(b"\x00" * 512)
        bridge._running = True

        call_count = [0]
        original_get = bridge.audio_queue.get

        async def get_then_stop() -> bytes:
            call_count[0] += 1
            if call_count[0] == 1:
                return await original_get()
            bridge._running = False
            raise asyncio.TimeoutError

        with patch.object(bridge.audio_queue, "get", side_effect=get_then_stop):
            await bridge._audio_forward_loop()

        mock_ws.send.assert_not_called()
        assert bridge._stats_frames == 0

    @pytest.mark.asyncio
    async def test_ws_send_exception_logs_warning(self, caplog) -> None:
        """ws.send() 抛异常时记录 WARNING，不中断循环。"""
        import logging

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge.ws_connected = True

        mock_ws = AsyncMock()
        mock_ws.send.side_effect = Exception("发送失败")
        bridge._ws = mock_ws

        mock_converter = MagicMock()
        mock_converter.convert.return_value = b"\x00\x01" * 128
        bridge.converter = mock_converter

        bridge.audio_queue.put_nowait(b"\x00" * EXPECTED_FRAME_SIZE)
        bridge._running = True

        call_count = [0]
        original_get = bridge.audio_queue.get

        async def get_then_stop() -> bytes:
            call_count[0] += 1
            if call_count[0] == 1:
                return await original_get()
            bridge._running = False
            raise asyncio.TimeoutError

        with caplog.at_level(logging.WARNING, logger="respeaker_bridge"):
            with patch.object(bridge.audio_queue, "get", side_effect=get_then_stop):
                await bridge._audio_forward_loop()

        assert "音频帧发送失败" in caplog.text
        assert bridge._stats_frames == 0


# ========== stats_loop 测试 ==========


class TestStatsLoop:
    """帧统计循环测试。"""

    @pytest.mark.asyncio
    async def test_stats_loop_with_frames(self, caplog) -> None:
        """有帧数据时输出帧统计日志并重置统计。"""
        import logging

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True
        bridge._stats_frames = 100
        bridge._stats_bytes = 25600
        bridge._stats_latency_sum = 0.5  # 500ms total / 100 frames = 5ms avg

        async def fake_sleep(seconds):
            bridge._running = False

        with caplog.at_level(logging.INFO, logger="respeaker_bridge"):
            with patch("bridge.asyncio.sleep", new_callable=AsyncMock, side_effect=fake_sleep):
                await bridge._stats_loop()

        assert "帧统计" in caplog.text
        assert "frames=100" in caplog.text
        # 重置后统计归零
        assert bridge._stats_frames == 0
        assert bridge._stats_bytes == 0
        assert bridge._stats_latency_sum == 0.0

    @pytest.mark.asyncio
    async def test_stats_loop_zero_frames(self, caplog) -> None:
        """无帧时输出零统计日志。"""
        import logging

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True
        # 默认 _stats_frames = 0

        async def fake_sleep(seconds):
            bridge._running = False

        with caplog.at_level(logging.INFO, logger="respeaker_bridge"):
            with patch("bridge.asyncio.sleep", new_callable=AsyncMock, side_effect=fake_sleep):
                await bridge._stats_loop()

        assert "frames=0" in caplog.text


# ========== _shutdown / _cleanup 测试 ==========


class TestShutdownAndCleanup:
    """优雅关闭和资源清理测试。"""

    @pytest.mark.asyncio
    async def test_shutdown_sets_running_false(self) -> None:
        """_shutdown() 设置 _running=False。"""
        import signal as signal_module

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True

        await bridge._shutdown(signal_module.SIGTERM)

        assert bridge._running is False

    @pytest.mark.asyncio
    async def test_shutdown_cancels_tasks(self) -> None:
        """_shutdown() 取消所有未完成任务。"""
        import signal as signal_module

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True

        mock_task1 = MagicMock()
        mock_task1.done.return_value = False
        mock_task2 = MagicMock()
        mock_task2.done.return_value = True  # 已完成，不应被取消

        bridge._tasks = [mock_task1, mock_task2]

        await bridge._shutdown(signal_module.SIGTERM)

        mock_task1.cancel.assert_called_once()
        mock_task2.cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_closes_websocket(self) -> None:
        """_shutdown() 关闭 WebSocket 连接。"""
        import signal as signal_module

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True

        mock_ws = AsyncMock()
        bridge._ws = mock_ws

        await bridge._shutdown(signal_module.SIGTERM)

        mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_ws_close_exception_ignored(self) -> None:
        """_shutdown() 关闭 WebSocket 异常时静默忽略。"""
        import signal as signal_module

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._running = True

        mock_ws = AsyncMock()
        mock_ws.close.side_effect = Exception("关闭失败")
        bridge._ws = mock_ws

        # 不应抛异常
        await bridge._shutdown(signal_module.SIGTERM)
        assert bridge._running is False

    @pytest.mark.asyncio
    async def test_cleanup_closes_transport(self, caplog) -> None:
        """_cleanup() 关闭 UDP transport 并记录日志。"""
        import logging

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)

        mock_transport = MagicMock()
        bridge._transport = mock_transport

        with caplog.at_level(logging.INFO, logger="respeaker_bridge"):
            await bridge._cleanup()

        mock_transport.close.assert_called_once()
        assert "UDP 服务器已关闭" in caplog.text
        assert "桥接服务已停止" in caplog.text

    @pytest.mark.asyncio
    async def test_cleanup_no_transport(self, caplog) -> None:
        """_cleanup() 无 transport 时不出错。"""
        import logging

        config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="test-token",
        )
        bridge = ReSpeakerBridge(config)
        bridge._transport = None

        with caplog.at_level(logging.INFO, logger="respeaker_bridge"):
            await bridge._cleanup()

        assert "桥接服务已停止" in caplog.text


# ========== main() 函数测试 ==========


class TestMain:
    """main() 入口函数测试。"""

    def test_main_runs_bridge(self) -> None:
        """main() 加载配置、运行 bridge，KeyboardInterrupt 时优雅退出。"""
        from bridge import main

        mock_config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="main-test-token",
        )

        with patch("bridge.BridgeConfig.load", return_value=mock_config):
            with patch("bridge.asyncio.run", side_effect=KeyboardInterrupt):
                # KeyboardInterrupt 应被静默捕获
                main()  # 不抛异常

    def test_main_configures_logging(self) -> None:
        """main() 应调用 logging.basicConfig 配置日志级别。"""
        import logging as logging_module
        from bridge import main

        mock_config = BridgeConfig(
            UDP_PORT=12345,
            WS_URL="ws://localhost:8002/ws/voice/",
            DEVICE_TOKEN="main-test-token",
            LOG_LEVEL="DEBUG",
        )

        with patch("bridge.BridgeConfig.load", return_value=mock_config):
            with patch("bridge.asyncio.run", side_effect=KeyboardInterrupt):
                with patch("bridge.logging.basicConfig") as mock_basic_config:
                    main()
                    mock_basic_config.assert_called_once()
                    call_kwargs = mock_basic_config.call_args[1]
                    assert call_kwargs.get("level") == logging_module.DEBUG
