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
    # 多轮交互对话（推荐！AI 会记住上下文）
    uv run python -m tradenest.cli chat
    uv run python -m tradenest.cli chat --stream   # 流式输出版

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
# 子命令：chat（多轮交互模式）
# ============================================================

async def cmd_chat(args: argparse.Namespace) -> int:
    """多轮交互对话，在内存里保持对话历史。"""
    setup_logging()

    # 解析 Provider
    provider = None
    if args.provider:
        try:
            provider = get_registry().get(args.provider)
        except KeyError as e:
            console.print(f"[red]✗ {e}[/red]")
            return 2

    try:
        task = TaskType(args.task)
    except ValueError:
        console.print(f"[red]✗ 未知任务类型: {args.task}[/red]")
        return 2

    console.print(Panel(
        f"[bold cyan]🪺 TradeNest[/bold cyan] v{__version__} — 多轮对话模式\n"
        f"[dim]输入问题后回车 | 输入 [bold]exit[/bold] 或 [bold]quit[/bold] 退出 | Ctrl+C 强制退出[/dim]\n"
        f"[dim]输入 [bold]clear[/bold] 清空对话历史 | 输入 [bold]history[/bold] 查看历史[/dim]",
        border_style="cyan",
    ))

    # 对话历史（内存）：[{"role": "user"|"assistant", "content": str}]
    history: list[dict] = []

    while True:
        # 读取输入
        try:
            console.print("[bold cyan]You >[/bold cyan] ", end="")
            user_input = input().strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]退出[/yellow]")
            break

        if not user_input:
            continue

        # 内置指令
        if user_input.lower() in ("exit", "quit", "bye", "q"):
            console.print("[yellow]再见！[/yellow]")
            break

        if user_input.lower() == "clear":
            history.clear()
            console.print("[dim]✓ 对话历史已清空[/dim]")
            continue

        if user_input.lower() == "history":
            if not history:
                console.print("[dim]（暂无历史）[/dim]")
            else:
                for i, msg in enumerate(history):
                    role = "You" if msg["role"] == "user" else "AI"
                    preview = msg["content"][:100].replace("\n", " ")
                    console.print(f"[dim]{i+1}. [{role}] {preview}...[/dim]")
            continue

        # 追加用户消息到历史
        history.append({"role": "user", "content": user_input})

        # 调用 Agent（带上完整历史）
        if args.stream:
            assistant_text = await _chat_stream(user_input, history[:-1], task, provider, args.model)
        else:
            assistant_text = await _chat_blocking(user_input, history[:-1], task, provider, args.model)

        if assistant_text:
            history.append({"role": "assistant", "content": assistant_text})

    return 0


async def _chat_blocking(
    message: str,
    history: list[dict],
    task: TaskType,
    provider,
    model: str | None,
) -> str:
    """多轮非流式：把历史拼进 system/user 消息传给 Agent。"""
    # 把历史格式化成上下文字符串追加到 message 前面
    # Agent loop 目前接收单条 message，用这个简单方案先跑通多轮
    full_message = _build_message_with_history(message, history)

    with console.status("[cyan]思考中...[/cyan]"):
        result = await run_agent(
            full_message,
            task=task,
            provider=provider,
            model=model,
        )

    if result.error:
        console.print(f"[red]✗ {result.error}[/red]")
        return ""

    safe_text, check = safe_output(result.final_text, strict=False)

    console.print()
    console.print(f"[bold green]AI >[/bold green]")
    console.print(Markdown(safe_text))
    console.print(f"[dim]({result.rounds}轮 {len(result.tool_calls)}次工具 {result.duration_ms:.0f}ms)[/dim]")
    console.print()

    return result.final_text


async def _chat_stream(
    message: str,
    history: list[dict],
    task: TaskType,
    provider,
    model: str | None,
) -> str:
    """多轮流式。"""
    full_message = _build_message_with_history(message, history)
    collected: list[str] = []

    console.print(f"[bold green]AI >[/bold green]")
    try:
        async for event in run_agent_stream(
            full_message,
            task=task,
            provider=provider,
            model=model,
        ):
            etype = event.get("type")
            if etype == "text_delta":
                delta = event.get("text", "")
                collected.append(delta)
                console.print(delta, end="", soft_wrap=True)
            elif etype == "tool_call_start":
                console.print(f"\n[dim]🔧 {event.get('name')}...[/dim]", end="")
            elif etype == "tool_result":
                preview = (event.get("content_preview") or "")[:80]
                console.print(f" ✓[/dim]")
            elif etype == "complete":
                console.print()
                t = event.get('duration_ms', 0)
                console.print(f"[dim]({event.get('rounds',1)}轮 {t:.0f}ms)[/dim]")
            elif etype == "error":
                console.print(f"\n[red]✗ {event.get('message')}[/red]")
                return ""
    except KeyboardInterrupt:
        console.print("\n[yellow]中断[/yellow]")

    console.print()
    return "".join(collected)


def _build_message_with_history(message: str, history: list[dict]) -> str:
    """
    把对话历史拼成上下文，追加到当前消息前。
    格式简洁，让 LLM 能理解上下文但不占太多 token。
    """
    if not history:
        return message

    # 只取最近 10 轮（避免太长）
    recent = history[-20:]  # 20条 = 10轮
    ctx_lines = ["【对话历史（最近）】"]
    for msg in recent:
        role = "用户" if msg["role"] == "user" else "AI"
        # 每条最多取 300 字符，避免 token 爆炸
        content = msg["content"][:300].replace("\n", " ")
        ctx_lines.append(f"{role}: {content}")
    ctx_lines.append("【当前问题】")
    ctx_lines.append(message)
    return "\n".join(ctx_lines)


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
    
    # chat（多轮交互，主推）
    chat = sub.add_parser("chat", help="多轮交互对话（推荐）")
    chat.add_argument("--task", default=TaskType.ANALYST.value, help="任务类型")
    chat.add_argument("--provider", default=None, help="指定 Provider ID")
    chat.add_argument("--model", default=None, help="指定模型")
    chat.add_argument("--stream", action="store_true", help="流式输出")

    # ask
    ask = sub.add_parser("ask", help="单次问答（无上下文）")
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
        "chat": cmd_chat,
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
