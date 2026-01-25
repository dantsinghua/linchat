'use client';

/**
 * 聊天页面
 *
 * 将在 T047 完善实现
 */
export default function ChatPage() {
  return (
    <div className="flex h-screen flex-col">
      {/* 顶部导航 */}
      <header className="border-b bg-white px-6 py-4">
        <h1 className="text-xl font-semibold text-gray-800">LinChat</h1>
      </header>

      {/* 聊天区域 */}
      <main className="flex flex-1 flex-col overflow-hidden">
        <div className="flex-1 overflow-y-auto p-6">
          <p className="text-center text-gray-500">聊天功能将在 Phase 4 实现</p>
        </div>

        {/* 输入区域 */}
        <div className="border-t bg-white p-4">
          <div className="mx-auto flex max-w-3xl items-center gap-4">
            <input
              type="text"
              placeholder="输入消息..."
              className="flex-1 rounded-lg border border-gray-300 px-4 py-2 focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20"
              disabled
            />
            <button
              className="rounded-lg bg-primary-500 px-6 py-2 text-white hover:bg-primary-600 disabled:opacity-50"
              disabled
            >
              发送
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
