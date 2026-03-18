/**
 * 模型配置 API 服务
 *
 * 参考: specs/003-model-config/contracts/api.yaml
 *
 * 后端 DRF 返回 snake_case，前端 TypeScript 使用 camelCase，
 * 本模块统一在 API 边界执行格式转换。
 */
import { get, put } from '@/services/api';
import { ModelConfig, ModelUpdateRequest } from '@/types/model';

// ========== snake_case / camelCase 转换工具 ==========

/** snake_case → camelCase */
function snakeToCamel(str: string): string {
  return str.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
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

/**
 * 获取所有模型配置
 */
export async function fetchModels(): Promise<ModelConfig[]> {
  const response = await get<ModelConfig[]>('/models/');
  return toCamelCase<ModelConfig[]>(response.data);
}

/**
 * 获取单个模型配置
 */
export async function fetchModelById(id: number): Promise<ModelConfig> {
  const response = await get<ModelConfig>(`/models/${id}/`);
  return toCamelCase<ModelConfig>(response.data);
}

/**
 * 更新模型配置
 */
export async function updateModel(
  id: number,
  data: ModelUpdateRequest
): Promise<ModelConfig> {
  const response = await put<ModelConfig>(`/models/${id}/`, data);
  return toCamelCase<ModelConfig>(response.data);
}
