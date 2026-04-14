/**
 * audiotools_slave_test.ino - ESP32 作为 I2S Slave 接收 XVF3800 Master 音频
 *
 * XVF3800 运行 i2s_master_48k 固件，ESP32 作为 Slave 跟随其时钟。
 * 打印文本统计到串口监视器，验证音频质量。
 *
 * Board: XIAO_ESP32S3, USB CDC On Boot: Enabled
 */

#include "AudioTools.h"

#define I2S_BCK      8
#define I2S_WS       7
#define I2S_DATA_RX  43
#define I2S_DATA_TX  44

// XVF3800 master 固件输出 48kHz
#define SAMPLE_RATE  48000
#define CHANNELS     2
#define BITS         32

I2SStream i2s_in;
AudioInfo info(SAMPLE_RATE, CHANNELS, BITS);

// 读取缓冲区
static uint8_t read_buf[1024];

void setup() {
    Serial.begin(115200);
    delay(2000);

    Serial.println("=== ESP32 I2S Slave 模式测试 ===");
    Serial.printf("BCK=%d, WS=%d, DATA_RX=%d\n", I2S_BCK, I2S_WS, I2S_DATA_RX);
    Serial.printf("Rate=%d (XVF3800 master), Ch=%d, Bits=%d\n", SAMPLE_RATE, CHANNELS, BITS);
    Serial.printf("ESP32 角色: SLAVE (跟随 XVF3800 时钟)\n\n");

    auto config = i2s_in.defaultConfig(RX_MODE);
    config.copyFrom(info);
    config.pin_bck = I2S_BCK;
    config.pin_ws = I2S_WS;
    config.pin_data = I2S_DATA_TX;
    config.pin_data_rx = I2S_DATA_RX;
    config.is_master = false;   // ← Slave 模式，关键！
    config.use_apll = false;

    i2s_in.begin(config);
    Serial.println("I2S Slave 启动成功\n");
}

void loop() {
    size_t bytes_read = i2s_in.readBytes(read_buf, sizeof(read_buf));
    if (bytes_read == 0) return;

    int stereo_samples = bytes_read / 8;

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

    int16_t l16_min = (int16_t)(l_min >> 16);
    int16_t l16_max = (int16_t)(l_max >> 16);
    int16_t r16_min = (int16_t)(r_min >> 16);
    int16_t r16_max = (int16_t)(r_max >> 16);

    Serial.printf("L: nz=%d/%d 16b=[%d,%d]  R: nz=%d/%d 16b=[%d,%d]",
                  l_nonzero, stereo_samples, l16_min, l16_max,
                  r_nonzero, stereo_samples, r16_min, r16_max);

    if (l_nonzero == 0 && r_nonzero == 0) {
        Serial.println(" ❌ 无数据");
    } else if (abs(l16_max) < 1000 && abs(l16_min) < 1000) {
        Serial.println(" 🔇 安静");
    } else {
        Serial.println(" ✅");
    }

    delay(500);
}
