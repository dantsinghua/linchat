/**
 * respeaker_serial_stream_v3.ino
 *
 * reSpeaker XVF3800 + XIAO ESP32-S3 串口音频流
 * ESP32 I2S Slave + AudioTools + XVF3800 Master 48kHz
 *
 * 数据流: XVF3800 (I2S Master 48kHz) → ESP32 (Slave) → 提取左声道 → 3:1 降采样 → 16-bit/1ch/16kHz → Serial
 *
 * 帧格式: [SYNC: 0xAA 0x55 0x03 0x00] [PCM: 512 bytes (256 samples, 16-bit/1ch/16kHz)]
 *
 * Board: XIAO_ESP32S3, USB CDC On Boot: Enabled
 * 需要: AudioTools 库
 */

#include "AudioTools.h"

// I2S 引脚
#define I2S_BCK      8
#define I2S_WS       7
#define I2S_DATA_RX  43
#define I2S_DATA_TX  44

// XVF3800 master 48kHz
#define SAMPLE_RATE  48000
#define CHANNELS     2
#define BITS         32

// 同步头 v3
static const uint8_t SYNC_HEADER[4] = {0xAA, 0x55, 0x03, 0x00};

I2SStream i2s_in;
AudioInfo info(SAMPLE_RATE, CHANNELS, BITS);

// 读取 768 个立体声样本 × 8 bytes = 6144 bytes
// 768 / 3 = 256 个 16kHz 样本 → 512 bytes 输出帧
static const int READ_STEREO_SAMPLES = 768;
static uint8_t read_buf[READ_STEREO_SAMPLES * 8];  // 6144 bytes
static int16_t out_buf[256];  // 256 samples @ 16kHz

// 统计
static uint32_t stats_frames = 0;
static uint32_t stats_bytes = 0;
static unsigned long stats_last_ms = 0;

void setup() {
    Serial.begin(115200);
    delay(1000);

    auto config = i2s_in.defaultConfig(RX_MODE);
    config.copyFrom(info);
    config.pin_bck = I2S_BCK;
    config.pin_ws = I2S_WS;
    config.pin_data = I2S_DATA_TX;
    config.pin_data_rx = I2S_DATA_RX;
    config.is_master = false;  // Slave 模式
    config.use_apll = false;

    i2s_in.begin(config);
    stats_last_ms = millis();
}

void loop() {
    size_t bytes_read = i2s_in.readBytes(read_buf, sizeof(read_buf));
    if (bytes_read == 0) return;

    int stereo_samples = bytes_read / 8;
    // 3:1 降采样: 每 3 个 48kHz 样本取 1 个 → 16kHz
    int out_count = 0;
    for (int i = 0; i < stereo_samples && out_count < 256; i += 3) {
        // 左声道 (offset 0) — 两个声道相同，取左声道即可
        int32_t val32 = *(int32_t*)(read_buf + i * 8);
        out_buf[out_count++] = (int16_t)(val32 >> 16);
    }

    if (out_count == 0) return;

    // 发送同步头 + 16kHz 16-bit 单声道 PCM
    Serial.write(SYNC_HEADER, 4);
    Serial.write((uint8_t*)out_buf, out_count * 2);
    Serial.flush();

    stats_frames++;
    stats_bytes += out_count * 2;

    // 每 10 秒统计（不打印文本，避免干扰二进制流）
    unsigned long now = millis();
    if (now - stats_last_ms >= 10000) {
        stats_frames = 0;
        stats_bytes = 0;
        stats_last_ms = now;
    }
}
