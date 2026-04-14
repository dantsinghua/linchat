/**
 * audiotools_record_test.ino - AudioTools 库 I2S 录制测试
 *
 * 用官方推荐的 AudioTools 库读取 I2S，打印文本统计到串口监视器。
 * 分别显示左右声道的值，帮助确认哪个声道有语音。
 *
 * Board: XIAO_ESP32S3, USB CDC On Boot: Enabled
 * 串口监视器: 115200 baud
 */

#include "AudioTools.h"

#define I2S_BCK      8
#define I2S_WS       7
#define I2S_DATA_RX  43
#define I2S_DATA_TX  44

#define SAMPLE_RATE  16000
#define CHANNELS     2
#define BITS         32

I2SStream i2s_in;
AudioInfo info(SAMPLE_RATE, CHANNELS, BITS);

// 128 个立体声样本 × 2ch × 4bytes = 1024 bytes
static uint8_t read_buf[1024];

void setup() {
    Serial.begin(115200);
    delay(2000);

    Serial.println("=== AudioTools I2S 录制测试 ===");
    Serial.printf("BCK=%d, WS=%d, DATA_RX=%d\n", I2S_BCK, I2S_WS, I2S_DATA_RX);
    Serial.printf("Rate=%d, Ch=%d, Bits=%d\n", SAMPLE_RATE, CHANNELS, BITS);
    Serial.println();

    auto config = i2s_in.defaultConfig(RX_MODE);
    config.copyFrom(info);
    config.pin_bck = I2S_BCK;
    config.pin_ws = I2S_WS;
    config.pin_data = I2S_DATA_TX;
    config.pin_data_rx = I2S_DATA_RX;
    config.is_master = true;
    config.use_apll = false;

    i2s_in.begin(config);
    Serial.println("I2S 启动成功，每秒打印统计...\n");
}

void loop() {
    size_t bytes_read = i2s_in.readBytes(read_buf, sizeof(read_buf));
    if (bytes_read == 0) return;

    int stereo_samples = bytes_read / 8;

    // 统计左右声道
    int32_t l_min = INT32_MAX, l_max = INT32_MIN;
    int32_t r_min = INT32_MAX, r_max = INT32_MIN;
    int l_nonzero = 0, r_nonzero = 0;

    for (int i = 0; i < stereo_samples; i++) {
        int32_t left  = *(int32_t*)(read_buf + i * 8);
        int32_t right = *(int32_t*)(read_buf + i * 8 + 4);

        if (left != 0 && left != -1) l_nonzero++;
        if (right != 0 && right != -1) r_nonzero++;
        if (left < l_min) l_min = left;
        if (left > l_max) l_max = left;
        if (right < r_min) r_min = right;
        if (right > r_max) r_max = right;
    }

    // 转 16-bit 看范围
    int16_t l16_min = (int16_t)(l_min >> 16);
    int16_t l16_max = (int16_t)(l_max >> 16);
    int16_t r16_min = (int16_t)(r_min >> 16);
    int16_t r16_max = (int16_t)(r_max >> 16);

    Serial.printf("L: nz=%d/%d 16b=[%d,%d]  R: nz=%d/%d 16b=[%d,%d]",
                  l_nonzero, stereo_samples, l16_min, l16_max,
                  r_nonzero, stereo_samples, r16_min, r16_max);

    if (l_nonzero > stereo_samples / 2 || r_nonzero > stereo_samples / 2) {
        Serial.println(" ✅");
    } else {
        Serial.println(" ❌");
    }

    delay(500);
}
