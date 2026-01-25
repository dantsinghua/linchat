/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',

  // 子路径配置 (nginx 代理 /linchat)
  basePath: '/linchat',
  assetPrefix: '/linchat',

  // 优化配置
  experimental: {
    optimizePackageImports: ['react-markdown', 'mermaid'],
  },

  // 环境变量前缀
  env: {
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  },

  // 图片优化配置
  images: {
    remotePatterns: [],
    unoptimized: false,
  },

  // Webpack 配置
  webpack: (config, { isServer }) => {
    // 避免在服务端打包 mermaid
    if (isServer) {
      config.externals.push('mermaid');
    }
    return config;
  },
};

module.exports = nextConfig;
