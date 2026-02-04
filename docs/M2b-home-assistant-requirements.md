# M2b: Home Assistant 控制工具 — 细化需求文档

> 基于现有 LangGraph 工具架构（`apps/graph/tools/`）
> 创建日期：2026-02-04

---

## 1. 概述

### 1.1 目标
将 Home Assistant 接入 linchat 的 LangGraph 工具体系，实现自然语言控制智能家居设备。覆盖三大场景：**日常控制、状态查询、诊断修复**。

### 1.2 架构位置
```
apps/graph/tools/
├── context.py      # 上下文工具
├── memory.py       # 记忆工具
├── search.py       # Brave 搜索
├── python_repl.py  # Python 沙箱
├── homeassistant.py  ← 新增
└── __init__.py     # 注册 HA_TOOLS
```

### 1.3 设计原则
- **一个文件、多个工具函数** — 与现有 search.py / python_repl.py 保持一致
- **user_id 隐式注入** — 遵循 R-004 安全规范
- **HA 连接信息从配置读取** — 不硬编码
- **LLM 自主决策调用** — Agent 根据用户意图自动选择工具

---

## 2. 配置

### 2.1 环境变量 / settings.py
```python
# core/settings.py
HA_URL = env("HA_URL", default="")              # http://192.168.1.100:8123
HA_TOKEN = env("HA_TOKEN", default="")           # Long-Lived Access Token
HA_ENABLED = bool(HA_URL and HA_TOKEN)           # 有配置才启用
HA_REQUEST_TIMEOUT = env.int("HA_REQUEST_TIMEOUT", default=10)  # 秒
```

### 2.2 工具注册（条件启用）
```python
# apps/graph/tools/__init__.py
HA_TOOLS = []
if settings.HA_ENABLED:
    from apps.graph.tools.homeassistant import HA_TOOLS
```
**无 HA 配置时，工具列表为空，Agent 不会看到 HA 相关工具。**

---

## 3. 功能需求

### 3.1 设备控制（ha_control）

**工具名：** `ha_control`
**场景：** "开灯"、"关空调"、"把客厅灯调到50%"、"播放音乐"

**支持的操作：**

| 操作 | HA Service | 参数 |
|------|-----------|------|
| 开启设备 | `homeassistant/turn_on` | entity_id, [brightness, color_temp, ...] |
| 关闭设备 | `homeassistant/turn_off` | entity_id |
| 切换开关 | `homeassistant/toggle` | entity_id |
| 设置亮度 | `light/turn_on` | entity_id, brightness(0-255) |
| 设置色温 | `light/turn_on` | entity_id, color_temp_kelvin |
| 设置颜色 | `light/turn_on` | entity_id, rgb_color |
| 设置温度 | `climate/set_temperature` | entity_id, temperature |
| 设置模式 | `climate/set_hvac_mode` | entity_id, hvac_mode |
| 设置风速 | `fan/set_percentage` | entity_id, percentage |
| 播放媒体 | `media_player/media_play` | entity_id |
| 暂停媒体 | `media_player/media_pause` | entity_id |
| 设置音量 | `media_player/volume_set` | entity_id, volume_level(0-1) |
| 触发场景 | `scene/turn_on` | entity_id |
| 执行脚本 | `script/turn_on` | entity_id |
| 锁门 | `lock/lock` | entity_id |
| 解锁 | `lock/unlock` | entity_id |
| 开盖 | `cover/open_cover` | entity_id |
| 关盖 | `cover/close_cover` | entity_id |

**工具定义：**
```python
@tool
async def ha_control(
    entity_id: str,
    action: str,
    params: dict | None = None,
    config: RunnableConfig = None,
) -> str:
    """控制 Home Assistant 设备。
    
    Args:
        entity_id: 设备实体ID，如 light.living_room, switch.kitchen
        action: 操作类型: turn_on / turn_off / toggle / set_temperature / set_brightness 等
        params: 附加参数，如 {"brightness": 128, "color_temp_kelvin": 4000}
    """
```

**action → HA service 映射规则：**
```python
ACTION_MAP = {
    "turn_on": "homeassistant/turn_on",
    "turn_off": "homeassistant/turn_off",
    "toggle": "homeassistant/toggle",
    "set_brightness": "light/turn_on",       # params 中带 brightness
    "set_color": "light/turn_on",            # params 中带 rgb_color
    "set_temperature": "climate/set_temperature",
    "set_hvac_mode": "climate/set_hvac_mode",
    "play": "media_player/media_play",
    "pause": "media_player/media_pause",
    "volume": "media_player/volume_set",
    "lock": "lock/lock",
    "unlock": "lock/unlock",
    "scene": "scene/turn_on",
    "script": "script/turn_on",
}
```

---

### 3.2 状态查询（ha_query）

**工具名：** `ha_query`
**场景：** "客厅温度多少"、"哪些灯开着"、"空调什么模式"、"列出所有设备"

