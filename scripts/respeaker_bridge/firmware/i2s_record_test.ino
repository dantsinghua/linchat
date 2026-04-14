/**
 * i2s_record_test.ino - 用已验证的 I2S 配置录制音频
 *
 * 完全复用你之前测试成功的 I2S 配置（driver/i2s.h, GPIO43, 无 MCLK）。
 * 读取 I2S 数据，通过 Serial 发送文本格式的样本值和统计，
 * 同时发送带同步头的二进制 PCM 供 quick_record.py 录制。
 *
 * 模式切换：
 *   启动后默认 TEXT 模式（串口监视器查看）
 *   发送 'b' 切换到 BINARY 模式（quick_record.py 录制）
 *   发送 't' 切回 TEXT 模式
 */

#include "driver/i2s.h"

// 完全复用你验证过的配置
#define I2S_WS   7
#define I2S_SCK  8
#define I2S_SD   43   // 你验证过能工作的数据引脚

#define SAMPLE_RATE     16000
#define BUFFER_SIZE     1024

// 同步头（v2 格式）
static const uint8_t SYNC_HEADER[4] = {0xAA, 0x55, 0x01, 0x00};

static bool binary_mode = false;
static int32_t buffer[BUFFER_SIZE];

void setup() {
    Serial.begin(115200);
    while (!Serial);
    delay(500);
    Serial.println("=== reSpeaker I2S 录制测试 ===");
    Serial.println("发送 'b' 切换到二进制模式(录制)");
    Serial.println("发送 't' 切换到文本模式(查看)");
    Serial.println();

    // 完全复用你验证过的 I2S 配置
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
        .sample_rate = SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
        .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 4,
        .dma_buf_len = 256,
        .use_apll = false,
        .tx_desc_auto_clear = false,
        .fixed_mclk = 0
    };

    i2s_pin_config_t pin_config = {
        .mck_io_num = I2S_PIN_NO_CHANGE,
        .bck_io_num = I2S_SCK,
        .ws_io_num  = I2S_WS,
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num  = I2S_SD
    };

    esp_err_t err;
    err = i2s_driver_install(I2S_NUM_0, &i2s_config, 0, NULL);
    Serial.printf("I2S driver: %s\n", err == ESP_OK ? "OK" : "FAIL");

    err = i2s_set_pin(I2S_NUM_0, &pin_config);
    Serial.printf("I2S pins: %s\n", err == ESP_OK ? "OK" : "FAIL");

    i2s_zero_dma_buffer(I2S_NUM_0);
    Serial.println("I2S 初始化完成");
    Serial.println();
}

void loop() {
    // 检查串口命令
    if (Serial.available()) {
        char c = Serial.read();
        if (c == 'b' || c == 'B') {
            binary_mode = true;
            // 不打印，避免干扰二进制流
        } else if (c == 't' || c == 'T') {
            binary_mode = false;
            Serial.println("\n=== 切换到文本模式 ===");
        }
    }

    size_t bytes_read = 0;
    i2s_read(I2S_NUM_0, buffer, sizeof(buffer), &bytes_read, portMAX_DELAY);
    int samples = bytes_read / 4;

    if (binary_mode) {
        // 二进制模式：转 16-bit 单声道，带同步头
        // buffer 是 int32 交替 L/R，提取左声道（偶数索引）
        int mono_count = samples / 2;
        int16_t out_buf[512];
        for (int i = 0; i < mono_count && i < 512; i++) {
            // 左声道 = 偶数索引 (i*2), 右声道 = 奇数索引 (i*2+1)
            // 先试左声道
            int32_t val = buffer[i * 2];
            out_buf[i] = (int16_t)(val >> 16);
        }
        Serial.write(SYNC_HEADER, 4);
        Serial.write((uint8_t*)out_buf, mono_count * 2);
        Serial.flush();
    } else {
        // 文本模式：打印统计
        int nonZero = 0;
        int32_t maxVal = 0;
        int32_t minVal = 0;

        for (int i = 0; i < samples; i++) {
            if (buffer[i] != 0 && buffer[i] != -1) nonZero++;
            if (buffer[i] > maxVal) maxVal = buffer[i];
            if (buffer[i] < minVal) minVal = buffer[i];
        }

        Serial.printf("samples=%d nonZero=%d min=%d max=%d", samples, nonZero, minVal, maxVal);

        if (nonZero > samples / 2) {
            Serial.println(" ✅ 有数据");
        } else if (nonZero > 0) {
            Serial.println(" ⚠️ 部分数据");
        } else {
            Serial.println(" ❌ 无数据");
        }

        // 每隔一段打印前 4 个立体声样本的详细值
        static int text_count = 0;
        if (++text_count % 10 == 0) {
            Serial.println("  --- 前 4 个立体声样本 (L, R) ---");
            for (int i = 0; i < 8 && i < samples; i += 2) {
                int32_t left = buffer[i];
                int32_t right = buffer[i + 1];
                int16_t l16 = (int16_t)(left >> 16);
                int16_t r16 = (int16_t)(right >> 16);
                Serial.printf("  [%d] L=%d (16b:%d)  R=%d (16b:%d)\n",
                              i / 2, left, l16, right, r16);
            }
        }

        delay(1000);
    }
}
