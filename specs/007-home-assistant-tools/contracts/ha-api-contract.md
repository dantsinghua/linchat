# HA REST API Contract

> HAClient 对 Home Assistant REST API 的接口约定

## 认证

所有请求携带 Bearer Token:
```
Authorization: Bearer {HA_TOKEN}
Content-Type: application/json
```

## 端点

### 1. 获取单个设备状态

```
GET /api/states/{entity_id}
```

**Response** (200):
```json
{
  "entity_id": "light.living_room",
  "state": "on",
  "attributes": {
    "friendly_name": "客厅主灯",
    "brightness": 178,
    "color_temp_kelvin": 4000
  },
  "last_changed": "2026-02-05T10:30:15+00:00",
  "last_updated": "2026-02-05T10:30:15+00:00"
}
```

**Errors**: 404 设备不存在, 401 Token 无效

### 2. 获取所有设备状态

```
GET /api/states
```

**Response** (200): `list[state_object]` — 返回所有设备状态的数组

### 3. 调用服务

```
POST /api/services/{domain}/{service}
```

**Request Body**:
```json
{
  "entity_id": "light.living_room",
  "brightness": 128
}
```

**Response** (200): 受影响的设备状态列表

### 4. 获取历史记录

```
GET /api/history/period/{timestamp}?filter_entity_id={entity_id}&minimal_response
```

**Parameters**:
- `timestamp`: ISO 格式起始时间
- `filter_entity_id`: 过滤设备
- `minimal_response`: 减少返回数据量

**Response** (200): `list[list[state_change]]`

### 5. 获取错误日志

```
GET /api/error_log
```

**Response** (200): 纯文本日志内容

### 6. 获取系统配置

```
GET /api/config
```

**Response** (200):
```json
{
  "version": "2024.12.1",
  "components": ["light", "switch", "climate", ...],
  "unit_system": {"temperature": "°C"}
}
```

### 7. 健康检查

```
GET /api/
```

**Response** (200):
```json
{"message": "API running."}
```

## HAClient 方法签名

```python
class HAClient:
    async def get_state(self, entity_id: str) -> dict
    async def get_states(self, domain: str | None = None) -> list[dict]
    async def call_service(self, domain: str, service: str, data: dict) -> list[dict]
    async def get_history(self, entity_id: str, hours: int = 24) -> list[list[dict]]
    async def get_error_log(self) -> str
    async def get_config(self) -> dict
    async def check_health(self) -> bool
```

## 错误映射

| HTTP Status | HAClient 行为 | 工具返回文本 |
|-------------|--------------|-------------|
| 200 | 返回 parsed JSON | 格式化的结果 |
| 401 | 抛出 HAAuthError | "HA 认证失败，请检查 Token 配置" |
| 404 | 抛出 HANotFoundError | "未找到设备 {entity_id}" |
| 连接超时 | 抛出 HAConnectionError | "Home Assistant 服务不可达" |
| 其他 4xx/5xx | 抛出 HAError | "HA 服务返回错误: {status}" |
