/**
 * i2s_diagnose.ino - reSpeaker XVF3800 I2S 诊断固件
 *
 * 仅输出文本诊断信息到串口，不发送二进制数据。
 * 用 Arduino 串口监视器 (115200 baud) 查看。
 *
 * 检查项:
 *   1. I2S 驱动是否安装成功
 *   2. I2S 是否能读到数据
 *   3. 数据是否全 0xFF（无信号）还是有效值
 *   4. 左右声道分别的值范围
 */

#include <driver/i2s.h>
#include "config.h"

static uint8_t read_buf[READ_BUF_SIZE];

void setup() {
    Serial.begin(115200);
    delay(2000);  // 等待串口就绪

    Serial.println();
    Serial.println("================================");
    Serial.println("  reSpeaker I2S 诊断工具");
    Serial.println("================================");
    Serial.println();

    // 打印配置
    Serial.printf("BCLK Pin:    GPIO%d (D9)\n", I2S_BCLK_PIN);
    Serial.printf("WS Pin:      GPIO%d (D8)\n", I2S_WS_PIN);
    Serial.printf("DATA_IN Pin: GPIO%d (D6)\n", I2S_DATA_IN_PIN);
    Serial.printf("MCLK:        不使用\n");
    Serial.printf("Sample Rate: %d Hz\n", SAMPLE_RATE);
    Serial.printf("DMA Buf:     %d x %d\n", DMA_BUF_COUNT, DMA_BUF_LEN);
    Serial.printf("Read Size:   %d bytes\n", READ_BUF_SIZE);
    Serial.println();

    // 安装 I2S 驱动
    Serial.println("[1] Installing I2S driver...");

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

    esp_err_t err = i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
    if (err != ESP_OK) {
        Serial.printf("  FAIL: i2s_driver_install error %d\n", err);
        Serial.println("  停止。检查 I2S 配置。");
        while(1) delay(1000);
    }
    Serial.println("  OK: driver installed");

    // 配置引脚
    Serial.println("[2] Setting I2S pins...");

    i2s_pin_config_t pin_config = {
        .mck_io_num   = I2S_PIN_NO_CHANGE,
        .bck_io_num   = I2S_BCLK_PIN,
        .ws_io_num    = I2S_WS_PIN,
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num  = I2S_DATA_IN_PIN
    };

    err = i2s_set_pin(I2S_PORT, &pin_config);
    if (err != ESP_OK) {
        Serial.printf("  FAIL: i2s_set_pin error %d\n", err);
        while(1) delay(1000);
    }
    Serial.println("  OK: pins configured");

    // 等待 XVF3800 启动
    Serial.println("[3] Waiting 2s for XVF3800 to boot...");
    delay(2000);

    // 连续读取 5 次，每次打印样本
    Serial.println("[4] Reading I2S data (5 rounds)...");
    Serial.println();

    for (int round = 0; round < 5; round++) {
        size_t bytes_read = 0;
        err = i2s_read(I2S_PORT, read_buf, READ_BUF_SIZE, &bytes_read, 1000);

        Serial.printf("--- Round %d: err=%d, bytes_read=%d ---\n", round + 1, err, bytes_read);

        if (err != ESP_OK || bytes_read == 0) {
            Serial.println("  No data!");
            continue;
        }

        // 检查是否全 0xFF
        bool all_ff = true;
        bool all_zero = true;
        for (int i = 0; i < bytes_read; i++) {
            if (read_buf[i] != 0xFF) all_ff = false;
            if (read_buf[i] != 0x00) all_zero = false;
        }

        if (all_ff) {
            Serial.println("  ⚠️  ALL 0xFF - I2S data line floating (no XVF3800 output)");
        } else if (all_zero) {
            Serial.println("  ⚠️  ALL 0x00 - I2S data line grounded");
        } else {
            Serial.println("  ✅ Data has varying values!");
        }

        // 打印前 8 个立体声样本
        Serial.println("  Sample    Left(Ch0)      Right(Ch1)     Hex(L)     Hex(R)");
        int sample_count = min(8, (int)(bytes_read / 8));
        for (int i = 0; i < sample_count; i++) {
            int32_t left  = *(int32_t*)(read_buf + i * 8);
            int32_t right = *(int32_t*)(read_buf + i * 8 + 4);
            Serial.printf("  %3d     %12d   %12d   %08X   %08X\n",
                          i, left, right,
                          *(uint32_t*)(read_buf + i * 8),
                          *(uint32_t*)(read_buf + i * 8 + 4));
        }

        // 统计
        int32_t l_min = INT32_MAX, l_max = INT32_MIN;
        int32_t r_min = INT32_MAX, r_max = INT32_MIN;
        int nonzero_l = 0, nonzero_r = 0;

        for (int i = 0; i < (int)(bytes_read / 8); i++) {
            int32_t l = *(int32_t*)(read_buf + i * 8);
            int32_t r = *(int32_t*)(read_buf + i * 8 + 4);
            if (l < l_min) l_min = l;
            if (l > l_max) l_max = l;
            if (r < r_min) r_min = r;
            if (r > r_max) r_max = r;
            if (l != 0 && l != -1) nonzero_l++;
            if (r != 0 && r != -1) nonzero_r++;
        }

        Serial.printf("  Left:  min=%d, max=%d, nonzero=%d/%d\n",
                      l_min, l_max, nonzero_l, (int)(bytes_read / 8));
        Serial.printf("  Right: min=%d, max=%d, nonzero=%d/%d\n",
                      r_min, r_max, nonzero_r, (int)(bytes_read / 8));
        Serial.println();

        delay(500);
    }

    Serial.println("================================");
    Serial.println("诊断完成。");
    Serial.println();
    Serial.println("如果全是 0xFF:");
    Serial.println("  1. 确认 XVF3800 USB-C 口已供电");
    Serial.println("  2. 确认已刷 I2S Slave 固件 (非 USB 固件)");
    Serial.println("  3. 尝试断电重启整个板子");
    Serial.println("  4. 检查 XIAO 模块是否插紧在底板上");
    Serial.println();
    Serial.println("如果有数据但 ASR 无转录:");
    Serial.println("  1. 检查提取的是否是正确的声道");
    Serial.println("  2. 检查位深转换是否正确");
}

void loop() {
    // 持续读取并每 3 秒打印一次摘要
    delay(3000);

    size_t bytes_read = 0;
    // 先丢弃积压数据
    for (int i = 0; i < 10; i++) {
        i2s_read(I2S_PORT, read_buf, READ_BUF_SIZE, &bytes_read, 100);
    }
    // 再读一帧新数据
    esp_err_t err = i2s_read(I2S_PORT, read_buf, READ_BUF_SIZE, &bytes_read, 1000);

    if (err == ESP_OK && bytes_read > 0) {
        bool all_ff = true;
        for (int i = 0; i < bytes_read; i++) {
            if (read_buf[i] != 0xFF) { all_ff = false; break; }
        }

        int32_t l0 = *(int32_t*)(read_buf);
        int32_t r0 = *(int32_t*)(read_buf + 4);

        if (all_ff) {
            Serial.println("[Loop] Still 0xFF - no I2S signal");
        } else {
            Serial.printf("[Loop] L=%d, R=%d (has signal)\n", l0, r0);
        }
    }
}
