# 自定义 PostgreSQL 镜像：pgvector + pg_jieba
# 用途：支持向量检索和中文全文搜索
#
# 使用 pgvector 官方预构建镜像（基于 postgres:15-alpine，已包含 pgvector 扩展）
# 退化方案：若 pg_jieba 编译失败，退化为 PostgreSQL 默认 simple 配置
# （按空格分词，中文分词精度下降但功能不中断）

FROM pgvector/pgvector:pg15

# 尝试安装 pg_jieba 中文分词扩展（可选，失败不阻塞构建）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git cmake make g++ postgresql-server-dev-15 libpq-dev && \
    git clone https://github.com/jaiminpan/pg_jieba.git /tmp/pg_jieba && \
    cd /tmp/pg_jieba && \
    git submodule update --init --recursive && \
    mkdir build && cd build && \
    cmake .. && make && make install || \
    echo "WARNING: pg_jieba build failed, falling back to simple config" && \
    rm -rf /tmp/pg_jieba && \
    apt-get purge -y git cmake make g++ postgresql-server-dev-15 libpq-dev && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*
