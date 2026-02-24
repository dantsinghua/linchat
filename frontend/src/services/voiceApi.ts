/**
 * 语音 REST API 服务
 *
 * 封装声纹 CRUD、设备 CRUD、语音设置的 HTTP 请求
 * 参考: specs/009-voice-interaction/data-model.md API 端点
 *
 * 后端 DRF 返回 snake_case，前端 TypeScript 使用 camelCase，
 * 本模块统一在 API 边界执行格式转换。
 */

import apiClient from './api';
import { get, post, put, del } from './api';
import type {
  SpeakerProfile,
  RegisteredDevice,
  DeviceRegisterRequest,
  DeviceRegisterResponse,
  VoiceSettings,
  VoiceSettingsUpdateRequest,
} from '@/types/voice';
import type { ApiResponse } from '@/types';

// ========== snake_case / camelCase 转换工具 ==========

/** snake_case → camelCase */
function snakeToCamel(str: string): string {
  return str.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}

/** camelCase → snake_case */
function camelToSnake(str: string): string {
  return str.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
}

/** 递归转换对象键名: snake_case → camelCase */
function toCamelCase<T>(obj: unknown): T {
  if (Array.isArray(obj)) {
    return obj.map((item) => toCamelCase(item)) as unknown as T;
  }
  if (obj !== null && typeof obj === 'object' && !(obj instanceof Date)) {
    const result: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      result[snakeToCamel(key)] = toCamelCase(value);
    }
    return result as T;
  }
  return obj as T;
}

/** 递归转换对象键名: camelCase → snake_case */
function toSnakeCase(obj: unknown): unknown {
  if (Array.isArray(obj)) {
    return obj.map((item) => toSnakeCase(item));
  }
  if (obj !== null && typeof obj === 'object' && !(obj instanceof Date)) {
    const result: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      result[camelToSnake(key)] = toSnakeCase(value);
    }
    return result;
  }
  return obj;
}

/** 包装 API 响应，将 data 字段从 snake_case 转为 camelCase */
function convertResponse<T>(response: ApiResponse<unknown>): ApiResponse<T> {
  return {
    ...response,
    data: toCamelCase<T>(response.data),
  };
}

// ========== 声纹管理 ==========

/** 获取当前用户的声纹档案 */
export async function getSpeakerProfile() {
  const response = await get<unknown>('/voice/speakers/');
  return convertResponse<SpeakerProfile>(response);
}

/** 注册声纹（multipart/form-data 上传 WAV 音频文件） */
export async function enrollSpeaker(name: string, audioBlob: Blob): Promise<ApiResponse<SpeakerProfile>> {
  const formData = new FormData();
  formData.append('name', name);
  formData.append('audio', audioBlob, 'recording.wav');
  const response = await apiClient.post<ApiResponse<unknown>>('/voice/speakers/', formData);
  return convertResponse<SpeakerProfile>(response.data);
}

/** 删除当前用户的声纹 */
export async function deleteSpeaker() {
  return del<null>('/voice/speakers/delete/');
}

// ========== 设备管理 ==========

/** 获取设备列表 */
export async function getDevices() {
  const response = await get<unknown>('/voice/devices/');
  return convertResponse<RegisteredDevice[]>(response);
}

/** 注册设备 */
export async function registerDevice(data: DeviceRegisterRequest) {
  const response = await post<unknown>('/voice/devices/', toSnakeCase(data) as object);
  return convertResponse<DeviceRegisterResponse>(response);
}

/** 更新设备 */
export async function updateDevice(deviceUuid: string, data: Partial<DeviceRegisterRequest>) {
  const response = await put<unknown>(`/voice/devices/${deviceUuid}/`, toSnakeCase(data) as object);
  return convertResponse<RegisteredDevice>(response);
}

/** 删除设备 */
export async function deleteDevice(deviceUuid: string) {
  return del<null>(`/voice/devices/${deviceUuid}/`);
}

// ========== 语音设置 ==========

/** 获取语音设置 */
export async function getVoiceSettings() {
  const response = await get<unknown>('/voice/settings/');
  return convertResponse<VoiceSettings>(response);
}

/** 更新语音设置 */
export async function updateVoiceSettings(data: VoiceSettingsUpdateRequest) {
  const response = await put<unknown>('/voice/settings/', toSnakeCase(data) as object);
  return convertResponse<VoiceSettings>(response);
}
