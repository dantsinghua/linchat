"""音频格式转换器 - ESP32 XVF3800 原始 PCM 转 LinChat 16-bit 单声道。

输入格式（ESP32 UDP 数据包）:
  - 32-bit signed / 2 channels / 16kHz / little-endian / interleaved
  - 每包 1024 字节 = 128 样本 x 2 通道 x 4 字节/样本

输出格式（LinChat WebSocket 语音端点）:
  - 16-bit signed / 1 channel (右声道 = ASR 波束) / 16kHz / little-endian

转换逻辑:
  1. 从交错立体声中提取右声道（奇数索引样本 = Ch1 ASR 波束）
  2. 32-bit 右移 16 位取高 16 位，转为 16-bit
"""

import logging
import struct

logger = logging.getLogger(__name__)

# ESP32 固件固定参数（config.h 确认）
EXPECTED_FRAME_SIZE = 1024  # 128 样本 x 2 通道 x 4 字节
SAMPLES_PER_FRAME = 128     # DMA_BUF_LEN
CHANNELS = 2
BYTES_PER_SAMPLE = 4        # 32-bit


def convert_frame(data: bytes) -> bytes | None:
    """将 32-bit/2ch PCM 帧转换为 16-bit/1ch PCM。

    从交错立体声中提取右声道（Ch1 = ASR 波束），
    将 32-bit 样本右移 16 位转为 16-bit。

    Args:
        data: ESP32 UDP 原始数据包（预期 1024 字节）。

    Returns:
        转换后的 16-bit 单声道 PCM 字节，或 None（首包校验失败时）。
    """
    # 解包所有 32-bit 有符号小端样本（128 x 2 = 256 个）
    total_samples = len(data) // BYTES_PER_SAMPLE
    samples = struct.unpack(f"<{total_samples}i", data)

    # 提取右声道（奇数索引: 1, 3, 5, ...）并右移 16 位转 16-bit
    right_channel = []
    for i in range(1, total_samples, CHANNELS):
        # 右移 16 位保留高 16 位，钳位到 int16 范围
        val = samples[i] >> 16
        val = max(-32768, min(32767, val))
        right_channel.append(val)

    # 打包为 16-bit 有符号小端
    return struct.pack(f"<{len(right_channel)}h", *right_channel)


class AudioConverter:
    """带首包校验的音频转换器。

    ESP32 固件启动后格式固定（16kHz/32-bit/2ch），因此只需校验第一个数据包。
    校验通过后后续帧直接转换，不再重复检查。
    """

    def __init__(self) -> None:
        self._validated: bool = False

    def convert(self, data: bytes) -> bytes | None:
        """转换音频帧，首包会进行格式校验。

        Args:
            data: ESP32 UDP 原始数据包。

        Returns:
            转换后的 16-bit 单声道 PCM 字节，
            首包校验失败时返回 None 并记录 ERROR 日志。
        """
        if not self._validated:
            if len(data) != EXPECTED_FRAME_SIZE:
                logger.error(
                    "首包格式校验失败: 期望 %d 字节 (16kHz/32-bit/2ch/128样本), "
                    "实际 %d 字节",
                    EXPECTED_FRAME_SIZE,
                    len(data),
                )
                return None
            self._validated = True
            logger.info(
                "首包格式校验通过: %d 字节, %d 样本/帧",
                EXPECTED_FRAME_SIZE,
                SAMPLES_PER_FRAME,
            )

        return convert_frame(data)
