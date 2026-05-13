"""
================================================================================
文件：tradenest/core/logging.py
作用：统一结构化日志配置
================================================================================

【核心业务功能】
为整个 TradeNest 后端配置 structlog（结构化日志）。

主要负责：
1. 配置 stdlib logging 基础（level、format）
2. 配置 structlog 的 processor chain（时间戳、log level、上下文绑定）
3. 提供模块级便捷函数 get_logger(name)

【为什么用 structlog 不是标准 logging】
- 结构化字段（key-value）让日志能被机器解析
- 比 print 强大：自动加时间戳 / level / 调用位置
- 比 logging 灵活：支持 binding 上下文（如 user_id / agent_run_id）

【使用示例】
    from tradenest.core.logging import get_logger
    
    log = get_logger("tradenest.agents.runtime")
    
    # 普通日志
    log.info("agent_started", query="分析茅台")
    
    # 绑定上下文
    log = log.bind(user_id="u1", agent_run_id="r1")
    log.info("tool_called", tool="get_realtime_quote")
    # 输出会自动带上 user_id 和 agent_run_id

================================================================================
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from tradenest.core.config import settings


def setup_logging() -> None:
    """初始化日志配置。在 FastAPI lifespan 启动时调用一次。"""
    # 1. 配置 stdlib logging（structlog 底层会用它）
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(message)s",
        stream=sys.stdout,
    )
    
    # 2. 配置 structlog
    structlog.configure(
        processors=[
            # 合并 contextvars 上下文（让 bind() 跨 await 也生效）
            structlog.contextvars.merge_contextvars,
            
            # 加 log level 字段
            structlog.stdlib.add_log_level,
            
            # 加 logger 名字
            structlog.stdlib.add_logger_name,
            
            # 加 ISO 时间戳
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            
            # 异常自动 format
            structlog.processors.format_exc_info,
            
            # 美化输出（开发期）/ JSON（生产）
            structlog.dev.ConsoleRenderer(colors=settings.debug)
            if settings.debug
            else structlog.processors.JSONRenderer(),
        ],
        # 让 structlog 走 stdlib 的 logger（兼容 uvicorn 等）
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "tradenest", **initial_context: Any) -> structlog.stdlib.BoundLogger:
    """获取一个绑定了上下文的 logger。
    
    Args:
        name: logger 名字（建议用模块路径，如 "tradenest.agents.runtime"）
        **initial_context: 初始绑定的字段，每条日志都会自动带上
    
    Returns:
        BoundLogger 实例
    
    Example:
        log = get_logger("tradenest.api.chat", request_id="abc")
        log.info("received")  # 自动带 request_id=abc
    """
    log = structlog.get_logger(name)
    if initial_context:
        log = log.bind(**initial_context)
    return log
