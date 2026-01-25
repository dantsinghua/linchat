/**
 * 认证服务
 *
 * 参考:
 * - process-model.md#一、用户登录流程（P_AUTH_001）
 * - behavior-model.md#1.1 获取验证码（B_AUTH_001）
 * - behavior-model.md#1.2 用户登录（B_AUTH_002）
 *
 * 安全要求:
 * - Token 由后端通过 httpOnly Cookie 设置
 * - 前端使用 credentials: 'include' 自动携带 Cookie
 */
import { post, get } from './api';
import { CaptchaResponse, User } from '@/types';
import { sm4Encrypt } from '@/utils/crypto';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || '/api/v1';

/**
 * 登录响应（后端返回 snake_case，前端使用 camelCase）
 */
interface LoginResponse {
  userId: number;
  username: string;
  expireTime: string;
}

/**
 * SM4 加密密码
 *
 * 参考: behavior-model.md#1.2 用户登录 - SM4加密密码
 * 使用 sm-crypto 库实现国密 SM4 加密
 *
 * @param password 明文密码
 * @returns SM4 加密后的密码（Base64 编码）
 */
export function encryptPassword(password: string): string {
  return sm4Encrypt(password);
}

/**
 * 获取验证码
 *
 * 参考: behavior-model.md#1.1 获取验证码（B_AUTH_001）
 * 规则: R_CAPTCHA_001 - 验证码有效期2分钟
 */
export async function getCaptcha(): Promise<CaptchaResponse> {
  const response = await get<CaptchaResponse>('/auth/captcha');
  if (response.code !== 'SUCCESS') {
    throw new Error(response.message || '获取验证码失败');
  }
  return response.data;
}

/**
 * 用户登录
 *
 * 参考:
 * - process-model.md#一、用户登录流程（P_AUTH_001）
 * - behavior-model.md#1.2 用户登录（B_AUTH_002）
 *
 * @param username 用户名
 * @param password 明文密码（会在前端 SM4 加密）
 * @param captchaId 验证码ID
 * @param captchaCode 用户输入的验证码
 */
export async function login(
  username: string,
  password: string,
  captchaId: string,
  captchaCode: string
): Promise<LoginResponse> {
  // SM4 加密密码
  const encryptedPassword = encryptPassword(password);

  try {
    const response = await post<LoginResponse>('/auth/login', {
      username,
      password: encryptedPassword,
      captcha_id: captchaId,
      captcha_code: captchaCode,
    });

    if (response.code !== 'SUCCESS') {
      throw new Error(response.message || '登录失败');
    }

    return response.data;
  } catch (error) {
    // 处理 axios 错误，提取后端返回的错误消息
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const axiosError = error as any;
    if (axiosError.response?.data?.message) {
      throw new Error(axiosError.response.data.message);
    }
    // 如果已经是 Error 对象且有自定义消息，直接抛出
    if (error instanceof Error && error.message && !error.message.includes('status code')) {
      throw error;
    }
    throw new Error('登录失败，请重试');
  }
}

/**
 * 用户登出
 */
export async function logout(): Promise<void> {
  try {
    await post('/auth/logout');
  } catch {
    // 忽略错误，即使后端失败也要清理前端状态
  }
}

/**
 * 获取当前用户信息
 */
export async function getCurrentUser(): Promise<User | null> {
  try {
    const response = await get<User>('/auth/me');
    if (response.code === 'SUCCESS') {
      return response.data;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * 检查是否已登录
 */
export async function checkAuthStatus(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE_URL}/auth/me`, {
      credentials: 'include',
    });
    return response.ok;
  } catch {
    return false;
  }
}
