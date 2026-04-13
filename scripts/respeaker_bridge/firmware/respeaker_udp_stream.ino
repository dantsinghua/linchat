/**
 * respeaker_udp_stream.ino
 *
 * reSpeaker XVF3800 + XIAO ESP32-S3 WiFi UDP 音频流发送固件
 *
 * 功能:
 *   1. 自动连接 WiFi（断线自动重连）
 *   2. 通过 I2S Master 模式接收 XVF3800 处理后的音频
 *   3. 通过 UDP 将原始 32-bit/2ch PCM 发送到 dev machine 桥接服务
 *   4. 每 10 秒输出帧统计（帧数/字节数/丢帧数）
 *
 * 硬件要求:
 *   - Seeed reSpeaker XVF3800 + XIAO ESP32-S3
 *   - XVF3800 已刷入 I2S Slave 固件 v1.0.4
 *   - 设备与 dev machine 在同一局域网 (192.168.3.x)
 *
 * 使用前:
 *   1. 修改 config.h 中的 WIFI_SSID / WIFI_PASSWORD / UDP_TARGET_IP
 *   2. Arduino IDE 选择 Board: "XIAO_ESP32S3"
 *   3. 编译上传
 *
 * 音频格式: 16kHz / 32-bit signed / 2 channels (interleaved)
 *   - Ch0 (左声道): AEC + 波束成形 + 后处理（会议模式）
 *   - Ch1 (右声道): ASR 自动选择波束（语音识别）
 *   桥接服务负责提取 Ch1 并转为 16-bit 单声道。
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include <driver/i2s.h>
#include "config.h"

/* ======================== 全局变量 ======================== */

WiFiUDP udp;
static uint8_t i2s_read_buf[READ_BUF_SIZE];

// 帧统计
static uint32_t stats_frames      = 0;
static uint32_t stats_bytes       = 0;
static uint32_t stats_udp_fails   = 0;
static uint32_t stats_i2s_errors  = 0;
static unsigned long stats_last_ms = 0;

/* ======================== WiFi ======================== */

static void setup_wifi(void) {
    Serial.printf("[WiFi] Connecting to %s", WIFI_SSID);

    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.persistent(true);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int retry = 0;
    while (WiFi.status() != WL_CONNECTED && retry < WIFI_RETRY_MAX) {
        delay(500);
        Serial.print(".");
        retry++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n[WiFi] Connected! IP: %s, RSSI: %d dBm\n",
                      WiFi.localIP().toString().c_str(), WiFi.RSSI());
    } else {
        Serial.println("\n[WiFi] Initial connection failed, will auto-retry in background");
    }
}

/* ======================== I2S ======================== */

static void setup_i2s(void) {
    /*
     * ESP32-S3 作为 I2S Master:
     *   - 生成 BCLK, WS(LRCK), MCLK 时钟信号
     *   - 从 I2S_DATAO 引脚读取 XVF3800 输出的音频数据
     *
     * XVF3800 I2S Slave 固件 v1.0.4:
     *   - 接收 Master 提供的时钟
     *   - 在 I2S_DATAO 上输出 16kHz/32-bit/2ch 处理后音频
     */
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
        .sample_rate = SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
        .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = DMA_BUF_COUNT,
        .dma_buf_len = DMA_BUF_LEN,
        .use_apll = true,                               // APLL 精确时钟
        .tx_desc_auto_clear = false,
        .fixed_mclk = SAMPLE_RATE * MCLK_MULTIPLE       // 4.096 MHz
    };

    i2s_pin_config_t pin_config = {
        .mck_io_num   = I2S_MCLK_PIN,      // GPIO9  → MCLK
        .bck_io_num   = I2S_BCLK_PIN,      // GPIO8  → BCLK
        .ws_io_num    = I2S_WS_PIN,         // GPIO7  → LRCK
        .data_out_num = I2S_PIN_NO_CHANGE,  // 不输出（无 AEC 参考）
        .data_in_num  = I2S_DATA_IN_PIN     // GPIO44 → I2S_DATAO
    };

    esp_err_t err;

    err = i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
    if (err != ESP_OK) {
        Serial.printf("[I2S] ERROR: driver install failed (%d)\n", err);
        return;
    }

    err = i2s_set_pin(I2S_PORT, &pin_config);
    if (err != ESP_OK) {
        Serial.printf("[I2S] ERROR: pin config failed (%d)\n", err);
        return;
    }

    Serial.printf("[I2S] Master RX: %dHz / 32-bit / 2ch, MCLK=%d Hz\n",
                  SAMPLE_RATE, SAMPLE_RATE * MCLK_MULTIPLE);
    Serial.printf("[I2S] Pins: MCLK=%d, BCLK=%d, WS=%d, DATA_IN=%d\n",
                  I2S_MCLK_PIN, I2S_BCLK_PIN, I2S_WS_PIN, I2S_DATA_IN_PIN);
}

/* ======================== 帧统计 ======================== */

static void print_stats(void) {
    unsigned long now = millis();
    if (now - stats_last_ms < STATS_INTERVAL_MS) return;

    float elapsed = (now - stats_last_ms) / 1000.0f;
    Serial.printf("[Stats] %.0fs: frames=%u, bytes=%u (%.1f KB/s), "
                  "udp_fail=%u, i2s_err=%u, WiFi=%s RSSI=%d\n",
                  elapsed,
                  stats_frames,
                  stats_bytes,
                  stats_bytes / elapsed / 1024.0f,
                  stats_udp_fails,
                  stats_i2s_errors,
                  WiFi.status() == WL_CONNECTED ? "OK" : "DISCONNECTED",
                  WiFi.RSSI());

    stats_frames    = 0;
    stats_bytes     = 0;
    stats_udp_fails = 0;
    stats_i2s_errors = 0;
    stats_last_ms   = now;
}

/* ======================== Setup & Loop ======================== */

void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(1000);  // 等待 Serial 就绪

    Serial.println();
    Serial.println("========================================");
    Serial.println("  reSpeaker XVF3800 UDP Audio Stream");
    Serial.println("  Firmware: v1.0.0");
    Serial.println("========================================");

    setup_wifi();
    setup_i2s();

    udp.begin(0);  // 随机本地端口

    Serial.printf("[UDP] Target: %s:%d\n", UDP_TARGET_IP, UDP_TARGET_PORT);
    Serial.printf("[UDP] Packet size: %d bytes (%.1f ms audio)\n",
                  READ_BUF_SIZE,
                  (float)DMA_BUF_LEN / SAMPLE_RATE * 1000.0f);
    Serial.println("[Main] Streaming started.");

    stats_last_ms = millis();
}

void loop() {
    // WiFi 断线时仍然从 I2S 读取（防止 DMA 溢出），但不发送 UDP
    size_t bytes_read = 0;
    esp_err_t err = i2s_read(I2S_PORT, i2s_read_buf,
                              READ_BUF_SIZE, &bytes_read,
                              portMAX_DELAY);

    if (err != ESP_OK) {
        stats_i2s_errors++;
        return;
    }

    if (bytes_read == 0) return;

    // WiFi 已连接时发送 UDP
    if (WiFi.status() == WL_CONNECTED) {
        int ok = udp.beginPacket(UDP_TARGET_IP, UDP_TARGET_PORT);
        if (ok) {
            udp.write(i2s_read_buf, bytes_read);
            if (udp.endPacket()) {
                stats_frames++;
                stats_bytes += bytes_read;
            } else {
                stats_udp_fails++;
            }
        } else {
            stats_udp_fails++;
        }
    }

    // 周期性统计
    print_stats();
}
