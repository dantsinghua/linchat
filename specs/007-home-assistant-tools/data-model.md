# Data Model: Home Assistant SubAgent

> 本特性不引入新的数据库模型。所有数据为运行时 API 交互，无持久化需求。

## 运行时数据结构

### HAClient 配置

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| base_url | str | settings.HA_URL | HA 实例地址（如 http://192.168.1.100:8123） |
| token | str | settings.HA_TOKEN | Long-Lived Access Token |
| timeout | int | settings.HA_REQUEST_TIMEOUT | HTTP 请求超时（默认 10 秒） |

### HA 设备状态（API 响应格式）

```python
# GET /api/states/{entity_id} 响应
{
    "entity_id": "light.living_room",
    "state": "on",
    "attributes": {
        "friendly_name": "客厅主灯",
        "brightness": 178,
        "color_temp_kelvin": 4000,
        "supported_features": 44,
    },
    "last_changed": "2026-02-05T10:30:15.123456+00:00",
    "last_updated": "2026-02-05T10:30:15.123456+00:00",
}
```

### action 映射表

| action | HA domain | HA service | 必需参数 |
|--------|-----------|------------|----------|
| turn_on | homeassistant | turn_on | — |
| turn_off | homeassistant | turn_off | — |
| toggle | homeassistant | toggle | — |
| set_brightness | light | turn_on | brightness (0-255) |
| set_color | light | turn_on | rgb_color |
| set_color_temp | light | turn_on | color_temp_kelvin |
| set_temperature | climate | set_temperature | temperature |
| set_hvac_mode | climate | set_hvac_mode | hvac_mode |
| set_fan_speed | fan | set_percentage | percentage |
| play | media_player | media_play | — |
| pause | media_player | media_pause | — |
| volume | media_player | volume_set | volume_level (0-1) |
| scene | scene | turn_on | — |
| script | script | turn_on | — |
| lock | lock | lock | — |
| unlock | lock | unlock | — |
| open_cover | cover | open_cover | — |
| close_cover | cover | close_cover | — |

### 敏感操作识别

| 判定条件 | 安全级别 | 处理方式 |
|----------|----------|----------|
| action == "unlock" | L3 敏感 | 返回确认提示 |
| action == "open_cover" 且 entity_id 匹配 cover.garage_* | L3 敏感 | 返回确认提示 |
| action == "turn_off" 且 entity_id 匹配 automation.* | L4 危险 | 返回确认提示 |
| entity_id 在 HA_BLOCKED_ENTITIES 中 | 禁止 | 直接拒绝 |

### 速率限制 Redis Key

| Key 格式 | TTL | 限制 |
|----------|-----|------|
| `ha:control:rate:{user_id}` | 60s | 10 次/分钟 |
| `ha:query:rate:{user_id}` | 60s | 30 次/分钟 |
| `ha:diagnose:rate:{user_id}` | 60s | 5 次/分钟 |

## 状态转换

无状态转换 — 所有操作是无状态的 API 请求/响应。