**查询类型：**

| 查询 | API | 返回 |
|------|-----|------|
| 单设备状态 | `GET /api/states/{entity_id}` | 状态 + 属性 |
| 按域查询 | `GET /api/states` + filter | 该域下所有设备状态 |
| 全部设备列表 | `GET /api/states` + 聚合 | 按域分组的设备清单 |
| 历史记录 | `GET /api/history/period/{timestamp}` | 指定时间段的状态变化 |

**工具定义：**
```python
@tool
async def ha_query(
    query_type: str,
    entity_id: str | None = None,
    domain: str | None = None,
    hours: int = 24,
    config: RunnableConfig = None,
) -> str:
    """查询 Home Assistant 设备状态。
    
    Args:
        query_type: 查询类型: state / list / history
        entity_id: 设备实体ID（state/history 时必填）
        domain: 设备域（list 时可选，如 light / switch / climate / sensor）
        hours: 历史查询时间范围，默认24小时
    """
```

**返回格式（给 LLM 阅读）：**
```
# 设备状态: light.living_room
- 名称: 客厅主灯
- 状态: on
- 亮度: 178/255 (70%)
- 色温: 4000K
- 最后变更: 2026-02-04 10:30:15
```

```
# 设备列表 (domain: light)
1. light.living_room — 客厅主灯 — on (70%)
2. light.bedroom — 卧室灯 — off
3. light.kitchen — 厨房灯 — on (100%)
共 3 个灯光设备，2 个开启
```

---

### 3.3 诊断修复（ha_diagnose）

**工具名：** `ha_diagnose`
**场景：** "为什么客厅灯打不开"、"检查一下智能家居系统状态"、"空调好像不响应了"

**诊断能力：**

| 诊断项 | 方法 | 输出 |
|--------|------|------|
| **HA 系统健康** | `GET /api/` + `GET /api/config` | 版本、运行时间、组件数 |
| **设备可达性** | 检查 entity 的 `state` 是否为 `unavailable` / `unknown` | 不可达设备清单 |
| **设备响应** | 对比控制前后状态变化 | 是否响应了控制指令 |
| **自动化状态** | `GET /api/states` filter `automation.*` | 自动化启用/禁用/最后触发 |
| **错误日志** | `GET /api/error_log` | 最近错误日志（截断） |
| **集成状态** | `GET /api/config/config_entries/entry` | 各集成的健康状态 |

**工具定义：**
```python
@tool
async def ha_diagnose(
    check_type: str,
    entity_id: str | None = None,
    config: RunnableConfig = None,
) -> str:
    """诊断 Home Assistant 设备或系统问题。
    
    Args:
        check_type: 诊断类型:
            - system: 系统整体健康检查（版本、运行状态、组件数）
            - device: 单设备诊断（可达性、属性、最近状态变化）
            - unreachable: 扫描所有不可达设备
            - automations: 检查自动化规则状态
            - errors: 获取最近错误日志
        entity_id: 设备诊断时必填
    """
```

**诊断输出示例：**
```
# 🔍 设备诊断: climate.living_room (客厅空调)

## 基本状态
- 状态: unavailable ⚠️
- 最后在线: 2026-02-04 09:15:22 (1小时55分前)

## 可能原因
1. 设备离线 — 检查空调电源和 WiFi 连接
2. 集成异常 — 对应集成可能需要重新认证
3. 网络问题 — HA 与设备之间通信中断

## 建议操作
1. 检查设备物理电源
2. 在 HA 中重新加载对应集成: POST /api/services/homeassistant/reload_config_entry
3. 如问题持续，尝试重启 HA
```

**自动修复能力：**
```python
REPAIR_ACTIONS = {
    "reload_integration": "homeassistant/reload_config_entry",
    "restart_ha": "homeassistant/restart",  # ⚠️ 需二次确认
    "enable_automation": "automation/turn_on",
    "disable_automation": "automation/turn_off",
}
```

**⚠️ 危险操作保护：**
- `restart` 需要在工具返回中明确提示 Agent，由 Agent 向用户确认后再执行
- 工具本身不直接重启，而是返回修复建议 + 确认指令

---

## 4. 安全设计

### 4.1 权限分级

| 级别 | 操作 | 保护措施 |
|------|------|---------|
| **L1 安全** | 查询状态、列出设备、查看日志 | 无限制 |
| **L2 常规** | 开关灯、调亮度、设温度 | 速率限制 |
| **L3 敏感** | 解锁门锁、打开车库门 | Agent 必须先向用户确认 |
| **L4 危险** | 重启 HA、禁用自动化 | 工具返回确认提示，不直接执行 |

### 4.2 速率限制
```python
# Redis 限流（与 Brave Search 同模式）
HA_RATE_LIMITS = {
    "control": "10/min/user",     # 控制操作：每用户每分钟 10 次
    "query": "30/min/user",       # 查询操作：每用户每分钟 30 次
    "diagnose": "5/min/user",     # 诊断操作：每用户每分钟 5 次
}
```

