"""reSpeaker 串口音频诊断工具。

用法: python diagnose.py --port COM5
"""
import argparse
import serial
import struct


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    print(f"打开 {args.port} @ {args.baud} baud ...")
    s = serial.Serial(args.port, args.baud, timeout=5)
    s.reset_input_buffer()

    # 读取 8KB 原始数据
    print("读取 8KB 数据（约 5 秒）...")
    data = s.read(8192)
    s.close()
    print(f"读取到 {len(data)} bytes\n")

    if len(data) == 0:
        print("未读到数据！检查设备是否在发送。")
        return

    # 1. 同步头检测
    sync = b"\xaa\x55\x04\x00"
    sync_count = data.count(sync)
    print(f"=== 同步头检测 ===")
    print(f"同步头 (AA 55 04 00) 出现次数: {sync_count}")

    # 2. 原始 hex
    print(f"\n=== 前 128 字节 hex ===")
    for i in range(0, min(128, len(data)), 16):
        hex_str = data[i:i+16].hex(" ")
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data[i:i+16])
        print(f"  {i:04x}: {hex_str:<48s} {ascii_str}")

    # 3. 找到第一个同步头后分析帧数据
    pos = data.find(sync)
    if pos < 0:
        print("\n未找到同步头！固件可能没有发送预期格式的数据。")
        print("检查：ESP32 是否烧录了 respeaker_serial_stream.ino？")
        return

    print(f"\n=== 帧分析（从 offset {pos} 开始）===")
    frame_start = pos + 4  # 跳过 4 字节同步头
    if frame_start + 1024 > len(data):
        print("数据不足一帧（需要同步头后 1024 bytes）")
        return

    frame = data[frame_start:frame_start + 1024]

    # 解析为 32-bit 立体声样本（左右交替）
    print(f"\n前 8 个立体声样本（32-bit signed, L/R 交替）:")
    print(f"  {'Sample':>6s}  {'Left (Ch0)':>14s}  {'Right (Ch1)':>14s}")
    print(f"  {'------':>6s}  {'-----------':>14s}  {'------------':>14s}")
    for i in range(8):
        offset = i * 8
        left = struct.unpack_from("<i", frame, offset)[0]
        right = struct.unpack_from("<i", frame, offset + 4)[0]
        print(f"  {i:>6d}  {left:>14d}  {right:>14d}")

    # 4. 统计分析
    lefts = []
    rights = []
    for i in range(128):  # 128 samples per frame
        offset = i * 8
        lefts.append(struct.unpack_from("<i", frame, offset)[0])
        rights.append(struct.unpack_from("<i", frame, offset + 4)[0])

    print(f"\n=== 128 样本统计 ===")
    print(f"Left  (Ch0): min={min(lefts):>12d}, max={max(lefts):>12d}, avg={sum(lefts)//128:>12d}")
    print(f"Right (Ch1): min={min(rights):>12d}, max={max(rights):>12d}, avg={sum(rights)//128:>12d}")

    # 5. 判断是否全零（无信号）
    left_nonzero = sum(1 for v in lefts if abs(v) > 100)
    right_nonzero = sum(1 for v in rights if abs(v) > 100)
    print(f"\nLeft  非零样本: {left_nonzero}/128")
    print(f"Right 非零样本: {right_nonzero}/128")

    if left_nonzero == 0 and right_nonzero == 0:
        print("\n⚠️  两个声道全是零/近零值！")
        print("可能原因：")
        print("  1. XVF3800 I2S 固件未刷或未正确输出")
        print("  2. I2S 引脚接线错误")
        print("  3. 麦克风无声音输入")
    elif right_nonzero > 0:
        print(f"\n✅ 右声道（ASR 波束）有信号！")
        # 转换为 16-bit 看看范围
        r16 = [max(-32768, min(32767, v >> 16)) for v in rights]
        print(f"转 16-bit 后: min={min(r16)}, max={max(r16)}")
    else:
        print(f"\n⚠️  只有左声道有信号，右声道为零")
        print("bridge 脚本提取的是右声道，可能需要改为左声道")


if __name__ == "__main__":
    main()
