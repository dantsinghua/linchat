-- 为 linchat 数据库安装 pgvector 和 pg_jieba 扩展
\connect linchat;

-- pgvector 向量检索
CREATE EXTENSION IF NOT EXISTS vector;

-- pg_jieba 中文分词（若安装失败则跳过，退化为 simple 配置）
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_jieba;
    -- 创建 jieba 分词配置
    PERFORM 1 FROM pg_ts_config WHERE cfgname = 'jiebacfg';
    IF NOT FOUND THEN
        CREATE TEXT SEARCH CONFIGURATION jiebacfg (PARSER = jieba);
        ALTER TEXT SEARCH CONFIGURATION jiebacfg
            ADD MAPPING FOR n, v, a, i, e, l WITH simple;
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'pg_jieba extension not available, using simple config as fallback: %', SQLERRM;
END $$;
