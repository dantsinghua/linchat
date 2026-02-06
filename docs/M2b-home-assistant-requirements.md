# M2b: Home Assistant SubAgent — 需求文档

> 基于 SubAgent 架构（`apps/graph/subagents/`）
> 创建日期：2026-02-04
> 更新日期：2026-02-05（适配 SubAgent 架构）

---

## 1. 概述

### 1.1 目标
将 Home Assistant 接入 linchat 的 SubAgent 体系，实现自然语言控制智能家居设备。覆盖三大场景：**日常控制、状态查询、诊断修复**。

### 1.2 架构位置

本特性遵循 `006-subagent-tools` 确立的 SubAgent 架构模式。**主 agent 不直接调用 HA 工具**，而是通过 `ha_subagent` 委派任务，subagent 内部管理 3 个 HA 工具。

```
apps/graph/
├── subagents/
│   ├── __init__.py          # 注册表：条件加载 ha_subagent
│   ├── base.py              # run_subagent() 工厂函数（已有）
│   ├── search_agent.py      # 搜索 SubAgent（已有）
│   ├── memory_agent.py      # 记忆 SubAgent（已有）
│   ├── code_agent.py        # 代码 SubAgent（已有）
│   └── ha_agent.py          # ← 新增：HA SubAgent
├── tools/
│   ├── homeassistant.py     # ← 新增：3 个 HA @tool 函数
│   └── ha_client.py         # ← 新增：HAClient HTTP 封装
```

### 1.3 SubAgent 模式说明

参考现有 `memory_agent.py` 的实现模式：

```python
# ha_agent.py 的核心结构
@tool
async def ha_subagent(task: str, config: RunnableConfig) -> str:
    """控制和查询智能家居设备。当用户需要控制灯光、空调、窗帘等设备，
    查询设备状态，或诊断设备问题时使用。"""
    return await run_subagent(
        task, config, list(HA_TOOLS), HA_PROMPT, name="ha_subagent"
    )
```

**关键点：**
- `ha_subagent` 对主 agent 而言是一个 tool，主 agent 只传入任务描述
- subagent 内部通过 `create_react_agent` 自主管理 `ha_query`、`ha_control`、`ha_diagnose` 三个工具
- `base.py` 自动注入公共工具（`mem_search`、`web_search`），subagent 可用于补充上下文
- 统一 60 秒超时（`SUBAGENT_TIMEOUT`）

### 1.4 设计原则
- **SubAgent 封装** — HA 工具不暴露给主 agent，由 ha_subagent 内部管理
- **条件注册** — 无 HA 配置时 subagent 不注册，不影响其他功能
- **user_id 隐式注入** — 通过 RunnableConfig 传递
- **自主决策** — subagent 内部 LLM 自行决定调用哪个工具、什么顺序

---

## 2. 配置

### 2.1 环境变量 / settings.py
```python
# core/settings.py 新增
HA_URL = env("HA_URL", default="")              # http://192.168.1.100:8123
HA_TOKEN = env("HA_TOKEN", default="")           # Long-Lived Access Token
HA_ENABLED = bool(HA_URL and HA_TOKEN)           # 有配置才启用
HA_REQUEST_TIMEOUT = env.int("HA_REQUEST_TIMEOUT", default=10)  # 秒
HA_BLOCKED_ENTITIES = env.list("HA_BLOCKED_ENTITIES", default=[])  # 黑名单
```

### 2.2 SubAgent 注册（条件启用）
```python
# apps/graph/subagents/__init__.py 新增
if getattr(settings, "HA_ENABLED", False):
    from .ha_agent import ha_subagent
    tools.append(ha_subagent)
```

**无 HA 配置时，ha_subagent 不注册，主 agent 不会看到任何 HA 相关能力。**

---

## 3. SubAgent 定义

### 3.1 ha_agent.py

