# app/settings 模块指南

## 模块概述

模型配置管理页面，仅管理员可访问。展示所有模型配置，支持在线编辑。

## 文件清单

| 文件 | 用途 |
|------|------|
| `page.tsx` | 设置页面主组件（SettingsPage） |

## 页面结构

```
SettingsPage (flex h-screen)
├── Header (顶部导航栏)
│   ├── 返回聊天按钮 (← 返回聊天)
│   ├── 页面标题 "模型配置"
│   └── 用户名显示
└── Main (内容区域)
    ├── 错误提示 (error)
    ├── 加载状态 (isLoading)
    ├── 模型配置卡片网格 (2 列布局)
    │   ├── ModelConfigCard (只读模式)
    │   └── ModelConfigForm (编辑模式, 替换对应卡片位置)
    ├── VoiceSettingsCard (语音设置)
    ├── SpeakerProfileCard (声纹管理)
    ├── DeviceManageCard (设备管理)
    └── 空状态 ("暂无模型配置")
```

## 关键逻辑

### 权限守卫

- 检测 `isAuthenticated` 和 `user.type`
- 未认证或非管理员: `router.push('/401')`
- API 返回 403: 跳转 `/401`
- 认证状态加载中: 显示 "加载中..." 占位

### 数据加载

- 权限检查通过后 (`authChecked = true`) 才加载模型列表
- 调用 `fetchModels()` -> `setModels()` 存入 `modelStore`

### 编辑流程

1. 点击卡片"编辑"按钮 -> `setEditingModel(model)`
2. 卡片位置替换为 `ModelConfigForm`
3. 保存成功: `updateModelInList()` 更新 store -> `setEditingModel(null)` 退出编辑
4. 取消: `setEditingModel(null)` 退出编辑

### 模型排序

按类型排序: tool(0) -> multimodal(1) -> embedding(2)

### 语音设置

- 三张语音设置卡片位于模型配置下方
- 所有用户可见（不限管理员）
- 数据来源 voiceApi（独立于 modelStore）

## 状态管理

- `modelStore`: 模型列表（models、isLoading、error）
- `useState`: editingModel（当前编辑的模型，null 表示非编辑状态）、authChecked（权限检查完成标志）

## 数据流

```
SettingsPage
  ├── useAuth → 权限检查
  ├── modelService.fetchModels() → 后端 GET /models/
  ├── modelStore (models, isLoading, error)
  └── UI 组件
       ├── ModelConfigCard (只读, onEdit 回调)
       └── ModelConfigForm (编辑, onSave/onCancel 回调)
            └── modelService.updateModel() → 后端 PUT /models/:id/
```

## 依赖关系

- `useAuth` Hook（认证和权限检查）
- `modelStore`（模型列表状态）
- `modelService`（fetchModels API）
- 组件: `ModelConfigCard`、`ModelConfigForm`
- 组件: `VoiceSettingsCard`、`SpeakerProfileCard`、`DeviceManageCard`

## 测试方法

- 权限测试: 验证非管理员跳转 401、未认证跳转 401
- 数据加载测试: 验证 fetchModels 调用时机、错误处理
- 编辑流程测试: 验证编辑/保存/取消状态切换
- 排序测试: 验证 tool -> multimodal -> embedding 排序