### 4.3 敏感设备白名单/黑名单（可选）
```python
# settings.py
HA_BLOCKED_ENTITIES = env.list("HA_BLOCKED_ENTITIES", default=[])
# 例: ["lock.front_door", "cover.garage"]
# 黑名单中的设备，控制操作直接拒绝
```

---

## 5. Prompt 集成

### 5.1 tool_usage.j2 追加
```jinja2
## 🏠 智能家居工具
你可以控制和查询 Home Assistant 中的智能设备。

**ha_control** — 控制设备
- 开关灯、调亮度/色温、设空调温度、播放音乐等
- entity_id 格式: domain.name（如 light.living_room）
- 不确定 entity_id 时，先用 ha_query(query_type="list") 查看设备列表

**ha_query** — 查询状态
- 查看设备当前状态、属性
- 列出某类设备或全部设备
- 查看设备历史状态变化

**ha_diagnose** — 诊断问题
- 设备不响应时先诊断再修复
- 可检查系统健康、不可达设备、错误日志
- 修复操作会给出建议，需用户确认后执行

**使用顺序建议：**
1. 不知道设备名 → ha_query(list) 先查
2. 控制设备 → ha_control
3. 设备异常 → ha_diagnose(device) 诊断
4. 系统异常 → ha_diagnose(system) + ha_diagnose(errors)
```

---

## 6. 错误处理

| 错误场景 | 处理方式 |
|---------|---------|
| HA 不可达（连接超时） | 返回 "Home Assistant 服务不可达，请检查网络连接" |
| Token 无效（401） | 返回 "HA 认证失败，请检查 Token 配置" |
| 设备不存在（entity_id 无效） | 返回 "未找到设备 {entity_id}，可用 ha_query(list) 查看设备列表" |
| 操作不支持（405） | 返回 "该设备不支持 {action} 操作" |
| 速率限制 | 返回 "操作过于频繁，请稍后再试" |

---

## 7. 技术实现

### 7.1 HTTP 客户端
```python
import httpx

class HAClient:
    """Home Assistant REST API 客户端"""
    
    def __init__(self, url: str, token: str, timeout: int = 10):
        self.base_url = url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self.timeout = timeout
    
    async def get_state(self, entity_id: str) -> dict: ...
    async def get_states(self, domain: str | None = None) -> list[dict]: ...
    async def call_service(self, domain: str, service: str, data: dict) -> dict: ...
    async def get_history(self, entity_id: str, hours: int = 24) -> list: ...
    async def get_error_log(self) -> str: ...
    async def get_config(self) -> dict: ...
    async def check_health(self) -> bool: ...
```

### 7.2 文件结构
```
apps/graph/tools/homeassistant.py   # 工具定义（3 个 @tool 函数）
apps/graph/tools/ha_client.py       # HAClient 类（HTTP 封装）
```

---

## 8. 验收标准

### 8.1 设备控制
- [ ] 支持灯光、开关、空调、风扇、窗帘、门锁、媒体播放器控制
- [ ] 支持场景和脚本触发
- [ ] 敏感操作（门锁等）有确认保护
- [ ] 操作结果返回人类可读的确认信息

### 8.2 状态查询
- [ ] 单设备状态查询含完整属性
- [ ] 设备列表按域分组，显示数量和状态摘要
- [ ] 历史查询返回可读的状态变化时间线

### 8.3 诊断修复
- [ ] 系统健康检查包含版本、组件数、运行状态
- [ ] 能识别 unavailable/unknown 设备并给出可能原因
- [ ] 错误日志截断返回（不超过 2000 字符）
- [ ] 修复建议明确且可操作

### 8.4 安全性
- [ ] 无 HA 配置时工具不注册
- [ ] 速率限制正常工作
- [ ] 黑名单设备无法控制
- [ ] 危险操作不直接执行

### 8.5 可测试性
- [ ] HAClient 有 mock 测试覆盖
- [ ] 3 个工具函数各有单元测试
- [ ] 错误场景有测试覆盖

---

## 9. 排期建议

| 阶段 | 内容 | 预估 |
|------|------|------|
| Phase 1 | HAClient + ha_query（状态查询） | 1 天 |
| Phase 2 | ha_control（设备控制 + 权限） | 1 天 |
| Phase 3 | ha_diagnose（诊断修复） | 1 天 |
| Phase 4 | Prompt 集成 + 测试 + 联调 | 1 天 |

**总计：4 天**

---

## 10. 依赖

- Home Assistant 实例可访问（内网或 frp 穿透）
- Long-Lived Access Token
- httpx（已在项目依赖中）
- Redis（限流，已有基础设施）

---

*文档版本：v1.0*
*创建日期：2026-02-04*
*作者：小鱼*
