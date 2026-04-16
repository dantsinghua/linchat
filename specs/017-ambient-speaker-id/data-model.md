# Data Model: Ambient 模式说话人识别

**Date**: 2026-04-15 | **Branch**: `017-ambient-speaker-id`

## 已有实体（无改动）

### SpeakerProfile

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| user | OneToOneField → SysUser | NOT NULL | 关联用户 |
| gateway_speaker_id | CharField(100) | UNIQUE | Gateway 声纹 ID |
| name | CharField(50) | NOT NULL | 说话人名称 |
| quality_score | FloatField | nullable | 声纹质量分 |
| enrolled_at | DateTimeField | nullable | 注册时间 |
| created_at | DateTimeField | auto_now_add | 创建时间 |
| updated_at | DateTimeField | auto_now | 更新时间 |

### Message（已有字段，本特性填充）

| 字段 | 类型 | 约束 | 当前状态 | 本特性改动 |
|------|------|------|---------|-----------|
| speaker_id | CharField(100) | nullable | 存在但未填充 | ambient 持久化时写入 |

## 新增 Redis 数据结构

### TTS 播放状态

| Key | 类型 | TTL | 说明 |
|-----|------|-----|------|
| `voice:tts_playing:{user_id}` | String "1" | 30s | TTS 正在播放标记 |
| `voice:tts_history:{user_id}` | List (最近 10 条 TTS 文本) | 300s | 文本比对用 |

### 临时说话人标签（持久化）

| Key | 类型 | TTL | 说明 |
|-----|------|-----|------|
| `voice:unknown_speakers` | Hash {embedding_hash → label} | 无 | 全局临时标签映射 |
| `voice:unknown_counter` | String (integer) | 无 | 全局自增计数器 |

## 新增 Settings

| 设置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `VOICE_SPEAKER_IDENTIFICATION_ENABLED` | bool | False | 功能开关 |

## 已有 Settings（复用）

| 设置项 | 当前值 | 用途 |
|--------|--------|------|
| `VOICE_SPEAKER_THRESHOLD` | 0.5 | 识别置信度阈值 |
| `VOICE_AUDIO_CACHE_TTL` | (已配置) | PCM 缓存 TTL |

## 状态转换

### 说话人识别结果

```
语音段 PCM → [功能开关检查]
  ├── 关闭 → 跳过识别，使用连接 user_id
  └── 开启 → [音频长度检查]
        ├── < 0.5s → 跳过识别，标记"未识别"
        └── >= 0.5s → [Gateway identify]
              ├── identified=True, confidence >= threshold → 已注册用户 (user_id + username)
              ├── identified=False → [临时标签查找]
              │     ├── embedding_hash 已存在 → 复用已有标签
              │     └── embedding_hash 不存在 → 分配新标签 (unknown_XX)
              └── Gateway 异常 → 降级到连接 user_id
```

### 临时标签生命周期

```
首次出现 → INCR voice:unknown_counter → HSET voice:unknown_speakers {hash} unknown_{N}
再次出现 → HGET voice:unknown_speakers {hash} → 复用 unknown_{N}
用户注册声纹 → 全量匹配 Message.speaker_id → 替换 unknown_{N} → HDEL → 更新前端
```

## 数据库迁移

**零迁移** — `Message.speaker_id` 字段已存在，`SpeakerProfile` 模型已就绪。
