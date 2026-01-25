'use client';

import Link from 'next/link';

/**
 * 401 未授权页面
 *
 * 蓝白风格设计
 */
export default function UnauthorizedPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-primary-50 to-white">
      <div className="text-center">
        <h1 className="mb-4 text-9xl font-bold text-primary-500">401</h1>
        <h2 className="mb-4 text-2xl font-semibold text-gray-800">未授权访问</h2>
        <p className="mb-8 text-gray-600">您需要登录才能访问此页面</p>
        <Link
          href="/login"
          className="inline-block rounded-lg bg-primary-500 px-6 py-3 text-white transition-colors hover:bg-primary-600"
        >
          前往登录
        </Link>
      </div>
    </div>
  );
}
