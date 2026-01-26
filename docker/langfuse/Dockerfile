# Langfuse Web 自定义构建
# 支持 /langfuse 基础路径
# 参考: https://langfuse.com/self-hosting/configuration/custom-base-path

ARG NEXT_PUBLIC_BASE_PATH=/langfuse

# ============ 构建阶段 ============
FROM node:20-alpine AS builder

ARG NEXT_PUBLIC_BASE_PATH

WORKDIR /app

# 安装构建依赖
RUN apk add --no-cache git python3 make g++

# 克隆 Langfuse 源码 (使用 production 分支)
RUN git clone --depth 1 --branch production https://github.com/langfuse/langfuse.git .

# 启用 corepack 和 pnpm
RUN corepack enable && corepack prepare pnpm@latest --activate

# 安装依赖
RUN pnpm install --frozen-lockfile

# 设置基础路径环境变量并构建
ENV NEXT_PUBLIC_BASE_PATH=${NEXT_PUBLIC_BASE_PATH}
ENV NEXT_TELEMETRY_DISABLED=1

# 构建时需要的环境变量（实际值在运行时会被覆盖）
ENV DATABASE_URL="postgresql://dummy:dummy@localhost:5432/langfuse"
ENV NEXTAUTH_SECRET="build-time-secret-will-be-replaced"
ENV NEXTAUTH_URL="http://localhost:3000"
ENV SALT="build-time-salt-will-be-replaced-32chars"
ENV CLICKHOUSE_URL="http://localhost:8123"
ENV CLICKHOUSE_USER="default"
ENV CLICKHOUSE_PASSWORD="dummy"

# 先构建依赖包，再构建 web 应用
RUN pnpm --filter=@langfuse/shared build && pnpm --filter=web build

# ============ 生产阶段 ============
FROM node:20-alpine AS runner

WORKDIR /app

# 创建非 root 用户
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# 复制构建产物
COPY --from=builder /app/web/.next/standalone ./
COPY --from=builder /app/web/.next/static ./web/.next/static
COPY --from=builder /app/web/public ./web/public

# 设置权限
RUN chown -R nextjs:nodejs /app

USER nextjs

# 环境变量
ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=3000

EXPOSE 3000

CMD ["node", "web/server.js"]
