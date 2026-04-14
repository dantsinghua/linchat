/**
 * config.h - reSpeaker XVF3800 + XIAO ESP32-S3 UDP 音频流配置
 *
 * 使用前必须修改 WIFI_SSID / WIFI_PASSWORD / UDP_TARGET_IP。
 * 其余参数已从 PCB 原理图和 XVF3800 I2S Slave 固件 v1.0.4 确认，
 * 通常无需修改。
 */

#ifndef RESPEAKER_CONFIG_H
#define RESPEAKER_CONFIG_H

/* ======================== WiFi ======================== */
#define WIFI_SSID       "Dan&Huir_5G"
#define WIFI_PASSWORD   "491001Dan"
#define WIFI_RETRY_MAX  60                  // 启动时最大重试次数（×500ms）

/* ======================== UDP 目标 ======================== */
#define UDP_TARGET_IP   "192.168.3.119"     // dev machine LAN IP
#define UDP_TARGET_PORT 12345               // 桥接服务监听端口

/* ======================== I2S 引脚（PCB 原理图确认）========================
 *
 *  reSpeaker 丝印    XIAO 引脚    ESP32-S3 GPIO    方向
 *  ─────────────────────────────────────────────────────
 *  MCLK              D10          GPIO9            ESP32 → XVF3800
 *  I2S_BCLK          D9           GPIO8            ESP32 → XVF3800
 *  I2S_LRCK          D8           GPIO7            ESP32 → XVF3800
 *  I2S_DATAO          D7           GPIO44           XVF3800 → ESP32 (PCB 标注)
 *  I2S_DATAI          D6           GPIO43           ESP32 → XVF3800 (PCB 标注)
 *  ⚠️ 实测: 音频数据实际在 GPIO43 (D6)，非 PCB 标注的 GPIO44 (D7)
 */
#define I2S_BCLK_PIN    8       // GPIO8  → I2S_BCLK
#define I2S_WS_PIN      7       // GPIO7  → I2S_LRCK
#define I2S_DATA_IN_PIN 43      // GPIO43 → 实际音频数据输出（实测确认）
// #define I2S_DATA_OUT_PIN 44  // GPIO44 → 未使用

/* ======================== 音频参数 ========================
 *
 *  XVF3800 I2S Slave 固件 v1.0.4 输出格式：
 *    - 采样率:  16 kHz
 *    - 位深:    32-bit signed
 *    - 通道:    2（左=AEC 处理后, 右=ASR 波束）
 *
 *  ESP32 作为 I2S Master 提供 BCLK/WS 时钟（无需 MCLK，实测确认）。
 */
#define SAMPLE_RATE         16000
#define I2S_PORT            I2S_NUM_0

/* ======================== DMA 缓冲区 ========================
 *
 *  每个 DMA 缓冲区: DMA_BUF_LEN 个立体声样本
 *  每样本 8 bytes (2ch × 4bytes)
 *
 *  DMA_BUF_LEN=128:
 *    - 缓冲区大小 = 128 × 8 = 1024 bytes
 *    - 音频时长   = 128 / 16000 = 8ms
 *    - UDP 包大小 = 1024 bytes（< MTU 1500，无需分片）
 *
 *  DMA_BUF_COUNT=8: 8 个缓冲区轮转，共 64ms 余量。
 */
#define DMA_BUF_LEN     128
#define DMA_BUF_COUNT   8
#define READ_BUF_SIZE   (DMA_BUF_LEN * 2 * 4)  // 1024 bytes

/* ======================== 日志 ======================== */
#define STATS_INTERVAL_MS   10000   // 帧统计日志间隔（ms）
#define SERIAL_BAUD         115200

#endif // RESPEAKER_CONFIG_H
