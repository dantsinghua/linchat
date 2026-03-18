/**
 * 家庭成员切换模态框
 *
 * 015-family-multiuser T025: 全屏模态框，展示成员列表，支持切换用户
 */
'use client';

import { memo, useMemo } from 'react';

import { useMemberStore, type Member } from '@/stores/memberStore';
import { getAvatarColor, getAvatarLetter } from './avatarUtils';

interface MemberSwitchModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSelect: (userId: number, username: string) => void;
  onCreateUser: () => void;
}

export const MemberSwitchModal = memo(function MemberSwitchModal({
  isOpen,
  onClose,
  onSelect,
  onCreateUser,
}: MemberSwitchModalProps) {
  const members = useMemberStore((s) => s.members);
  const authUserId = useMemberStore((s) => s.authUserId);

  // 分组排序：活跃成员在上，过期访客灰色在底部，status=0 不显示
  const { activeMembers, expiredMembers } = useMemo(() => {
    const active: Member[] = [];
    const expired: Member[] = [];

    for (const m of members) {
      // status=0 不显示
      if (m.status === 0) continue;

      if (m.is_expired) {
        expired.push(m);
      } else {
        active.push(m);
      }
    }

    return { activeMembers: active, expiredMembers: expired };
  }, [members]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl dark:bg-gray-800"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题栏 */}
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-800 dark:text-white">
            家庭成员
          </h2>
          <button
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700"
          >
            <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* 用户列表 */}
        <div className="max-h-80 space-y-2 overflow-y-auto">
          {/* 活跃成员 */}
          {activeMembers.map((member) => {
            const isCurrentUser = member.user_id === authUserId;
            const bgColor = getAvatarColor(member.user_id);
            const letter = getAvatarLetter(member.username);

            return (
              <button
                key={member.user_id}
                onClick={() => onSelect(member.user_id, member.username)}
                className={`flex w-full items-center gap-3 rounded-xl px-4 py-3 transition-colors ${
                  isCurrentUser
                    ? 'bg-primary-50 ring-2 ring-primary-500 dark:bg-primary-900/20'
                    : 'hover:bg-gray-50 dark:hover:bg-gray-700'
                }`}
              >
                {/* 头像 */}
                <div
                  className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full text-sm font-bold text-white"
                  style={{ backgroundColor: bgColor }}
                >
                  {letter}
                </div>

                {/* 用户信息 */}
                <div className="flex flex-1 items-center gap-2">
                  <span className="font-medium text-gray-800 dark:text-white">
                    {member.username}
                  </span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs ${
                      member.member_type === 'member'
                        ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
                        : 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
                    }`}
                  >
                    {member.member_type === 'member' ? '成员' : '访客'}
                  </span>
                </div>

                {/* 当前用户标记 */}
                {isCurrentUser && (
                  <span className="text-xs text-primary-500">当前</span>
                )}
              </button>
            );
          })}

          {/* 过期访客（灰色展示，不可点击） */}
          {expiredMembers.map((member) => {
            const letter = getAvatarLetter(member.username);

            return (
              <div
                key={member.user_id}
                className="flex w-full items-center gap-3 rounded-xl px-4 py-3 opacity-50"
              >
                {/* 头像（灰色） */}
                <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full bg-gray-300 text-sm font-bold text-white dark:bg-gray-600">
                  {letter}
                </div>

                {/* 用户信息 */}
                <div className="flex flex-1 items-center gap-2">
                  <span className="font-medium text-gray-400 dark:text-gray-500">
                    {member.username}
                  </span>
                  <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-400 dark:bg-gray-700 dark:text-gray-500">
                    访客(已过期)
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        {/* 添加用户按钮 */}
        <button
          onClick={onCreateUser}
          className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl border-2 border-dashed border-gray-300 px-4 py-3 text-sm text-gray-500 transition-colors hover:border-primary-400 hover:text-primary-500 dark:border-gray-600 dark:text-gray-400 dark:hover:border-primary-500 dark:hover:text-primary-400"
        >
          <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
          </svg>
          添加用户
        </button>
      </div>
    </div>
  );
});