```python
"""Home Assistant SubAgent — 智能家居控制助手

通过 run_subagent() 创建内部 react agent，
管理 ha_query / ha_control / ha_diagnose 三个专属工具，
自动注入公共工具（mem_search + web_search）。
"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.graph.subagents.base import run_subagent
from apps.graph.tools.homeassistant import HA_TOOLS

HA_PROMPT = """你是智能家居控制助手，帮助用户通过自然语言控制和查询 Home Assistant 设备。

## 可用工具

### ha_query — 状态查询
- query_type="state": 查询单个设备的详细状态（需要 entity_id）
- query_type="list": 查询设备列表（可选 domain 过滤）
- query_type="history": 查询设备历史记录（需要 entity_id，默认 24 小时）

### ha_control — 设备控制
- entity_id: 设备ID（如 light.living_room）
- action: 操作类型（turn_on/turn_off/toggle/set_brightness/set_temperature 等）
- params: 附加参数（如 {"brightness": 128}）

### ha_diagnose — 诊断修复
- diagnose_type="health": 系统健康检查
- diagnose_type="device": 单设备诊断（需要 entity_id）
- diagnose_type="offline_scan": 扫描离线设备
- diagnose_type="automations": 自动化规则状态
- diagnose_type="error_log": 查看错误日志

## 执行策略

1. **设备名模糊时**：先用 ha_query(query_type="list") 查看设备列表，找到正确的 entity_id
2. **控制前可选查询**：可用 ha_query(query_type="state") 确认设备当前状态
3. **设备不响应时**：用 ha_diagnose(diagnose_type="device") 诊断原因
4. **查询用户偏好**：可用 mem_search 查询历史偏好（如"平时灯亮度多少"）

## 安全规则（必须遵守）

### 敏感操作 — 返回确认提示，不直接执行
- L3 操作：unlock（解锁门锁）、open_cover 针对 cover.garage_*（开车库门）
- L4 操作：turn_off 针对 automation.*（禁用自动化规则）

当检测到敏感操作时，ha_control 会自动返回确认提示，你需要将该提示原样返回给用户。

### 黑名单设备
某些设备已被管理员禁止通过聊天控制。如果操作被拒绝，向用户解释该设备不可通过聊天控制。

## 响应规范

- 使用中文，简洁友好
- 操作成功：返回"✅ 已执行: [操作描述]"+ 当前状态
- 操作失败：返回错误原因 + 建议操作
- 查询结果：格式化输出，包含关键属性
- 独立完成任务，不要请求主 agent 补充信息"""


@tool
async def ha_subagent(task: str, config: RunnableConfig) -> str:
    """控制和查询智能家居设备。当用户需要控制灯光、空调、窗帘、
    开关等设备，查询设备状态，或诊断智能家居问题时使用。"""
    return await run_subagent(
        task, config, list(HA_TOOLS), HA_PROMPT, name="ha_subagent"
    )
```

---

## 4. 工具定义（SubAgent 内部工具）

以下 3 个工具由 `ha_subagent` 内部使用，主 agent 不可见。

### 4.1 设备控制（ha_control）

**场景：** "开灯"、"关空调"、"把客厅灯调到50%"、"播放音乐"

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

**action → HA service 映射：**

| action | HA Service | 附加参数 |
|--------|-----------|---------|
| turn_on | homeassistant/turn_on | brightness, color_temp 等 |
| turn_off | homeassistant/turn_off | — |
| toggle | homeassistant/toggle | — |
| set_brightness | light/turn_on | brightness(0-255) |
| set_color | light/turn_on | rgb_color |
| set_color_temp | light/turn_on | color_temp_kelvin |
| set_temperature | climate/set_temperature | temperature |
| set_hvac_mode | climate/set_hvac_mode | hvac_mode |
| set_fan_speed | fan/set_percentage | percentage |
| play | media_player/media_play | — |
| pause | media_player/media_pause | — |
| volume | media_player/volume_set | volume_level(0-1) |
| scene | scene/turn_on | — |
| script | script/turn_on | — |
| lock | lock/lock | — |
| unlock | lock/unlock | — |
| open_cover | cover/open_cover | — |
| close_cover | cover/close_cover | — |

**返回格式：**
```
✅ 已执行: 开启 客厅主灯 (light.living_room)
当前状态: on, 亮度 178/255 (70%)
```

**敏感操作返回：**
```
⚠️ 敏感操作确认
即将执行: 解锁 前门门锁 (lock.front_door)
这是一个涉及安全的操作，请确认是否继续。
回复"确认解锁"以执行，或"取消"以放弃。
```

### 4.2 状态查询（ha_query）

**场景：** "客厅温度多少"、"哪些灯开着"、"空调什么模式"

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

**query_type → API 映射：**

| query_type | API | 说明 |
|-----------|-----|------|
| state | GET /api/states/{entity_id} | 单设备状态 + 属性 |
| list | GET /api/states + filter | 按域分组的设备清单 |
| history | GET /api/history/period/{timestamp} | 状态变化时间线 |

**返回格式示例：**
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

### 4.3 诊断修复（ha_diagnose）

**场景：** "为什么客厅灯打不开"、"检查智能家居系统"

**工具定义：**
```python
@tool
async def ha_diagnose(
    diagnose_type: str,
    entity_id: str | None = None,
    config: RunnableConfig = None,
) -> str:
    """诊断 Home Assistant 设备或系统问题。

    Args:
        diagnose_type: 诊断类型:
            - system: 系统健康检查（版本、运行状态）
            - device: 单设备诊断（可达性、最近状态变化）
            - unreachable: 扫描所有不可达设备
            - automations: 检查自动化规则状态
            - errors: 获取最近错误日志
        entity_id: 设备诊断时必填
    """
```

**诊断输出示例：**
```
# 🏠 Home Assistant 系统状态
- 版本: 2024.12.1
- 运行时间: 15天3小时
- 组件数: 42
- 自动化: 12个 (11个启用, 1个禁用)
- 状态: 正常 ✅
```

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
2. 在 HA 中重新加载对应集成
3. 如问题持续，尝试重启 HA（需确认）
```

**工具导出：**
```python
# homeassistant.py 末尾
HA_TOOLS = [ha_query, ha_control, ha_diagnose]
```

---

## 5. HTTP 客户端

### 5.1 ha_client.py

```python
"""Home Assistant REST API 客户端"""
import httpx
from django.conf import settings

