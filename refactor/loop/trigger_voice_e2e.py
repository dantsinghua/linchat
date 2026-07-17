#!/usr/bin/env python3
"""batch-27 SLO 测量：模拟 reSpeaker 推送 wav 触发 N 次 ambient 语音 pipeline。

用法: linchat/bin/python refactor/loop/trigger_voice_e2e.py [N=1] [wav=scripts/respeaker_bridge/quick_test.wav]

协议同 scripts/respeaker_bridge/bridge.py:
  ws://localhost:8002/ws/voice/?token=<设备token> → session.configure(ambient) → binary PCM(16bit/1ch/16kHz)
设备 token 从 RegisteredDevice 表解密（仅进程内存，不打印不落盘）。
每轮: 实时速率推送 wav 语音段 → 3s 静音（触发 VAD speech_end + 聚合窗口）→ 等待 pipeline 完成。
测量数据由 batch-07 埋点写入后端日志 latency.summary 行，跑完后用 measure-voice-latency.sh 汇总。
"""
import asyncio
import json
import os
import sys
import time
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
import django  # noqa: E402

django.setup()

import websockets  # noqa: E402
from apps.users.crypto import sm4_decrypt  # noqa: E402
from apps.voice.models import RegisteredDevice  # noqa: E402

CHUNK = 1024          # 每帧字节数（32ms @ 16kHz/16bit/1ch）
FRAME_SEC = CHUNK / 2 / 16000
SILENCE_SEC = 3.0     # 语音段后静音，触发 VAD end + 聚合器 1.5s 窗口
WAIT_PIPELINE = 20.0  # 每轮等待 pipeline（决策+LLM+TTS+HA）完成的上限


def load_pcm(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        assert w.getsampwidth() == 2 and w.getframerate() == 16000, (
            f"需要 16bit/16kHz wav，实际 {w.getsampwidth()*8}bit/{w.getframerate()}Hz")
        frames = w.readframes(w.getnframes())
        if w.getnchannels() == 2:  # 立体声取单声道
            frames = b"".join(frames[i:i+2] for i in range(0, len(frames), 4))
        return frames


async def run(n: int, wav_path: str, device, token: str) -> None:
    pcm = load_pcm(wav_path)
    dur = len(pcm) / 2 / 16000
    print(f"device={device.name} wav={dur:.1f}s rounds={n}")

    url = f"ws://localhost:8002/ws/voice/?token={token}"
    async with websockets.connect(url, max_size=1024 * 1024, close_timeout=5) as ws:
        await ws.send(json.dumps({"type": "session.configure", "data": {"mode": "ambient"}}))

        events = []

        async def drain():
            try:
                async for msg in ws:
                    if isinstance(msg, str):
                        try:
                            t = json.loads(msg).get("type", "?")
                        except ValueError:
                            t = "?"
                        events.append(t)
            except websockets.exceptions.ConnectionClosed:
                pass

        drain_task = asyncio.create_task(drain())
        silence = b"\x00" * CHUNK
        for r in range(1, n + 1):
            t0 = time.monotonic()
            for i in range(0, len(pcm), CHUNK):
                await ws.send(pcm[i:i+CHUNK])
                await asyncio.sleep(FRAME_SEC)
            for _ in range(int(SILENCE_SEC / FRAME_SEC)):
                await ws.send(silence)
                await asyncio.sleep(FRAME_SEC)
            # 静音期后留时间给 决策→LLM→TTS→HA
            await asyncio.sleep(WAIT_PIPELINE)
            print(f"round {r}/{n} done ({time.monotonic()-t0:.0f}s), events so far: {len(events)}")
        drain_task.cancel()
        from collections import Counter
        print("event types:", dict(Counter(events)))


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    wav = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(__file__), "../../scripts/respeaker_bridge/quick_test.wav")
    dev = RegisteredDevice.objects.filter(is_active=True).first()
    assert dev, "无激活设备"
    tok = sm4_decrypt(dev.api_token_encrypted)
    asyncio.run(run(n, wav, dev, tok))
