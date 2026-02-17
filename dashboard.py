"""
Rich 可视化面板
实时显示挖矿状态、收益统计、滚动日志
"""

from collections import deque
from datetime import datetime

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# 日志级别颜色映射
LOG_COLORS = {
    "信息": "cyan",
    "成功": "green",
    "警告": "yellow",
    "错误": "red",
    "奖励": "bold magenta",
    "系统": "dim",
}


class Dashboard:
    """Rich 实时可视化面板"""

    def __init__(self, wallet: str, agent_id: int, x_handle: str):
        self.wallet = wallet
        self.agent_id = agent_id
        self.x_handle = x_handle
        self.console = Console()
        self.logs: deque = deque(maxlen=15)
        self.stats = {}
        self._live = None

    def log(self, level: str, message: str):
        """添加日志条目"""
        ts = datetime.now().strftime("%H:%M:%S")
        self.logs.append((ts, level, message))

    def update_stats(self, stats: dict):
        """更新统计数据"""
        self.stats = stats

    def build_layout(self) -> Layout:
        """构建面板布局"""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="logs", size=19),
        )
        layout["body"].split_row(
            Layout(name="status", ratio=1),
            Layout(name="earnings", ratio=1),
        )

        layout["header"].update(self._build_header())
        layout["status"].update(self._build_status())
        layout["earnings"].update(self._build_earnings())
        layout["logs"].update(self._build_logs())

        return layout

    def _build_header(self) -> Panel:
        """顶部标题栏"""
        text = Text()
        text.append("  AgentCoin 挖矿面板", style="bold cyan")
        text.append("  │  ", style="dim")
        text.append(f"钱包: {self.wallet[:6]}...{self.wallet[-4:]}", style="dim")
        text.append("  │  ", style="dim")
        text.append(f"Agent: #{self.agent_id}", style="bold yellow")
        text.append("  │  ", style="dim")
        text.append(f"@{self.x_handle}", style="dim cyan")
        return Panel(text, style="cyan")

    def _build_status(self) -> Panel:
        """当前题目状态面板"""
        table = Table(show_header=False, expand=True, box=None, padding=(0, 1))
        table.add_column("项", style="bold", width=12)
        table.add_column("值")

        pid = self.stats.get("current_problem_id")
        status = self.stats.get("current_status", "空闲")
        last_tx = self.stats.get("last_submit_tx")
        streak = self.stats.get("streak", 0)
        correct = self.stats.get("correct_count", 0)

        # 状态颜色
        status_style = "yellow"
        if "成功" in status or "已提交" in status:
            status_style = "green"
        elif "错误" in status or "失败" in status:
            status_style = "red"
        elif "空闲" in status or "等待" in status:
            status_style = "dim"

        table.add_row("当前题目", f"[bold]#{pid}[/bold]" if pid else "[dim]无[/dim]")
        table.add_row("运行状态", f"[{status_style}]{status}[/{status_style}]")
        table.add_row("", "")

        # 连胜显示
        streak_display = f"{streak}"
        if streak >= 5:
            streak_display += " [bold red]MAX[/bold red]"
        elif streak >= 3:
            streak_display += " [yellow]🔥[/yellow]"

        table.add_row("连胜次数", streak_display)
        table.add_row("正确次数", str(correct))

        if last_tx:
            table.add_row("最近TX", f"[dim]{last_tx[:20]}...[/dim]")
        else:
            table.add_row("最近TX", "[dim]无[/dim]")

        return Panel(table, title="[bold]当前状态[/bold]", border_style="blue")

    def _build_earnings(self) -> Panel:
        """收益统计面板"""
        table = Table(show_header=False, expand=True, box=None, padding=(0, 1))
        table.add_column("项", style="bold", width=12)
        table.add_column("值")

        agc_balance = self.stats.get("agc_balance", 0)
        pending = self.stats.get("pending_rewards", 0)
        total_rewards = self.stats.get("total_rewards", 0)
        solved = self.stats.get("problems_solved", 0)
        submitted = self.stats.get("problems_submitted", 0)

        # 运行时间
        start = self.stats.get("start_time")
        if start:
            elapsed = datetime.now() - start
            hours = int(elapsed.total_seconds() // 3600)
            minutes = int((elapsed.total_seconds() % 3600) // 60)
            runtime = f"{hours}小时{minutes}分"
        else:
            runtime = "0分"

        table.add_row("AGC 余额", f"[bold green]{agc_balance:,.2f}[/bold green]")
        table.add_row("待领取", f"[bold yellow]{pending:,.2f}[/bold yellow]" if pending > 0 else "[dim]0.00[/dim]")
        table.add_row("累计收益", f"[cyan]{total_rewards:,.2f}[/cyan]")
        table.add_row("", "")
        table.add_row("已解题数", str(solved))
        table.add_row("已提交数", str(submitted))
        table.add_row("运行时间", runtime)

        return Panel(table, title="[bold]收益统计[/bold]", border_style="green")

    def _build_logs(self) -> Panel:
        """滚动日志面板"""
        text = Text()

        if not self.logs:
            text.append("  等待挖矿开始...", style="dim")
        else:
            for ts, level, message in self.logs:
                color = LOG_COLORS.get(level, "white")
                text.append(f"  {ts} ", style="dim")
                text.append(f"[{level}]", style=color)
                text.append(f" {message}\n", style="white" if level != "系统" else "dim")

        return Panel(text, title="[bold]运行日志[/bold]", border_style="yellow")

    def start(self) -> Live:
        """启动 Live 面板"""
        self._live = Live(
            self.build_layout(),
            console=self.console,
            refresh_per_second=2,
            screen=True,
        )
        return self._live

    def refresh(self):
        """刷新面板"""
        if self._live:
            self._live.update(self.build_layout())
