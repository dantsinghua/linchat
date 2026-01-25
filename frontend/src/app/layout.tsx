import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'LinChat - 大模型聊天平台',
  description: '企业级多租户 AI 聊天应用',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-gray-50 antialiased">{children}</body>
    </html>
  );
}