class HAClient:
    def __init__(self):
        self.base_url = settings.HA_URL.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {settings.HA_TOKEN}",
            "Content-Type": "application/json",
        }
        self.timeout = settings.HA_REQUEST_TIMEOUT

    async def get_state(self, entity_id: str) -> dict: ...
    async def get_states(self, domain: str | None = None) -> list[dict]: ...
    async def call_service(self, domain: str, service: str, data: dict) -> list[dict]: ...
    async def get_history(self, entity_id: str, hours: int = 24) -> list: ...
    async def get_error_log(self) -> str: ...
    async def get_config(self) -> dict: ...
    async def check_health(self) -> bool: ...
```

所有方法使用 `httpx.AsyncClient`，统一超时处理和异常转换。

---

## 6. 安全设计

### 6.1 权限分级

| 级别 | 操作 | 保护措施 |
|------|------|---------|
| L1 安全 | 查询状态、列出设备、查看日志 | 无限制 |
| L2 常规 | 开关灯、调亮度、设温度 | 速率限制 |
| L3 敏感 | 解锁门锁、打开车库门 | subagent 返回确认提示，主 agent 转达用户 |
| L4 危险 | 重启 HA、禁用自动化 | 工具返回确认提示，不直接执行 |

### 6.2 速率限制
```python
HA_RATE_LIMITS = {
    "control": "10/min/user",
    "query": "30/min/user",
    "diagnose": "5/min/user",
}
```

### 6.3 黑名单
`HA_BLOCKED_ENTITIES` 中的设备，控制操作直接拒绝并返回提示。

---

## 7. 错误处理

| 错误场景 | 返回文本 |
|---------|---------|
| HA 不可达（连接超时） | "Home Assistant 服务不可达，请检查网络连接" |
| Token 无效（401） | "HA 认证失败，请检查 Token 配置" |
| 设备不存在 | "未找到设备 {entity_id}，可用 ha_query(query_type='list') 查看设备列表" |
| 操作不支持 | "该设备不支持 {action} 操作" |
| 速率限制 | "操作过于频繁，请稍后再试" |
| 黑名单设备 | "该设备 ({entity_id}) 已被禁止通过聊天控制" |

**注意：** 所有错误由 subagent 内部处理并返回文本结果，不会抛异常到主 agent。如果 subagent 整体超时（60秒），由 `base.py` 的 `run_subagent()` 统一返回超时提示。

---

## 8. 验收标准

### 8.1 SubAgent 集成
- [ ] `ha_subagent` 在有 HA 配置时正确注册到 subagent 列表
- [ ] 无 HA 配置时不注册，不影响其他 subagent
- [ ] 主 agent 能根据用户意图正确委派 HA 任务给 ha_subagent
- [ ] subagent 内部 LLM 能自主选择 ha_query/ha_control/ha_diagnose

### 8.2 设备控制
- [ ] 支持灯光、开关、空调、风扇、窗帘、门锁、媒体播放器控制
- [ ] 支持场景和脚本触发
- [ ] 敏感操作（门锁等）有确认保护
- [ ] 操作结果返回人类可读的确认信息

### 8.3 状态查询
- [ ] 单设备状态查询含完整属性
- [ ] 设备列表按域分组，显示数量和状态摘要
- [ ] 历史查询返回可读的状态变化时间线

### 8.4 诊断修复
- [ ] 系统健康检查包含版本、组件数、运行状态
- [ ] 能识别 unavailable/unknown 设备并给出可能原因
- [ ] 错误日志截断返回（不超过 2000 字符）
- [ ] 修复建议明确且可操作

### 8.5 安全性
- [ ] 速率限制正常工作
- [ ] 黑名单设备无法控制
- [ ] 危险操作不直接执行

### 8.6 测试
- [ ] HAClient 有 mock 测试覆盖
- [ ] 3 个工具函数各有单元测试
- [ ] ha_subagent 集成测试（mock HA API）
- [ ] 错误场景有测试覆盖

---

## 9. 实现步骤

| 步骤 | 内容 | 涉及文件 |
|------|------|---------|
| 1 | settings.py 添加 HA 配置项 | `core/settings.py` |
| 2 | 实现 HAClient HTTP 封装 | `apps/graph/tools/ha_client.py`（新建） |
| 3 | 实现 3 个 HA 工具函数 | `apps/graph/tools/homeassistant.py`（新建） |
| 4 | 创建 ha_subagent | `apps/graph/subagents/ha_agent.py`（新建） |
| 5 | 注册 ha_subagent | `apps/graph/subagents/__init__.py`（修改） |
| 6 | 编写测试 | `tests/apps/graph/test_ha_*.py`（新建） |

**总计新增 3 个文件，修改 2 个文件。** 符合 SC-004（新增 subagent 修改不超过 2 个已有文件）。

---

## 10. 依赖

- Home Assistant 实例可访问（内网或 frp 穿透）
- Long-Lived Access Token
- httpx（已在项目依赖中）
- Redis（限流，已有基础设施）

---

*文档版本：v2.0*
*更新说明：从平铺工具模式改为 SubAgent 架构，与 006-subagent-tools 保持一致*
