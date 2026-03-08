#!/usr/bin/env python3
"""语音模式端到端延迟测试 — Gateway ASR + TTS 直连"""

import asyncio
import io
import json
import time
import wave

import websockets
from gtts import gTTS
from pydub import AudioSegment

GATEWAY_URL = "ws://127.0.0.1:8100"
API_KEY = "sk-23h8ugn3828910h8g308979y4"
TEXT = "查一下家里有哪些设备"


def generate_pcm_audio(text: str) -> tuple[bytes, int]:
    """gTTS 生成语音 → 16kHz 16bit mono PCM。"""
    print(f"[gTTS] 生成: '{text}'")
    t0 = time.time()
    tts = gTTS(text=text, lang="zh-cn")
    mp3_buf = io.BytesIO()
    tts.write_to_fp(mp3_buf)
    mp3_buf.seek(0)
    audio = AudioSegment.from_mp3(mp3_buf)
    audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    pcm = audio.raw_data
    dur = len(pcm) / (16000 * 2)
    print(f"[gTTS] 完成: {time.time()-t0:.2f}s, {len(pcm)} bytes, {dur:.1f}s")
    wav_buf = io.BytesIO()
    with wave.open(wav_buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
        wf.writeframes(pcm)
    with open("/tmp/voice_test.wav", "wb") as f:
        f.write(wav_buf.getvalue())
    return pcm, 16000


async def test_asr(pcm_data: bytes) -> dict:
    """测试 Gateway ASR 延迟。"""
    url = f"{GATEWAY_URL}/v1/audio/transcriptions/stream?api_key={API_KEY}"
    print(f"\n{'='*60}")
    print("[ASR 测试]")

    timings = {}
    transcript = ""
    t0 = time.time()

    ws = await websockets.connect(url, ping_interval=30, close_timeout=5)

    # 1. session.created
    raw = await asyncio.wait_for(ws.recv(), timeout=10)
    ev = json.loads(raw)
    timings["connect"] = time.time() - t0
    print(f"  连接: {timings['connect']:.3f}s  session={ev.get('session_id','?')}")

    # 2. configure (无回复)
    await ws.send(json.dumps({
        "type": "configure",
        "auto_commit": True,
        "speech_pad_ms": 600,
        "language": "zh",
    }))
    print(f"  配置已发送 (auto_commit=True, pad=600ms)")

    # 3. 发送音频 — 100ms 帧 (3200B @ 16kHz 16bit mono)
    chunk_size = 3200
    t_send = time.time()
    n = 0
    for i in range(0, len(pcm_data), chunk_size):
        await ws.send(pcm_data[i:i+chunk_size])
        n += 1
        await asyncio.sleep(0.05)  # 50ms 间隔（略快于实时）
    timings["send"] = time.time() - t_send
    print(f"  发送: {timings['send']:.3f}s  {n}帧")

    # 4. commit
    await ws.send(json.dumps({"type": "commit"}))
    t_commit = time.time()

    # 5. 收集事件直到 transcription.completed
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=20)
            ev = json.loads(raw)
            etype = ev.get("type", "")
            if etype == "vad.speech_start":
                print(f"  VAD 开始 (+{time.time()-t0:.3f}s)")
            elif etype == "vad.speech_end":
                print(f"  VAD 结束 (+{time.time()-t0:.3f}s)")
            elif etype == "transcription.completed":
                transcript = ev.get("text", "")
                timings["transcribe"] = time.time() - t_commit
                print(f"  转录完成: '{transcript}' ({timings['transcribe']:.3f}s after commit)")
                break
            elif etype == "error":
                print(f"  错误: {ev}")
                break
        except asyncio.TimeoutError:
            print(f"  超时(20s)")
            break

    timings["total"] = time.time() - t0
    await ws.close()
    print(f"  ASR总计: {timings['total']:.3f}s")
    return {"transcript": transcript, "timings": timings}


