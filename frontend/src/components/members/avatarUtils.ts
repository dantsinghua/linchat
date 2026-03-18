/**
 * 头像工具函数
 *
 * 015-family-multiuser: 成员头像颜色和首字母提取
 */

const AVATAR_COLORS = [
  '#3B82F6',
  '#10B981',
  '#F59E0B',
  '#EF4444',
  '#8B5CF6',
  '#EC4899',
  '#06B6D4',
  '#84CC16',
];

/**
 * 根据 user_id 获取头像背景色
 */
export function getAvatarColor(userId: number): string {
  return AVATAR_COLORS[userId % 8] ?? '#3B82F6';
}

/**
 * 获取用户名首字母（大写）
 */
export function getAvatarLetter(username: string): string {
  return username.charAt(0).toUpperCase();
}
