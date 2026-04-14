/**
 * respeaker_serial_stream_v2.ino
 *
 * reSpeaker XVF3800 + XIAO ESP32-S3 串口音频流（AudioTools 库版本）
 *
 * 使用官方推荐的 AudioTools 库替代旧 driver/i2s.h API。
 * 通过 USB Serial (CDC) 发送 16-bit/1ch PCM 到 Windows PC。
 *
 * 数据流: XVF3800 I2S → ESP32 → 提取单声道 → Serial → Windows
 *
 * 帧格式: [SYNC: 0xAA 0x55 0x01 0x00] [PCM: 256 bytes (16-bit/1ch, 128 samples)]
 *
 * 使用:
 *   1. 安装 AudioTools 库 (Phil Schatzmann)
 *   2. Board: XIAO_ESP32S3, USB CDC On Boot: Enabled
 *   3. 编译上传
 */

#include "AudioTools.h"

// I2S 引脚（官方文档确认）
#define I2S_BCK   8
#define I2S_WS    7
#define I2S_DATA_RX  43   // XVF3800 → ESP32（音频数据）
#define I2S_DATA_TX  44   // ESP32 → XVF3800（未使用）

// 音频参数
#define SAMPLE_RATE   16000
#define CHANNELS      2      // I2S 输入是立体声
#define BITS          32     // I2S 输入是 32-bit

// 同步头（v2: 标记为 16-bit 单声道输出）
static const uint8_t SYNC_HEADER[4] = {0xAA, 0x55, 0x01, 0x00};

// I2S 输入流
I2SStream i2s_in;
AudioInfo info(SAMPLE_RATE, CHANNELS, BITS);

// 读取缓冲区: 128 个立体声样本 × 2ch × 4bytes = 1024 bytes
static const int READ_SAMPLES = 128;
static uint8_t read_buf[READ_SAMPLES * CHANNELS * (BITS / 8)];  // 1024 bytes
// 输出缓冲区: 128 个单声道样本 × 2bytes = 256 bytes
static int16_t out_buf[READ_SAMPLES];

// 统计
static uint32_t stats_frames = 0;
static uint32_t stats_bytes = 0;
static unsigned long stats_last_ms = 0;

void setup() {
    Serial.begin(115200);
    delay(1000);

    // 配置 I2S（官方推荐方式）
    auto config = i2s_in.defaultConfig(RX_MODE);
    config.copyFrom(info);
    config.pin_bck = I2S_BCK;
    config.pin_ws = I2S_WS;
    config.pin_data = I2S_DATA_TX;     // TX（未使用但需设置）
    config.pin_data_rx = I2S_DATA_RX;  // RX（音频数据输入）
    config.is_master = true;
    config.use_apll = false;

    i2s_in.begin(config);

    stats_last_ms = millis();
}

void loop() {
    // 读取 I2S 立体声数据
    size_t bytes_read = i2s_in.readBytes(read_buf, sizeof(read_buf));
    if (bytes_read == 0) return;

    // 提取右声道 (Ch1 = ASR beam) 并转为 16-bit
    int stereo_samples = bytes_read / 8;  // 每个立体声样本 8 bytes
    for (int i = 0; i < stereo_samples; i++) {
        // 右声道偏移: i*8 + 4
        int32_t val32 = *(int32_t*)(read_buf + i * 8 + 4);
        // 32-bit → 16-bit: 右移 16 位
        int16_t val16 = (int16_t)(val32 >> 16);
        out_buf[i] = val16;
    }

    // 发送同步头 + 16-bit 单声道 PCM
    Serial.write(SYNC_HEADER, 4);
    Serial.write((uint8_t*)out_buf, stereo_samples * 2);
    Serial.flush();

    stats_frames++;
    stats_bytes += stereo_samples * 2;

    // 每 10 秒统计
    unsigned long now = millis();
    if (now - stats_last_ms >= 10000) {
        float elapsed = (now - stats_last_ms) / 1000.0f;
        // 统计信息（会混入二进制流，但占比极小）
        // Serial.printf 会打断二进制帧，接收端通过同步头重对齐
        stats_frames = 0;
        stats_bytes = 0;
        stats_last_ms = now;
    }
}
