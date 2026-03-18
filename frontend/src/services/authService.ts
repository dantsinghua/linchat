/**
 * 认证服务
 *
 * 安全要求:
 * - Token 由后端通过 httpOnly Cookie 设置
 * - 前端使用 credentials: 'include' 自动携带 Cookie
 */
import { post, get } from './api';
import { CaptchaResponse, User } from '@/types';
import { sm4Encrypt } from '@/utils/crypto';

interface LoginResponse {
  user_id: number;
  username: string;
  expire_time: string;
}

/**
 * 获取验证码
 */
export async function getCaptcha(): Promise<CaptchaResponse> {
  const response = await get<CaptchaResponse>('/auth/captcha');
  if (response.code !== 'SUCCESS') {
    throw new Error(response.message || '获取验证码失败');
  }
  return response.data;
}

/**
 * 用户登录（密码自动 SM4 加密）
 */
export async function login(
  username: string,
  password: string,
  captchaId: string,
  captchaCode: string
): Promise<LoginResponse> {
  try {
    const response = await post<LoginResponse>('/auth/login', {
      username,
      password: sm4Encrypt(password),
      captcha_id: captchaId,
      captcha_code: captchaCode,
    });
    if (response.code !== 'SUCCESS') {
      throw new Error(response.message || '登录失败');
    }
    return response.data;
  } catch (error) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const responseData = (error as any).response?.data;
    // 015-family-multiuser: 账号过期特殊提示
    if (responseData?.code === 'ACCOUNT_EXPIRED') {
      throw new Error('账号已过期，请联系家庭成员');
    }
    const msg = responseData?.message;
    if (msg) throw new Error(msg);
    if (error instanceof Error && !error.message.includes('status code')) throw error;
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
    return response.code === 'SUCCESS' ? response.data : null;
  } catch {
    return null;
  }
}
