"""快速录制 3 秒音频，直接保存 v2 固件输出。

用法: python quick_record.py --port COM5
"""
import argparse
import serial
import wave

SYNC = bytes([0xAA, 0x55, 0x03, 0x00])
FRAME_SIZE = 512  # v3: 16-bit/1ch/16kHz, 256 samples


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM5")
    args = parser.parse_args()

    s = serial.Serial(args.port, 115200, timeout=3)
    s.reset_input_buffer()

    # 同步
    print("同步帧头...")
    buf = bytearray()
    for _ in range(5000):
        b = s.read(1)
        if not b:
            print("超时！"); return
        buf.append(b[0])
        if len(buf) >= 4 and buf[-4:] == bytearray(SYNC):
            break
    else:
        print("找不到帧头！"); return
    print("OK")

    # 录制 3 秒 = 16000*3/128 = 375 帧
    print("录制 3 秒，请说话...")
    pcm = bytearray()
    for _ in range(400):
        frame = s.read(FRAME_SIZE)
        if len(frame) != FRAME_SIZE:
            continue
        pcm.extend(frame)
        header = s.read(4)
        if header != SYNC:
            # 重新同步
            buf2 = bytearray(header)
            for _ in range(2000):
                b = s.read(1)
                if not b: break
                buf2.append(b[0])
                if len(buf2) >= 4 and buf2[-4:] == bytearray(SYNC):
                    break

    s.close()
    print(f"录制了 {len(pcm)} bytes ({len(pcm)/32000:.1f}s)")

    # 保存
    with wave.open("quick_test.wav", "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm)
    print("已保存 quick_test.wav，请播放试听！")


if __name__ == "__main__":
    main()
