"""录制 5 秒音频并保存为 WAV，用于验证转换是否正确。

用法: python record_test.py --port COM5
生成: test_output.wav（可直接播放检查音质）
"""
import argparse
import serial
import struct
import wave
import sys

SYNC_HEADER = bytes([0xAA, 0x55, 0x04, 0x00])
FRAME_SIZE = 1024  # 32-bit/2ch


def convert_frame(pcm_32bit_2ch: bytes, use_left: bool = False) -> bytes:
    """32-bit/2ch → 16-bit/1ch。"""
    sample_count = len(pcm_32bit_2ch) // 8
    output = bytearray(sample_count * 2)
    for i in range(sample_count):
        # 左声道 offset=0, 右声道 offset=4
        offset = i * 8 + (0 if use_left else 4)
        val_32 = struct.unpack_from("<i", pcm_32bit_2ch, offset)[0]
        val_16 = max(-32768, min(32767, val_32 >> 16))
        struct.pack_into("<h", output, i * 2, val_16)
    return bytes(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--left", action="store_true", help="使用左声道（默认右声道）")
    args = parser.parse_args()

    print(f"打开 {args.port}...")
    s = serial.Serial(args.port, 115200, timeout=2)
    s.reset_input_buffer()

    # 同步到帧头
    print("同步帧头...")
    buf = bytearray()
    for _ in range(FRAME_SIZE * 10):
        b = s.read(1)
        if not b:
            print("超时！")
            return
        buf.append(b[0])
        if len(buf) >= 4 and buf[-4:] == bytearray(SYNC_HEADER):
            break
    else:
        print("找不到帧头！")
        return
    print("帧头同步成功")

    # 计算需要的帧数: 16kHz, 128 samples/frame
    frames_needed = (16000 * args.seconds) // 128
    channel = "左声道(Ch0)" if args.left else "右声道(Ch1)"
    print(f"录制 {args.seconds} 秒（{frames_needed} 帧），提取{channel}...")
    print("请对着 reSpeaker 说话！")

    all_pcm16 = bytearray()
    recorded = 0
    sync_errors = 0

    for _ in range(frames_needed + 50):  # 多读一些补偿同步丢失
        frame = s.read(FRAME_SIZE)
        if len(frame) != FRAME_SIZE:
            continue

        header = s.read(4)
        if header != SYNC_HEADER:
            sync_errors += 1
            # 重新同步
            buf = bytearray(header)
            for _ in range(FRAME_SIZE * 2):
                b = s.read(1)
                if not b:
                    break
                buf.append(b[0])
                if len(buf) >= 4 and buf[-4:] == bytearray(SYNC_HEADER):
                    break
            continue

        pcm16 = convert_frame(frame, use_left=args.left)
        all_pcm16.extend(pcm16)
        recorded += 1

        if recorded >= frames_needed:
            break

    s.close()
    print(f"录制完成: {recorded} 帧, sync_errors={sync_errors}")

    # 打印一些样本值
    samples = struct.unpack(f"<{min(20, len(all_pcm16)//2)}h", all_pcm16[:40])
    print(f"前 20 个 16-bit 样本: {list(samples)}")
    max_val = max(abs(v) for v in struct.unpack(f"<{len(all_pcm16)//2}h", all_pcm16))
    print(f"最大绝对值: {max_val} (满幅=32767)")

    # 保存两个 WAV: 左声道和右声道
    for ch_name, use_left in [("right", False), ("left", True)]:
        filename = f"test_{ch_name}.wav"
        # 重新转换
        s2 = serial.Serial(args.port, 115200, timeout=2)
        s2.close()

    # 保存当前声道
    filename = f"test_{'left' if args.left else 'right'}.wav"
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(16000)
        wf.writeframes(all_pcm16)
    print(f"\n✅ 已保存: {filename} ({len(all_pcm16)} bytes, {len(all_pcm16)/32000:.1f}s)")
    print(f"请播放 {filename} 检查是否能听到你说的话")


if __name__ == "__main__":
    main()