async def test_tts(text: str) -> dict:
    """测试 Gateway TTS 延迟。"""
    url = f"{GATEWAY_URL}/v1/audio/speech/stream?api_key={API_KEY}"
    print(f"\n{'='*60}")
    print(f"[TTS 测试] '{text[:30]}...'")

    timings = {}
    t0 = time.time()

    ws = await websockets.connect(url, close_timeout=5)

    # 1. session.created
    raw = await asyncio.wait_for(ws.recv(), timeout=10)
    ev = json.loads(raw)
    timings["connect"] = time.time() - t0
    sample_rate = ev.get("sample_rate", 24000)
    print(f"  连接: {timings['connect']:.3f}s  rate={sample_rate}")

    # 2. config (注意: Gateway 用 "config" 不是 "configure")
    await ws.send(json.dumps({
        "type": "config",
        "voice": "zf_xiaobei",
    }))

    # 3. 发送文本（模拟流式，字段名是 "delta" 不是 "text"）
    await ws.send(json.dumps({"type": "text.delta", "delta": text}))
    await ws.send(json.dumps({"type": "text.done"}))
    t_sent = time.time()

    # 4. 收集音频
    audio_bytes = 0
    first_audio = None
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            if isinstance(raw, bytes):
                audio_bytes += len(raw)
                if first_audio is None:
                    first_audio = time.time()
                    timings["first_audio"] = first_audio - t_sent
                    print(f"  首音频: {timings['first_audio']:.3f}s")
            else:
                ev = json.loads(raw)
                etype = ev.get("type", "")
                if etype == "audio.done":
                    break
                elif etype == "tts.sentence_start":
                    pass
                elif etype == "error":
                    print(f"  错误: {ev}")
                    break
        except asyncio.TimeoutError:
            break

    timings["total"] = time.time() - t0
    timings["audio_bytes"] = audio_bytes
    dur = audio_bytes / (sample_rate * 2) if audio_bytes else 0
    await ws.close()
    print(f"  音频: {audio_bytes}B ({dur:.1f}s)")
    print(f"  TTS总计: {timings['total']:.3f}s")
    return timings


async def main():
    print("=" * 60)
    print("LinChat 语音延迟测试 (Gateway 直连)")
    print("=" * 60)

    pcm, _ = generate_pcm_audio(TEXT)

    asr = await test_asr(pcm)

    tts_text = "目前家里有7个设备在线，包括对话系统、备份管理器和太阳传感器。大部分小米设备处于离线状态。"
    tts = await test_tts(tts_text)

    # 汇总
    at = asr["timings"]
    tt = tts

    asr_conn = at.get("connect", 0)
    asr_send = at.get("send", 0)
    asr_trans = at.get("transcribe", 0)
    tts_conn = tt.get("connect", 0)
    tts_first = tt.get("first_audio", 0)

    # Agent 推理延迟：HA 查询实测约 5-8s，普通问答 1-3s
    agent_ha = 6.0
    agent_simple = 2.0

    print(f"""
{'='*60}
延迟汇总
{'='*60}

┌─────────────────────────┬──────────┐
│ 阶段                    │ 耗时     │
├─────────────────────────┼──────────┤
│ ① ASR 连接              │ {asr_conn:>6.2f}s  │
│ ② 音频发送 (模拟实时)   │ {asr_send:>6.2f}s  │
│ ③ ASR 转录 (commit 后)  │ {asr_trans:>6.2f}s  │
│ ④ TTS 连接              │ {tts_conn:>6.2f}s  │
│ ⑤ TTS 首音频            │ {tts_first:>6.2f}s  │
├─────────────────────────┼──────────┤
│ Agent (HA 查询估算)     │ {agent_ha:>6.2f}s  │
│ Agent (普通问答估算)    │ {agent_simple:>6.2f}s  │
├─────────────────────────┼──────────┤
│ 🎤 说完→🔊 听到 (HA)   │ {asr_trans+agent_ha+tts_conn+tts_first:>6.2f}s  │
│ 🎤 说完→🔊 听到 (问答) │ {asr_trans+agent_simple+tts_conn+tts_first:>6.2f}s  │
└─────────────────────────┴──────────┘

注: ①② 仅在开始时发生，实际体感延迟 = ③+Agent+④+⑤
    LinChat WS 中转额外 ~50-100ms
    ASR 转录结果: '{asr.get("transcript", "N/A")}'
""")


if __name__ == "__main__":
    asyncio.run(main())
