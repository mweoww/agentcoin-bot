#!/usr/bin/env python3
"""
AgentCoin 注册模块入口
支持单账号和批量注册（带 Rich Live 实时面板）
注册成功的账号写入 data/registered.txt

用法:
  python register.py                    # 单账号（从 .env 读取）
  python register.py --batch            # 批量注册所有账号
  python register.py --batch --start 0 --count 10  # 批量注册第0-9个
"""

import argparse
import sys
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from eth_account import Account

import config
import contracts
import wallet
import x_binding
import registry
from accounts import (
    AccountInfo,
    load_account_range,
    load_registered,
    save_registered_account,
    is_registered_by_token,
    get_stats,
)

console = Console()


# ═══════════════════════════════════════════
# 注册可视化面板
# ═══════════════════════════════════════════

LOG_COLORS = {
    "信息": "cyan",
    "成功": "green",
    "警告": "yellow",
    "错误": "red",
    "系统": "dim",
    "绑定": "magenta",
}


class RegisterDashboard:
    """批量注册实时可视化面板"""

    def __init__(self, total: int, todo: int, workers: int):
        self.total = total
        self.todo = todo
        self.workers = workers
        self.console = Console()
        self._lock = threading.Lock()

        self.success = 0
        self.failed = 0
        self.current = 0
        self.start_time = datetime.now()
        self.logs: deque = deque(maxlen=20)
        self.account_status: dict[str, str] = {}
        self.errors: list[str] = []
        self._live = None

    def log(self, level: str, message: str):
        with self._lock:
            ts = datetime.now().strftime("%H:%M:%S")
            self.logs.append((ts, level, message))

    def mark_success(self, name: str):
        with self._lock:
            self.success += 1
            self.current += 1
            self.account_status[name] = "[green]成功[/green]"

    def mark_failed(self, name: str, error: str):
        with self._lock:
            self.failed += 1
            self.current += 1
            self.account_status[name] = "[red]失败[/red]"
            self.errors.append(f"{name}: {error}")

    def mark_processing(self, name: str):
        with self._lock:
            self.account_status[name] = "[yellow]处理中...[/yellow]"

    def build_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", size=14),
            Layout(name="logs"),
        )
        layout["body"].split_row(
            Layout(name="progress", ratio=1),
            Layout(name="accounts", ratio=1),
        )
        layout["header"].update(self._build_header())
        layout["progress"].update(self._build_progress())
        layout["accounts"].update(self._build_accounts())
        layout["logs"].update(self._build_logs())
        return layout

    def _build_header(self) -> Panel:
        text = Text()
        text.append("  AgentCoin 批量注册面板", style="bold cyan")
        text.append("  |  ", style="dim")
        elapsed = datetime.now() - self.start_time
        mins = int(elapsed.total_seconds() // 60)
        secs = int(elapsed.total_seconds() % 60)
        text.append(f"运行: {mins}分{secs}秒", style="dim")
        text.append("  |  ", style="dim")
        text.append(f"并发: {self.workers}", style="dim yellow")
        return Panel(text, style="cyan")

    def _build_progress(self) -> Panel:
        table = Table(show_header=False, expand=True, box=None, padding=(0, 1))
        table.add_column("项", style="bold", width=14)
        table.add_column("值")

        total_reg = get_stats()["registered"]
        pct = f"{self.current}/{self.todo}" if self.todo > 0 else "0/0"

        bar_len = 20
        filled = int(bar_len * self.current / self.todo) if self.todo > 0 else 0
        bar = "[green]" + "█" * filled + "[/green]" + "[dim]░[/dim]" * (bar_len - filled)

        table.add_row("账号总数", f"[bold]{self.total}[/bold]")
        table.add_row("本次待注册", f"[bold]{self.todo}[/bold]")
        table.add_row("", "")
        table.add_row("当前进度", f"{bar} {pct}")
        table.add_row("", "")
        table.add_row("成功", f"[bold green]{self.success}[/bold green]")
        table.add_row("失败", f"[bold red]{self.failed}[/bold red]")
        table.add_row("", "")
        table.add_row("累计已注册", f"[bold cyan]{total_reg}[/bold cyan]")

        # 速率
        elapsed = (datetime.now() - self.start_time).total_seconds()
        if elapsed > 0 and self.current > 0:
            rate = self.current / elapsed * 60
            eta_min = int((self.todo - self.current) / (self.current / elapsed) / 60) if self.current > 0 else 0
            table.add_row("速率", f"{rate:.1f} 个/分钟")
            table.add_row("预计剩余", f"~{eta_min} 分钟")

        return Panel(table, title="[bold]注册进度[/bold]", border_style="blue")

    def _build_accounts(self) -> Panel:
        table = Table(show_header=True, expand=True, box=None, padding=(0, 1))
        table.add_column("账号", style="dim", max_width=22, no_wrap=True)
        table.add_column("状态", max_width=12)

        items = list(self.account_status.items())
        show = items[-10:] if len(items) > 10 else items
        for name, status in show:
            display_name = name[:20] + ".." if len(name) > 20 else name
            table.add_row(display_name, status)

        if len(items) > 10:
            table.add_row(f"[dim]... 还有 {len(items) - 10} 个[/dim]", "")

        return Panel(table, title="[bold]账号状态[/bold]", border_style="green")

    def _build_logs(self) -> Panel:
        text = Text()
        if not self.logs:
            text.append("  等待注册开始...", style="dim")
        else:
            for ts, level, message in self.logs:
                color = LOG_COLORS.get(level, "white")
                text.append(f"  {ts} ", style="dim")
                text.append(f"[{level}]", style=color)
                text.append(f" {message}\n", style="white" if level != "系统" else "dim")
        return Panel(text, title="[bold]运行日志[/bold]", border_style="yellow")

    def start(self) -> Live:
        self._live = Live(
            self.build_layout(),
            console=self.console,
            refresh_per_second=2,
            screen=True,
        )
        return self._live

    def refresh(self):
        if self._live:
            self._live.update(self.build_layout())


# ═══════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="AgentCoin Agent 注册")
    parser.add_argument("--batch", action="store_true", help="批量注册模式（从 data/accounts.txt 读取）")
    parser.add_argument("--start", type=int, default=0, help="批量起始序号")
    parser.add_argument("--count", type=int, default=None, help="批量数量（默认全部）")
    parser.add_argument("--workers", type=int, default=3, help="并发数（默认3）")
    args = parser.parse_args()

    if args.batch:
        batch_register(args.start, args.count, args.workers)
    else:
        single_register()


def single_register():
    """单账号注册"""
    console.print(Panel(
        "[bold cyan]AgentCoin 单账号注册[/bold cyan]\n"
        "[dim]钱包生成 → X 账号绑定 → 链上注册[/dim]",
        border_style="cyan",
    ))

    config.check_required_config(mode="register")
    _verify_proxy()

    console.print("\n[bold cyan]═══ 步骤 1/3: 钱包 ═══[/bold cyan]\n")
    account = wallet.load_or_create_wallet()
    w3 = _connect_chain()
    bal = wallet.get_balance(w3, account.address)
    console.print(f"[dim]  ETH 余额: {bal['eth']} ETH[/dim]")

    bind_result = x_binding.bind_x_account(account.address)
    if not bind_result["success"]:
        console.print(f"\n[red]✗ X 绑定失败: {bind_result.get('error')}[/red]")
        sys.exit(1)

    reg_result = registry.register_agent_onchain(w3, account, config.X_HANDLE)
    if not reg_result["success"]:
        console.print(f"\n[red]✗ 链上注册失败: {reg_result.get('error')}[/red]")
        sys.exit(1)

    registry.save_state(account.address, reg_result["agent_id"], config.X_HANDLE, config.PRIVATE_KEY)
    _print_summary(account.address, reg_result["agent_id"], config.X_HANDLE)
    console.print("\n[bold green]✓ 注册完成！运行 python mine.py 开始挖矿[/bold green]\n")


def batch_register(start: int, count: int | None, workers: int):
    """批量注册 - 带实时可视化面板"""
    _verify_proxy()
    w3 = _connect_chain()

    accounts = load_account_range(start, count)
    if not accounts:
        console.print("[red]✗ 未找到账号数据，请检查 data/accounts.txt[/red]")
        sys.exit(1)

    stats = get_stats()
    todo_accounts = [a for a in accounts if not is_registered_by_token(a.auth_token)]

    if not todo_accounts:
        console.print("[green]所有账号已注册完成！[/green]")
        return

    dash = RegisterDashboard(
        total=stats["total"],
        todo=len(todo_accounts),
        workers=workers,
    )
    dash.log("系统", f"账号总数: {stats['total']} | 已注册: {stats['registered']}")
    dash.log("系统", f"本次待注册: {len(todo_accounts)} | 并发: {workers}")

    with dash.start():
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for acc in todo_accounts:
                future = executor.submit(_register_one_account_live, w3, acc, dash)
                futures[future] = acc

            for future in as_completed(futures):
                acc = futures[future]
                try:
                    result = future.result()
                    if result["success"]:
                        dash.mark_success(acc.agent_name)
                        save_registered_account(
                            auth_token=acc.auth_token,
                            wallet_address=result["wallet"],
                            private_key=result["private_key"],
                            x_handle=result.get("x_handle", ""),
                        )
                        dash.log("成功", f"{acc.agent_name} 注册成功 → {result['wallet'][:10]}...")
                    else:
                        error = result.get("error", "未知")
                        dash.mark_failed(acc.agent_name, error)
                        dash.log("错误", f"{acc.agent_name} 失败: {error}")
                except Exception as e:
                    dash.mark_failed(acc.agent_name, str(e))
                    dash.log("错误", f"{acc.agent_name} 异常: {e}")

                dash.refresh()

        dash.log("系统", "批量注册完成！")
        dash.refresh()
        time.sleep(3)

    # 退出面板后打印最终结果
    final_stats = get_stats()
    console.print(f"\n[bold]═══ 批量注册完成 ═══[/bold]")
    console.print(f"  [green]成功: {dash.success}[/green]")
    console.print(f"  [red]失败: {dash.failed}[/red]")
    console.print(f"  [cyan]累计已注册: {final_stats['registered']}/{final_stats['total']}[/cyan]")

    if dash.errors:
        console.print(f"\n[yellow]失败详情:[/yellow]")
        for err in dash.errors[:20]:
            console.print(f"  [dim]• {err}[/dim]")

    console.print(f"\n[dim]已注册账号保存在: data/registered.txt[/dim]")
    console.print(f"[bold green]运行 python mine.py --batch 开始批量挖矿[/bold green]\n")


def _register_one_account_live(w3, acc: AccountInfo, dash: RegisterDashboard) -> dict:
    """注册单个账号（线程安全，日志输出到面板）"""
    try:
        dash.mark_processing(acc.agent_name)
        dash.log("信息", f"{acc.agent_name}: 生成新钱包...")
        dash.refresh()

        new_account = Account.create()
        new_wallet = new_account.address
        new_private_key = new_account.key.hex()

        dash.log("信息", f"{acc.agent_name}: 钱包 {new_wallet[:10]}... → 开始 X 绑定")
        dash.refresh()

        bind_result = x_binding.bind_x_account(
            wallet_address=new_wallet,
            account_info=acc,
        )
        if not bind_result["success"]:
            return {"success": False, "error": f"X绑定失败: {bind_result.get('error')}"}

        return {
            "success": True,
            "x_handle": bind_result.get("x_handle", acc.x_handle),
            "wallet": new_wallet,
            "private_key": new_private_key,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


# ─── 工具方法 ───

def _verify_proxy():
    proxy_url = config.get_proxy_url()
    if proxy_url:
        console.print("[bold]代理检测...[/bold]")
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            exit_ip = config.verify_proxy()
            if exit_ip:
                console.print(f"[green]  ✓ 代理连通，出口 IP: {exit_ip}[/green]")
                return
            console.print(f"[yellow]  ⚠ 第 {attempt}/{max_retries} 次代理连接失败，重试中...[/yellow]")
            if attempt < max_retries:
                time.sleep(3)
        console.print("[red]  ✗ 代理连接失败（已重试3次），将尝试直连[/red]")


def _connect_chain():
    import time as _time
    console.print("[bold]Base 链连接检测...[/bold]")
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            w3 = contracts.get_w3()
            if w3.is_connected():
                console.print(f"[green]  ✓ Base 链已连接[/green] (chainId: {w3.eth.chain_id})")
                return w3
            else:
                console.print(f"[yellow]  ⚠ 第 {attempt}/{max_retries} 次连接失败，重试中...[/yellow]")
        except Exception as e:
            console.print(f"[yellow]  ⚠ 第 {attempt}/{max_retries} 次连接异常: {str(e)[:60]}[/yellow]")
        if attempt < max_retries:
            _time.sleep(5)

    console.print("[red]✗ 无法连接 Base 链（已尝试所有 RPC 节点）[/red]")
    console.print("[dim]  可在 .env 中修改 BASE_RPC_URL 或检查代理/网络[/dim]")
    sys.exit(1)


def _print_summary(wallet_addr: str, agent_id: int, x_handle: str):
    table = Table(title="注册摘要", show_header=False, border_style="green")
    table.add_column("项目", style="bold")
    table.add_column("值", style="cyan")
    table.add_row("钱包地址", wallet_addr)
    table.add_row("Agent ID", str(agent_id))
    table.add_row("X 账号", f"@{x_handle}")
    table.add_row("状态", "[bold green]已注册[/bold green]")
    console.print()
    console.print(Panel(table, border_style="green"))


if __name__ == "__main__":
    main()
