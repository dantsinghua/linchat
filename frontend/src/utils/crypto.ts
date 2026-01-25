/**
 * 国密算法封装
 *
 * 参考:
 * - constitution.md#4.2 数据保护
 * - behavior-model.md#1.2 用户登录 - SM4加密密码
 *
 * 注意: SM4 密钥必须与后端保持一致
 */
import { sm4 } from 'sm-crypto';

// SM4 密钥（必须与后端 SM4_SECRET_KEY 保持一致）
// 开发环境使用默认密钥，生产环境应通过环境变量配置
const SM4_KEY = process.env.NEXT_PUBLIC_SM4_KEY || 'default-sm4-key-16';

/**
 * 确保密钥为16字节（与后端 crypto.py 保持一致）
 *
 * 后端实现：
 *   key = key.encode("utf-8")
 *   if len(key) < 16: key = key.ljust(16, b"\0")
 *   elif len(key) > 16: key = key[:16]
 *
 * 前端需使用相同的填充方式：
 *   - 先将字符串转为 UTF-8 字节
 *   - 不足16字节时用 0x00 填充
 *   - 超过16字节时截断
 */
function normalizeKeyToHex(key: string): string {
  // 将字符串转为 UTF-8 字节
  const keyBytes = Buffer.from(key, 'utf-8');

  // 创建16字节的 buffer，默认填充 0x00
  const normalizedKey = Buffer.alloc(16, 0);

  // 复制密钥字节（最多16字节）
  const copyLength = Math.min(keyBytes.length, 16);
  keyBytes.copy(normalizedKey, 0, 0, copyLength);

  // 返回十六进制字符串
  return normalizedKey.toString('hex');
}

/**
 * SM4 加密
 *
 * 用于登录时加密密码
 * 参考: rule-model.md#R_TOKEN_001 Token生成规则
 *
 * @param plaintext 明文字符串
 * @returns Base64 编码的密文
 */
export function sm4Encrypt(plaintext: string): string {
  // 获取标准化的16字节密钥（十六进制格式）
  const keyHex = normalizeKeyToHex(SM4_KEY);

  // ECB 模式加密，输出为十六进制
  const ciphertextHex = sm4.encrypt(plaintext, keyHex, {
    mode: 'ecb',
    padding: 'pkcs#7',
  });

  // 转换为 Base64（与后端保持一致）
  const ciphertextBuffer = Buffer.from(ciphertextHex, 'hex');
  return ciphertextBuffer.toString('base64');
}

/**
 * SM4 解密
 *
 * @param ciphertext Base64 编码的密文
 * @returns 解密后的明文字符串
 */
export function sm4Decrypt(ciphertext: string): string {
  // 获取标准化的16字节密钥（十六进制格式）
  const keyHex = normalizeKeyToHex(SM4_KEY);

  // Base64 解码为十六进制
  const ciphertextBuffer = Buffer.from(ciphertext, 'base64');
  const ciphertextHex = ciphertextBuffer.toString('hex');

  // ECB 模式解密
  const plaintext = sm4.decrypt(ciphertextHex, keyHex, {
    mode: 'ecb',
    padding: 'pkcs#7',
  });

  return plaintext;
}
