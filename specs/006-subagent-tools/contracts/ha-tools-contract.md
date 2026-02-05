# API Contract: Home Assistant Tools

**Feature**: 006-subagent-tools
**Date**: 2026-02-05

---

## 说明

本特性不新增 REST API 端点。3 个工具通过 LangGraph Agent 内部调用，不暴露为 HTTP 接口。
以下定义工具的输入/输出契约，供 Agent 和测试使用。

---

## Tool: ha_query

### 输入

```python
ha_query(
    query_type: str,           # "state" | "list" | "history"
    entity_id: str | None,     # state/history 时必填
    domain: str | None,        # list 时可选过滤
    hours: int = 24,           # history 时间范围
    config: RunnableConfig,    # 隐式注入 user_id
) -> str
```

### 输出示例

**query_type="state"**:
```
# 设备状态: light.living_room
- 名称: 客厅主灯
- 状态: on
- 亮度: 178/255 (70%)
- 色温: 4000K
- 最后变更: 2026-02-04 10:30:15
```

**query_type="list"**:
```
# 设备列表 (domain: light)
1. light.living_room — 客厅主灯 — on (70%)
2. light.bedroom — 卧室灯 — off
3. light.kitchen — 厨房灯 — on (100%)
共 3 个灯光设备，2 个开启
```

**query_type="history"**:
```
# 历史记录: climate.living_room (最近24小时)
- 10:30 on (26°C, cool) → 12:00 off
- 14:00 on (25°C, auto) → 18:30 on (27°C, heat)
共 3 次状态变化
```

### 错误输出

| 场景 | 返回文本 |
|------|---------|
| entity_id 不存在 | "未找到设备 {entity_id}，可用 ha_query(query_type='list') 查看设备列表" |
| HA 不可达 | "Home Assistant 服务不可达，请检查网络连接" |
| Token 无效 | "HA 认证失败，请检查 Token 配置" |
| 速率超限 | "查询操作过于频繁，请稍后再试（限额 30次/分钟）" |

---

## Tool: ha_control

### 输入

```python
ha_control(
    entity_id: str,            # 设备实体ID
    action: str,               # ACTION_MAP 中的操作
    params: dict | None,       # 附加参数
    config: RunnableConfig,    # 隐式注入 user_id
) -> str
```

### 输出示例

**普通操作**:
```
✅ 已执行: 开启 客厅主灯 (light.living_room)
当前状态: on, 亮度 178/255 (70%)
```

**敏感操作（L3）**:
```
⚠️ 敏感操作确认
即将执行: 解锁 前门门锁 (lock.front_door)
这是一个涉及安全的操作，请确认是否继续。
回复"确认解锁"以执行，或"取消"以放弃。
```

### 错误输出

| 场景 | 返回文本 |
|------|---------|
| 黑名单设备 | "该设备 ({entity_id}) 已被管理员禁止通过聊天控制" |
| 不支持的 action | "不支持的操作类型: {action}" |
| 设备不响应 | "设备 {entity_id} 未响应操作，可使用 ha_diagnose 诊断问题" |
| 速率超限 | "控制操作过于频繁，请稍后再试（限额 10次/分钟）" |

---

## Tool: ha_diagnose

### 输入

```python
ha_diagnose(
    check_type: str,           # "system" | "device" | "unreachable" | "automations" | "errors"
    entity_id: str | None,     # device 时必填
    config: RunnableConfig,    # 隐式注入 user_id
) -> str
```

### 输出示例

**check_type="system"**:
```
# 🏠 Home Assistant 系统状态
- 版本: 2024.12.1
- 运行时间: 15天3小时
- 组件数: 42
- 自动化: 12个 (11个启用, 1个禁用)
- 状态: 正常 ✅
```

**check_type="device"**:
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

**check_type="unreachable"**:
```
# ⚠️ 不可达设备扫描
发现 2 个不可达设备:
1. climate.living_room — 客厅空调 — unavailable (1小时55分)
2. sensor.outdoor_temp — 室外温度 — unknown (3小时12分)

共扫描 25 个设备，23 个正常
```
