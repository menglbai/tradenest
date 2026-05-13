-- TradeNest 数据库初始化
-- 启用 pgvector 扩展（向量存储）
CREATE EXTENSION IF NOT EXISTS vector;

-- 启用 pg_trgm（中文模糊搜索）
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 时区
SET TIME ZONE 'Asia/Shanghai';
