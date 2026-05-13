"""
================================================================================
文件：tradenest/cli.py
作用：命令行接口（不依赖 web 服务）
================================================================================

【核心业务功能】
让用户在终端里直接跟 TradeNest 对话，不用起 FastAPI 服务。

主要用途：
- Day 1 烟雾测试（验证 LLM + 工具 + Agent loop 都能跑）
- 离线开发 / 单元测试
- 给不熟悉 web 的用户一个简单入口

【用法】
    # 单条问话
    uv run python -m tradenest.cli ask "贵州茅台多少钱？"
    
    # 流式输出
    uv run python -m tradenest.cli ask "分析下宁德时代" --stream
    
    # 用其他 Provider
    uv run python -m tradenest.cli ask "..." --provider deepseek
    
    # 查看 system 信息
    uv run python -m tradenest.cli info

================================================================================
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from tradenest import __version__
from tradenest.agents.runtime import run_agent, run_agent_stream
from tradenest.compliance import safe_output
from tradenest.core.config import settings
from tradenest.core.logging import setup_logging
from tradenest.llm.registry import get_registry
from tradenest.llm.router import TaskType
from tradenest.tools import get_all_tools

# 触发工具注册
import tradenest.tools  # noqa: F401


console = Console()


# ============================================================
# 子命令：ask
# ============================================================

async def cmd_ask(args: argparse.Namespace) -> int:
    """单次问答"""
    setup_logging()
    
    console.print(Panel(
        f"[bold cyan]🪺 TradeNest[/bold cyan] v{__version__}\n"
        f"[dim]问: {args.message}[/dim]",
        border_style="cyan",
    ))
    
    # 解析任务类型
    try:
        task = TaskType(args.task)
    except ValueError:
        console.print(f"[red]✗ 未知任务类型: {args.task}[/red]")
        return 2
    
    # 解析 Provider
    provider = None
    if args.provider:
        try:
            provider = get_registry().get(args.provider)
        except KeyError as e:
            console.print(f"[red]✗ {e}[/red]")
            return 2
    
    if args.stream:
        return await _ask_stream(args.message, task, provider, args.model)
    else:
        return await _ask_blocking(args.message, task, provider, args.model)


async def _ask_blocking(
    message: str,
    task: TaskType,
    provider,
    model: str | None,
) -> int:
    """非流式 ask"""
    with console.status("[cyan]思考中...[/cyan]"):
        result = await run_agent(
            message,
            task=task,
            provider=provider,
            model=model,
        )
    
    if result.error:
        console.print(f"[red]✗ {result.error}[/red]")
        return 1
    
    safe_text, check = safe_output(result.final_text, strict=settings.compliance_strict)
    
    # 输出
    console.print()
    console.print(Markdown(safe_text))
    console.print()
    
    # 元信息
    console.print(Panel(
        f"轮数: {result.rounds}\n"
        f"工具调用: {len(result.tool_calls)} 次\n"
        f"耗时: {result.duration_ms:.0f} ms\n"
        f"输入 token: {result.total_input_tokens}\n"
        f"输出 token: {result.total_output_tokens}\n"
        f"合规通过: {'✓' if check.passed else '✗'}",
        title="[dim]运行信息[/dim]",
        border_style="dim",
    ))
    
    if check.violations:
        console.print(f"[yellow]⚠ 检测到合规警告 {len(check.violations)} 处[/yellow]")
    
    return 0


async def _ask_stream(
    message: str,
    task: TaskType,
    provider,
    model: str | None,
) -> int:
    """流式 ask"""
    full_text_parts: list[str] = []
    rounds = 0
    tool_calls_count = 0
    
    try:
        async for event in run_agent_stream(
            message,
            task=task,
            provider=provider,
            model=model,
        ):
            etype = event.get("type")
            
            if etype == "text_delta":
                delta = event.get("text", "")
                full_text_parts.append(delta)
                console.print(delta, end="", soft_wrap=True)
            
            elif etype == "tool_call_start":
                tool_calls_count += 1
                console.print(
                    f"\n[dim]🔧 调用工具 {event.get('name')}({event.get('input')})[/dim]"
                )
            
            elif etype == "tool_result":
                preview = event.get("content_preview", "")[:200]
                style = "red" if event.get("is_error") else "dim"
                console.print(f"[{style}]   ← {preview[:100]}...[/{style}]")
            
            elif etype == "round_end":
                rounds = event.get("round", 0)
                if event.get("stop_reason") == "tool_use":
                    console.print()
            
            elif etype == "complete":
                console.print()
                console.print(Panel(
                    f"轮数: {event.get('rounds', rounds)}\n"
                    f"工具调用: {len(event.get('tool_calls', []))} 次\n"
                    f"耗时: {event.get('duration_ms', 0):.0f} ms\n"
                    f"合规: {'✓' if event.get('compliance_passed') else '✗'}",
                    title="[dim]运行信息[/dim]",
                    border_style="dim",
                ))
            
            elif etype == "error":
                console.print(f"\n[red]✗ {event.get('message')}[/red]")
                return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]中断[/yellow]")
        return 130
    
    return 0


# ============================================================
# 子命令：info
# ============================================================

async def cmd_info(args: argparse.Namespace) -> int:
    """系统信息"""
    setup_logging()
    
    registry = get_registry()
    tools = get_all_tools()
    
    console.print(Panel(
        f"[bold cyan]🪺 TradeNest[/bold cyan] v{__version__}\n\n"
        f"默认 Provider: [yellow]{settings.default_provider_id}[/yellow]\n"
        f"已注册 Provider: [yellow]{', '.join(registry.list_providers())}[/yellow]\n"
        f"工具数量: [yellow]{len(tools)}[/yellow]\n"
        f"Agent 最大轮数: [yellow]{settings.agent_max_loop_turns}[/yellow]\n"
        f"严格合规: [yellow]{'✓' if settings.compliance_strict else '✗'}[/yellow]",
        title="系统信息",
        border_style="cyan",
    ))
    
    # 工具列表
    console.print("\n[bold]工具清单:[/bold]")
    for t in tools:
        console.print(f"  • [cyan]{t.definition.name}[/cyan]: {t.definition.description[:80]}")
    
    return 0


# ============================================================
# 子命令：health
# ============================================================

async def cmd_health(args: argparse.Namespace) -> int:
    """测试 Provider 健康"""
    setup_logging()
    
    registry = get_registry()
    pids = [args.provider] if args.provider else registry.list_providers()
    
    for pid in pids:
        try:
            provider = registry.get(pid)
        except KeyError:
            console.print(f"[red]✗ {pid}: 未注册[/red]")
            continue
        
        with console.status(f"[cyan]测试 {pid}...[/cyan]"):
            ok = await provider.health_check()
        
        if ok:
            console.print(f"[green]✓ {pid}: OK[/green]")
        else:
            console.print(f"[red]✗ {pid}: FAILED[/red]")
    
    return 0


# ============================================================
# CLI 主入口
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tradenest",
        description="TradeNest CLI - 你的私人 AI 投资研究伙伴",
    )
    parser.add_argument("--version", action="version", version=f"TradeNest {__version__}")
    
    sub = parser.add_subparsers(dest="cmd", required=True)
    
    # ask
    ask = sub.add_parser("ask", help="问 AI 一个问题")
    ask.add_argument("message", help="你的问题")
    ask.add_argument("--task", default=TaskType.ANALYST.value, help="任务类型")
    ask.add_argument("--provider", default=None, help="指定 Provider ID")
    ask.add_argument("--model", default=None, help="指定模型")
    ask.add_argument("--stream", action="store_true", help="流式输出")
    
    # info
    info = sub.add_parser("info", help="查看系统信息")
    
    # health
    health = sub.add_parser("health", help="测试 Provider 是否可用")
    health.add_argument("--provider", default=None, help="指定 Provider（不指定测全部）")
    
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    
    handlers = {
        "ask": cmd_ask,
        "info": cmd_info,
        "health": cmd_health,
    }
    
    handler = handlers.get(args.cmd)
    if handler is None:
        parser.print_help()
        sys.exit(2)
    
    try:
        exit_code = asyncio.run(handler(args))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        console.print("\n[yellow]中断[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]✗ 未预期错误: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
