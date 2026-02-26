# components/settings 模块指南

## 模块概述

模型配置管理页面的 UI 组件，包含配置展示卡片和编辑表单。仅管理员可访问。

## 文件清单

| 文件 | 用途 |
|------|------|
| `ModelConfigCard.tsx` | 模型配置展示卡片（只读视图，显示基础配置、容量参数、生成参数） |
| `ModelConfigForm.tsx` | 模型配置编辑表单（前端校验、API 提交、必填/选填字段处理） |
| `VoiceSettingsCard.tsx` | 语音设置卡片（唤醒词、录音模式、VAD 灵敏度） |
| `SpeakerProfileCard.tsx` | 声纹档案管理（注册/查看/删除声纹） |
| `DeviceManageCard.tsx` | 设备管理（注册/查看/删除设备） |

## 关键组件说明

### ModelConfigCard

展示单个模型配置的卡片视图。

**内部组件:**
- `TypeBadge`: 模型类型标签（tool=工具模型/multimodal=多模态模型/embedding=向量模型）
- `ConfigRow`: 配置项展示行（label-value 对）

**展示内容:**
- 基础配置: 模型名称、API 地址、API Key（脱敏）
- 容量参数: 最大上下文窗口、最大输入/输出 Token、有效上下文窗口
- 生成参数: Temperature、Top P、Frequency Penalty、Presence Penalty、Embedding 维度（仅 embedding 类型）
- 状态: 激活/未激活状态指示
- 时间: 创建时间、更新时间

**Props:**
- `model: ModelConfig` -- 模型配置数据
- `onEdit?: (model) => void` -- 编辑按钮回调

### ModelConfigForm

模型配置编辑表单，表单数据全部用字符串管理，提交时转换为正确类型。

**内部组件:**
- `FormField`: 必填文本/数字/密码输入框
- `OptionalField`: 选填数值输入框（带清除按钮，空值提交时转 null）

**前端校验规则:**
- 必填: 模型名称（最长 100 字符）、API 地址（最长 500 字符）、API Key（最短 12 字符）
- 容量参数: 正整数（max_context_window、max_input_tokens、max_output_tokens）
- 选填范围: temperature 0~2、top_p 0~1、frequency_penalty -2~2、presence_penalty -2~2
- embedding_dimensions: 仅 embedding 类型，正整数

**提交流程:**
1. 前端 `validateForm()` 校验
2. 构造 `ModelUpdateRequest` payload（snake_case 字段名）
3. 调用 `updateModel()` API
4. 成功后触发 `onSave(updated)` 回调

**API Key 处理:** 表单初始值为脱敏值（含 `****`），用户可覆盖输入新值。

### VoiceSettingsCard (370 行)

语音设置管理卡片（唤醒词管理、录音模式选择、VAD 灵敏度滑块）。

- 唤醒词：最多 5 个，每个最长 20 字符，支持添加/删除
- 录音模式：hold（按住说话）/ toggle（点击切换），Radio 选择
- VAD 灵敏度：Range 滑块 0.0~1.0，0.1 步长
- 保存按钮：仅在有变化时启用

**依赖:** `voiceApi.getVoiceSettings()`, `voiceApi.updateVoiceSettings()`

### SpeakerProfileCard (555 行)

声纹档案管理卡片（注册新声纹、查看/删除已有声纹）。

- 注册流程：输入名称 -> 录音 10-30 秒 -> 上传 WAV -> 显示质量评分
- 质量评分进度条：80%+ 绿色 / 50-80% 黄色 / <50% 红色
- 使用 `usePCMAudioCapture` 采集 PCM 音频，构建 WAV 文件上传

**依赖:** `usePCMAudioCapture`, `voiceApi.{getSpeakerProfile, enrollSpeaker, deleteSpeaker}`

### DeviceManageCard (368 行)

设备管理卡片（注册新设备、查看列表、删除设备）。

- 注册成功后一次性展示 API Token（TokenDialog），关闭后不可再查看
- 设备列表显示名称、状态、最后活跃时间

**依赖:** `voiceApi.{getDevices, registerDevice, deleteDevice}`

## 状态管理

- 使用 `modelStore` 管理模型列表（通过 Settings 页面协调）
- 表单内部状态使用 `useState` 管理

## 数据流

```
SettingsPage
  ├── ModelConfigCard (只读展示)
  │     └── onEdit → setEditingModel
  └── ModelConfigForm (编辑模式)
        ├── modelService.updateModel() → 后端 PUT /models/:id/
        └── onSave → updateModelInList (modelStore)
```

## 依赖关系

- `ModelConfigCard` -> `@/types/model`（ModelConfig）
- `ModelConfigForm` -> `@/services/modelService`（updateModel）、`@/types/model`（ModelConfig、ModelUpdateRequest）
- `VoiceSettingsCard` -> `@/services/voiceApi`（getVoiceSettings、updateVoiceSettings）
- `SpeakerProfileCard` -> `@/hooks/usePCMAudioCapture`、`@/services/voiceApi`（getSpeakerProfile、enrollSpeaker、deleteSpeaker）
- `DeviceManageCard` -> `@/services/voiceApi`（getDevices、registerDevice、deleteDevice）

## 测试方法

- ModelConfigCard: 验证各类型标签渲染、配置项展示、选填参数"未设置"显示
- ModelConfigForm: 验证前端校验规则（必填、范围、类型）、空值提交转 null、API Key 脱敏处理、提交成功/失败流程
