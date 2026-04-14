/**
 * respeaker_serial_stream.ino
 *
 * reSpeaker XVF3800 + XIAO ESP32-S3 串口音频流发送固件
 *
 * 功能:
 *   1. 通过 I2S Master 模式接收 XVF3800 处理后的音频
 *   2. 通过 USB Serial (CDC) 将原始 PCM 发送到 Windows PC
 *   3. 每帧带 4 字节同步头，便于接收端对齐
 *   4. 每 10 秒在 stderr 输出帧统计
 *
 * 不需要 WiFi，直接 USB 连接即可。
 *
 * 帧格式 (每帧 1028 bytes):
 *   [SYNC: 0xAA 0x55 0x04 0x00] [PCM: 1024 bytes (32-bit/2ch)]
 *
 * 使用:
 *   1. Arduino IDE 选择 Board: "XIAO_ESP32S3"
 *   2. 选择 USB CDC On Boot: "Enabled"
 *   3. 编译上传
 *   4. Windows 上运行 serial_bridge.py 接收
 */

#include <driver/i2s.h>
#include "config.h"

/* ======================== 帧同步头 ======================== */
static const uint8_t SYNC_HEADER[4] = {0xAA, 0x55, 0x04, 0x00};

/* ======================== 全局变量 ======================== */
static uint8_t i2s_read_buf[READ_BUF_SIZE];

// 帧统计
static uint32_t stats_frames     = 0;
static uint32_t stats_bytes      = 0;
static uint32_t stats_i2s_errors = 0;
static unsigned long stats_last_ms = 0;

/* ======================== I2S ======================== */

static void setup_i2s(void) {
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
        .sample_rate = SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
        .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = DMA_BUF_COUNT,
        .dma_buf_len = DMA_BUF_LEN,
        .use_apll = false,
        .tx_desc_auto_clear = false,
        .fixed_mclk = 0
    };

    i2s_pin_config_t pin_config = {
        .mck_io_num   = I2S_PIN_NO_CHANGE,
        .bck_io_num   = I2S_BCLK_PIN,
        .ws_io_num    = I2S_WS_PIN,
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num  = I2S_DATA_IN_PIN
    };

    i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
    i2s_set_pin(I2S_PORT, &pin_config);
}

/* ======================== 帧统计 ======================== */

static void print_stats(void) {
    unsigned long now = millis();
    if (now - stats_last_ms < STATS_INTERVAL_MS) return;

    float elapsed = (now - stats_last_ms) / 1000.0f;
    // 统计信息打印到 stderr (不影响二进制数据流)
    Serial.printf("[Stats] %.0fs: frames=%u, KB/s=%.1f, i2s_err=%u\n",
                  elapsed, stats_frames,
                  stats_bytes / elapsed / 1024.0f,
                  stats_i2s_errors);

    stats_frames    = 0;
    stats_bytes     = 0;
    stats_i2s_errors = 0;
    stats_last_ms   = now;
}

/* ======================== Setup & Loop ======================== */

void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(500);

    setup_i2s();

    stats_last_ms = millis();
}

void loop() {
    size_t bytes_read = 0;
    esp_err_t err = i2s_read(I2S_PORT, i2s_read_buf,
                              READ_BUF_SIZE, &bytes_read,
                              portMAX_DELAY);

    if (err != ESP_OK) {
        stats_i2s_errors++;
        return;
    }
    if (bytes_read == 0) return;

    // 发送同步头 + PCM 数据
    Serial.write(SYNC_HEADER, 4);
    Serial.write(i2s_read_buf, bytes_read);
    Serial.flush();

    stats_frames++;
    stats_bytes += bytes_read;

    print_stats();
}
