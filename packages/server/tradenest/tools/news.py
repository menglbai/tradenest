"""
================================================================================
文件：tradenest/tools/news.py
作用：新闻 / 公告 / 研报工具
================================================================================

【核心业务功能】
拉取与某只股票相关的：
1. 最近公告（巨潮 / 东财）
2. 最近新闻（财经媒体）

让 Agent 在做"新闻分析师"角色时有数据。

【数据来源】
- AkShare 的 stock_zhibo_ths（同花顺直播流）
- AkShare 的 stock_news_em（东财个股新闻）

【网络容错】
和 market.py 一样，失败时返回 is_error=True。

================================================================================
"""

from __future__ import annotations

from tradenest.core.logging import get_logger
from tradenest.tools.base import ToolResult, register_tool

log = get_logger("tradenest.tools.news")


@register_tool(
    name="get_recent_news",
    description=(
        "获取与指定股票相关的最近新闻。"
        "用户问'XX 最近有什么新闻 / 最近发生了什么'时调用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A 股 6 位代码"},
            "limit": {
                "type": "integer",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
                "description": "返回最多 N 条",
            },
        },
        "required": ["code"],
    },
)
async def get_recent_news(code: str, limit: int = 10) -> ToolResult:
    """获取个股最近新闻（东财数据源）。"""
    try:
        import akshare as ak
    except ImportError:
        return ToolResult(content="AkShare 未安装", is_error=True)
    
    try:
        df = ak.stock_news_em(symbol=code)
        if df is None or len(df) == 0:
            return ToolResult(content=f"暂无 {code} 的相关新闻", metadata={"count": 0})
        
        df_recent = df.head(limit)
        lines = [f"【{code}】最近 {len(df_recent)} 条新闻:\n"]
        for i, r in df_recent.iterrows():
            lines.append(
                f"[{i+1}] {r.get('发布时间', '')} | {r.get('新闻标题', '')}\n"
                f"    来源: {r.get('文章来源', 'N/A')}\n"
                f"    摘要: {(r.get('新闻内容', '') or '')[:150]}\n"
            )
        return ToolResult(content="\n".join(lines), metadata={"count": len(df_recent)})
    
    except Exception as e:
        log.error("recent_news_failed", code=code, error=str(e))
        return ToolResult(content=f"获取 {code} 新闻失败：{e}", is_error=True)


@register_tool(
    name="get_announcements",
    description=(
        "获取 A 股个股最近的官方公告。"
        "用户问'XX 最近的公告 / 财报 / 重大事件'时调用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A 股 6 位代码"},
            "limit": {"type": "integer", "default": 20, "description": "返回最多 N 条"},
        },
        "required": ["code"],
    },
)
async def get_announcements(code: str, limit: int = 20) -> ToolResult:
    """获取个股公告（暂用 stock_news_em 兼用，未来对接巨潮）。"""
    try:
        import akshare as ak
    except ImportError:
        return ToolResult(content="AkShare 未安装", is_error=True)
    
    try:
        # 公告数据用 stock_notice_report (东财公司公告)
        # 不同 AkShare 版本接口名可能不同，try 一下
        try:
            df = ak.stock_notice_report(symbol="全部", date="")
            df = df[df['代码'] == code].head(limit) if '代码' in df.columns else df.head(0)
        except Exception:
            # 兜底用 news
            df = ak.stock_news_em(symbol=code)
            df = df.head(limit)
        
        if df is None or len(df) == 0:
            return ToolResult(content=f"暂无 {code} 的公告", metadata={"count": 0})
        
        lines = [f"【{code}】最近公告:\n"]
        for i, r in df.iterrows():
            title = r.get('标题') or r.get('新闻标题') or 'N/A'
            date = r.get('公告日期') or r.get('发布时间') or 'N/A'
            lines.append(f"[{i+1}] {date} | {title}")
        
        return ToolResult(content="\n".join(lines), metadata={"count": len(df)})
    
    except Exception as e:
        log.error("announcements_failed", code=code, error=str(e))
        return ToolResult(content=f"获取 {code} 公告失败：{e}", is_error=True)
