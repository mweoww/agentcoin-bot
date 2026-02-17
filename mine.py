#!/usr/bin/env python3
"""
AgentCoin 挖矿模块入口
支持单账号（可视化面板）和批量模式（多线程并发 + Live 面板）

用法:
  python mine.py                          # 单账号（从 .state.json 加载）
  python mine.py --batch                  # 批量挖矿所有账号
  python mine.py --batch --start 0 --count 10 --workers 5  # 批量指定范围
"""

import argparse
import json
import signal
import sys
import time
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

from eth_account import Account
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import config
import contracts
import registry
from accounts import RegisteredAccount, load_registered_range, get_stats, lookup_x_handle_by_token
from dashboard import Dashboard
from miner import Miner, ProblemPoller
from solver import Solver

MINE_STATUS_FILE = Path(__file__).parent / "data" / "mine_status.json"
MINE_STATS_FILE = Path(__file__).parent / "data" / "mine_stats.json"

console = Console()

_running = True


def _signal_handler(sig, frame):
    global _running
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def _load_persistent_stats() -> dict:
    """加载历史累计统计"""
    default = {"total_solved": 0, "total_submitted": 0, "total_rewards": 0.0, "total_errors": 0}
    if not MINE_STATS_FILE.exists():
        return default
    try:
        data = json.loads(MINE_STATS_FILE.read_text())
        return {k: data.get(k, default[k]) for k in default}
    except Exception:
        return default


def _save_persistent_stats(solved: int, submitted: int, rewards: float, errors: int):
    """保存累计统计到磁盘"""
    try:
        MINE_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {"total_solved": solved, "total_submitted": submitted,
                "total_rewards": rewards, "total_errors": errors,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        MINE_STATS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


# ═══════════════════════════════════════════
# 批量挖矿可视化面板
# ═══════════════════════════════════════════

LOG_COLORS = {
    "信息": "cyan",
    "成功": "green",
    "警告": "yellow",
    "错误": "red",
    "奖励": "bold magenta",
    "系统": "dim",
    "提交": "bold green",
}


class BatchMineDashboard:
    """批量挖矿实时可视化面板"""

    def __init__(self, total_accounts: int, workers: int):
        self.total_accounts = total_accounts
        self.workers = workers
        self.console = Console()
        self._lock = threading.Lock()

        # 加载历史累计数据
        hist = _load_persistent_stats()
        self.active = 0
        self.total_submitted = hist["total_submitted"]
        self.total_solved = hist["total_solved"]
        self.total_rewards = hist["total_rewards"]
        self.total_errors = hist["total_errors"]
        self.start_time = datetime.now()
        self.logs: deque = deque(maxlen=100)
        self.account_info: dict[str, dict] = {}
        self._live = None

    def log(self, level: str, message: str):
        with self._lock:
            ts = datetime.now().strftime("%H:%M:%S")
            self.logs.append((ts, level, message))

    def update_account(self, addr: str, status: str, solved: int = 0, submitted: int = 0, rewards: float = 0.0):
        with self._lock:
            self.account_info[addr] = {
                "status": status,
                "solved": solved,
                "submitted": submitted,
                "rewards": rewards,
            }

    def inc_submitted(self):
        with self._lock:
            self.total_submitted += 1

    def inc_solved(self):
        with self._lock:
            self.total_solved += 1

    def inc_errors(self):
        with self._lock:
            self.total_errors += 1

    def add_rewards(self, amount: float):
        with self._lock:
            self.total_rewards += amount

    def set_rewards(self, total: float):
        """直接设置累计收益（从矿工链上数据汇总）"""
        with self._lock:
            self.total_rewards = total

    def set_active(self, n: int):
        with self._lock:
            self.active = n

    def build_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", size=16),
            Layout(name="logs"),
        )
        layout["body"].split_row(
            Layout(name="stats", ratio=1),
            Layout(name="accounts", ratio=1),
        )
        layout["header"].update(self._build_header())
        layout["stats"].update(self._build_stats())
        layout["accounts"].update(self._build_accounts())
        layout["logs"].update(self._build_logs())
        return layout

    def _build_header(self) -> Panel:
        text = Text()
        text.append("  AgentCoin 批量挖矿面板", style="bold cyan")
        text.append("  |  ", style="dim")
        elapsed = datetime.now() - self.start_time
        hours = int(elapsed.total_seconds() // 3600)
        mins = int((elapsed.total_seconds() % 3600) // 60)
        secs = int(elapsed.total_seconds() % 60)
        text.append(f"运行: {hours}时{mins}分{secs}秒", style="dim")
        text.append("  |  ", style="dim")
        text.append(f"活跃: {self.active}/{self.total_accounts}", style="bold green")
        text.append("  |  ", style="dim")
        text.append(f"并发: {self.workers}", style="dim yellow")
        return Panel(text, style="cyan")

    def _build_stats(self) -> Panel:
        table = Table(show_header=False, expand=True, box=None, padding=(0, 1))
        table.add_column("项", style="bold", width=14)
        table.add_column("值")

        table.add_row("活跃矿工", f"[bold green]{self.active}[/bold green] / {self.total_accounts}")
        table.add_row("", "")
        table.add_row("已解题", f"[bold cyan]{self.total_solved}[/bold cyan]")
        table.add_row("已提交", f"[bold green]{self.total_submitted}[/bold green]")
        table.add_row("错误次数", f"[bold red]{self.total_errors}[/bold red]")
        table.add_row("", "")
        table.add_row("累计收益", f"[bold magenta]{self.total_rewards:,.2f} AGC[/bold magenta]")

        elapsed = (datetime.now() - self.start_time).total_seconds()
        if elapsed > 0 and self.total_submitted > 0:
            rate = self.total_submitted / elapsed * 3600
            table.add_row("提交速率", f"{rate:.1f} 次/小时")

        table.add_row("", "")
        table.add_row("轮询间隔", f"{config.POLL_INTERVAL}s")

        return Panel(table, title="[bold]挖矿统计[/bold]", border_style="blue")

    def _build_accounts(self) -> Panel:
        table = Table(show_header=True, expand=True, box=None, padding=(0, 1))
        table.add_column("钱包", style="dim", max_width=14, no_wrap=True)
        table.add_column("状态", max_width=12)
        table.add_column("解题", justify="right", max_width=5)
        table.add_column("提交", justify="right", max_width=5)

        items = list(self.account_info.items())
        show = items[:12]
        for addr, info in show:
            display = addr[:6] + "..." + addr[-4:] if len(addr) > 12 else addr
            status = info["status"]
            if "运行" in status:
                status = f"[green]{status}[/green]"
            elif "等待" in status:
                status = f"[yellow]{status}[/yellow]"
            elif "错误" in status or "失败" in status:
                status = f"[red]{status}[/red]"
            elif "停止" in status:
                status = f"[dim]{status}[/dim]"
            table.add_row(display, status, str(info["solved"]), str(info["submitted"]))

        if len(items) > 12:
            table.add_row(f"[dim]... +{len(items) - 12}[/dim]", "", "", "")

        return Panel(table, title="[bold]矿工状态[/bold]", border_style="green")

    def _build_logs(self) -> Panel:
        text = Text()
        if not self.logs:
            text.append("  等待挖矿开始...", style="dim")
        else:
            for ts, level, message in self.logs:
                color = LOG_COLORS.get(level, "white")
                text.append(f"  {ts} ", style="dim")
                text.append(f"[{level}]", style=color)
                text.append(f" {message}\n", style="white" if level not in ("系统",) else "dim")
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

    def save_status(self):
        """将挖矿状态写入 data/mine_status.json 供 Web 面板读取"""
        try:
            elapsed = (datetime.now() - self.start_time).total_seconds()
            miners = []
            with self._lock:
                for addr, info in self.account_info.items():
                    miners.append({
                        "wallet": addr,
                        "status": info["status"],
                        "solved": info["solved"],
                        "submitted": info["submitted"],
                        "rewards": info["rewards"],
                    })
                logs = [{"time": t, "level": l, "message": m} for t, l, m in list(self.logs)[-50:]]

            data = {
                "running": True,
                "start_time": self.start_time.isoformat(),
                "elapsed_seconds": int(elapsed),
                "total_accounts": self.total_accounts,
                "active": self.active,
                "total_solved": self.total_solved,
                "total_submitted": self.total_submitted,
                "total_rewards": self.total_rewards,
                "total_errors": self.total_errors,
                "miners": miners,
                "logs": logs,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            MINE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            MINE_STATUS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            # 同步持久化累计统计
            _save_persistent_stats(self.total_solved, self.total_submitted,
                                   self.total_rewards, self.total_errors)
        except Exception:
            pass


# ═══════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="AgentCoin 自动挖矿")
    parser.add_argument("--batch", action="store_true", help="批量挖矿模式（从 data/registered.txt）")
    parser.add_argument("--start", type=int, default=0, help="批量起始序号")
    parser.add_argument("--count", type=int, default=None, help="批量数量")
    parser.add_argument("--workers", type=int, default=5, help="并发数（默认5）")
    args = parser.parse_args()

    if args.batch:
        batch_mine(args.start, args.count, args.workers)
    else:
        single_mine()


# ═══════════════════════════════════════════
# 单账号模式（带可视化面板）
# ═══════════════════════════════════════════

def single_mine():
    """单账号挖矿（带 Rich 面板）"""
    console.print(Panel(
        "[bold cyan]AgentCoin 自动挖矿[/bold cyan]\n"
        "[dim]持续轮询 → AI 解题 → 提交答案 → 领取奖励[/dim]",
        border_style="cyan",
    ))

    state = registry.load_state()
    if not state or not state.get("registered"):
        console.print("[red]✗ 未找到注册状态，请先运行 python register.py[/red]")
        sys.exit(1)

    agent_id = state["agent_id"]
    wallet_addr = state["wallet"]
    x_handle = state["x_handle"]

    console.print(f"[green]✓ 已加载注册状态[/green]")
    console.print(f"  Agent ID: [bold]{agent_id}[/bold]  钱包: [dim]{wallet_addr}[/dim]")

    config.check_required_config(mode="mine")
    if not config.PRIVATE_KEY:
        console.print("[red]✗ 未配置 PRIVATE_KEY[/red]")
        sys.exit(1)

    _verify_connections()

    account = Account.from_key(config.PRIVATE_KEY)
    w3 = contracts.get_w3()

    console.print(f"\n[bold green]启动挖矿... 轮询间隔: {config.POLL_INTERVAL}秒[/bold green]")
    console.print("[dim]按 Ctrl+C 优雅退出[/dim]\n")

    dashboard = Dashboard(wallet_addr, agent_id, x_handle)
    miner_inst = Miner(w3, account, agent_id, log_fn=dashboard.log)

    miner_inst.update_chain_stats()
    dashboard.update_stats(miner_inst.stats)
    dashboard.log("系统", "挖矿引擎启动")
    dashboard.log("系统", f"Agent ID: {agent_id} | 轮询间隔: {config.POLL_INTERVAL}s")

    cycle_count = 0

    with dashboard.start():
        while _running:
            try:
                cycle_count += 1
                miner_inst.run_once()

                if cycle_count % 5 == 0:
                    miner_inst.update_chain_stats()
                    miner_inst.check_and_claim_rewards()

                dashboard.update_stats(miner_inst.stats)
                dashboard.refresh()

                # 智能轮询间隔
                poll = miner_inst.get_smart_poll_interval()
                for _ in range(poll):
                    if not _running:
                        break
                    time.sleep(1)
                    dashboard.refresh()

            except KeyboardInterrupt:
                break
            except Exception as e:
                dashboard.log("错误", f"主循环异常: {e}")
                dashboard.refresh()
                time.sleep(10)

    console.print("\n[bold yellow]挖矿已停止[/bold yellow]")
    _print_final_stats(miner_inst.stats)


# ═══════════════════════════════════════════
# 批量模式（多线程并发 + Live 面板）
# ═══════════════════════════════════════════

def batch_mine(start: int, count: int | None, workers: int):
    """
    批量挖矿 - 单调度线程轮询 + 并发答题
    架构：1 个调度线程查题目 → 发现新题目 → 所有矿工并发解题提交
    """
    _verify_connections()

    accounts = load_registered_range(start, count)
    if not accounts:
        stats = get_stats()
        console.print(f"[red]✗ 未找到已注册账号（data/registered.txt）[/red]")
        console.print(f"[dim]  账号总数: {stats['total']} | 已注册: {stats['registered']}[/dim]")
        console.print("[yellow]  请先运行 python register.py --batch 完成注册[/yellow]")
        sys.exit(1)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    w3 = contracts.get_w3()
    dash = BatchMineDashboard(total_accounts=len(accounts), workers=workers)
    dash.log("系统", f"加载 {len(accounts)} 个已注册账号")
    dash.log("系统", f"模式: 单轮询 + {workers} 并发答题")

    # 1. 并发初始化所有矿工（批量查 agent_id，大幅加速启动）
    miners: list[Miner] = []
    init_t0 = time.time()
    dash.log("系统", f"并发初始化 {len(accounts)} 个矿工...")

    init_workers = min(20, len(accounts))
    with ThreadPoolExecutor(max_workers=init_workers) as pool:
        futures = {pool.submit(_create_miner_for_account_live, w3, acc, dash): acc for acc in accounts}
        for future in as_completed(futures):
            acc = futures[future]
            try:
                miner = future.result()
                if miner:
                    addr = miner.account.address

                    def _make_log(a):
                        def _log(level, msg):
                            dash.log(level, f"{a[:8]}... {msg}")
                        return _log

                    miner.log = _make_log(addr)
                    miners.append(miner)
                    dash.update_account(addr, "就绪")
            except Exception as e:
                dash.log("错误", f"{acc.short_str()} 初始化异常: {e}")

    init_elapsed = round(time.time() - init_t0, 1)
    dash.log("系统", f"初始化完成: {len(miners)}/{len(accounts)} 个矿工就绪 ({init_elapsed}s)")

    if not miners:
        console.print("[red]✗ 没有可用的矿工[/red]")
        sys.exit(1)

    dash.set_active(len(miners))
    dash.log("成功", f"{len(miners)} 个矿工就绪，开始轮询题目...")

    # 2. 公共题目轮询器
    poller = ProblemPoller(w3)
    last_dispatched_id = None
    cycle_count = 0

    def _sync_miner(miner: Miner, status: str = None):
        """同步矿工状态到面板"""
        addr = miner.account.address
        st = status or miner.stats.get("current_status", "就绪")
        dash.update_account(addr, st,
                            solved=miner.stats["problems_solved"],
                            submitted=miner.stats["problems_submitted"],
                            rewards=miner.stats["total_rewards"])

    def _dispatch_problem(problem: dict):
        """将题目分发给所有未提交的矿工，并发执行"""
        nonlocal last_dispatched_id
        pid = problem["problem_id"]
        last_dispatched_id = pid

        # 筛选未提交且有 Gas 的矿工
        pending_miners = [m for m in miners if not m.has_submitted(pid) and not m.gas_exhausted]
        if not pending_miners:
            return

        dash.log("信息", f"题目 #{pid} 分发给 {len(pending_miners)} 个矿工并发答题")

        for m in pending_miners:
            _sync_miner(m, f"求解 #{pid}...")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(m.solve_and_submit, problem): m for m in pending_miners}
            for future in as_completed(futures):
                m = futures[future]
                try:
                    result = future.result()
                    action = result.get("action", "")
                    if action == "submitted":
                        dash.inc_solved()
                        dash.inc_submitted()
                    elif action == "gas_exhausted":
                        _sync_miner(m, "Gas不足")
                        dash.set_active(len([x for x in miners if not x.gas_exhausted]))
                    elif action == "error":
                        dash.inc_errors()
                    _sync_miner(m)
                except Exception as e:
                    dash.inc_errors()
                    dash.log("错误", f"{m.account.address[:8]}... 并发异常: {e}")

    with dash.start():
        dash.save_status()

        try:
            while _running:
                cycle_count += 1

                # 轮询题目（全局只查一次）
                problem = poller.poll()

                if problem and problem["is_active"]:
                    pid = problem["problem_id"]
                    deadline = problem["answer_deadline"]
                    remaining = deadline - int(time.time())

                    # 新题目或有矿工未提交 → 分发
                    pending = [m for m in miners if not m.has_submitted(pid) and not m.gas_exhausted]
                    if pending:
                        if pid != last_dispatched_id:
                            dash.log("信息", f"新题目 #{pid}，剩余 {remaining}s")
                        _dispatch_problem(problem)

                    # 更新所有矿工状态显示
                    for m in miners:
                        if m.gas_exhausted:
                            _sync_miner(m, "Gas不足")
                        elif m.has_submitted(pid):
                            _sync_miner(m, f"已提交 #{pid} ({remaining}s)")
                        else:
                            _sync_miner(m, f"等待新题目")

                    active_miners = [m for m in miners if not m.gas_exhausted]
                    all_submitted = all(m.has_submitted(pid) for m in active_miners) if active_miners else True
                else:
                    all_submitted = True
                    for m in miners:
                        if m.gas_exhausted:
                            _sync_miner(m, "Gas不足")
                        else:
                            _sync_miner(m, "等待新题目")

                # 定期检查奖励（汇总所有矿工的链上收益）
                if cycle_count % 20 == 0:
                    rewards_sum = 0.0
                    for m in miners:
                        try:
                            m.check_and_claim_rewards()
                            rewards_sum += m.stats.get("total_rewards", 0.0)
                        except Exception:
                            pass
                    if rewards_sum > 0:
                        dash.set_rewards(rewards_sum)

                # 智能等待，期间持续刷新面板
                wait = poller.get_smart_interval(all_submitted)
                for sec in range(wait, 0, -1):
                    if not _running:
                        break
                    pid_display = poller._last_problem_id or "?"
                    for m in miners:
                        if m.gas_exhausted:
                            continue
                        _sync_miner(m, f"等待中 {sec}s (#{pid_display})")
                    dash.refresh()
                    dash.save_status()
                    time.sleep(1)

        except KeyboardInterrupt:
            pass

    # 写入停止状态
    for m in miners:
        _sync_miner(m, "已停止")

    try:
        # 保存持久化统计
        _save_persistent_stats(dash.total_solved, dash.total_submitted,
                               dash.total_rewards, dash.total_errors)
        # 停止状态保留累计数据
        dash.save_status()
        # 标记为已停止（但保留统计和矿工列表）
        status = json.loads(MINE_STATUS_FILE.read_text())
        status["running"] = False
        status["active"] = 0
        MINE_STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2))
    except Exception:
        pass

    console.print("[bold yellow]批量挖矿已停止[/bold yellow]")
    console.print(f"  总解题: {dash.total_solved}")
    console.print(f"  总提交: {dash.total_submitted}")
    console.print(f"  总收益: {dash.total_rewards:,.2f} AGC")
    console.print(f"  总错误: {dash.total_errors}")


def _create_miner_for_account_live(w3, acc: RegisteredAccount, dash: BatchMineDashboard) -> Miner | None:
    """为单个已注册账号创建 Miner 实例，未链上注册则自动注册"""
    try:
        account = Account.from_key(acc.private_key)
        addr = account.address

        c = contracts.get_contracts(w3)
        agent_id = c["registry"].functions.getAgentId(account.address).call()

        if agent_id == 0:
            dash.log("警告", f"{addr[:10]}... 未链上注册，尝试自动注册...")
            dash.update_account(addr, "注册中...")

            x_handle = acc.x_handle
            if not x_handle:
                dash.log("信息", f"{addr[:10]}... registered.txt 中无 x_handle，从 accounts.txt 反查...")
                x_handle = lookup_x_handle_by_token(acc.auth_token)

            agent_id = _auto_register_onchain(w3, account, x_handle, dash)

            if agent_id == 0:
                # 注册失败，再查一次链上（可能之前已注册但本次交易失败）
                try:
                    agent_id = c["registry"].functions.getAgentId(account.address).call()
                except Exception:
                    pass
                if agent_id == 0:
                    dash.log("错误", f"{addr[:10]}... 未注册且注册失败，跳过")
                    return None
                else:
                    dash.log("信息", f"{addr[:10]}... 注册交易失败但链上已有 Agent ID: {agent_id}，继续挖矿")

        def log_fn(level, msg):
            dash.log(level, f"{addr[:8]}... {msg}")

        dash.update_account(addr, "运行中")
        return Miner(w3, account, agent_id, log_fn=log_fn)

    except Exception as e:
        dash.log("错误", f"{acc.short_str()} 初始化失败: {e}")
        return None


def _auto_register_onchain(w3, account, x_handle: str, dash: BatchMineDashboard) -> int:
    """
    自动链上注册 Agent
    返回 agent_id，失败返回 0
    """
    addr = account.address

    # 检查 x_handle
    if not x_handle:
        dash.log("错误", f"{addr[:10]}... 缺少 X 用户名，无法链上注册（registered.txt 和 accounts.txt 均未找到）")
        dash.update_account(addr, "缺少X用户名")
        return 0

    # 检查 ETH 余额
    balance = w3.eth.get_balance(account.address)
    eth_bal = float(w3.from_wei(balance, "ether"))

    if balance == 0:
        dash.log("错误", f"{addr[:10]}... ETH 余额为 0，无法支付 gas。请充值 Base ETH 到 {addr}")
        dash.update_account(addr, "Gas不足")
        return 0

    if eth_bal < 0.0001:
        dash.log("警告", f"{addr[:10]}... ETH 余额极低 ({eth_bal:.6f})，可能不够 gas")

    # 计算 xAccountHash
    x_normalized = x_handle.lower().strip()
    if not x_normalized.startswith("@"):
        x_normalized = f"@{x_normalized}"

    from web3 import Web3 as W3
    x_hash = W3.keccak(text=x_normalized)

    dash.log("信息", f"{addr[:10]}... 发送 registerAgent 交易 (x={x_normalized}, ETH={eth_bal:.6f})")

    try:
        c = contracts.get_contracts(w3)
        tx_func = c["registry"].functions.registerAgent(x_hash)
        receipt = contracts.send_tx(w3, account, tx_func)

        if receipt["status"] == 1:
            agent_id = c["registry"].functions.getAgentId(account.address).call()
            tx_hash = receipt["transactionHash"].hex()
            dash.log("成功", f"{addr[:10]}... 链上注册成功！Agent ID: {agent_id} TX: {tx_hash[:16]}...")

            # 回调确认（不影响主流程）
            try:
                import requests
                import config
                requests.post(
                    f"{config.AGC_API_BASE}/api/x/confirm-registration",
                    json={"wallet": addr, "agent_id": agent_id, "x_handle": x_handle, "x_hash": x_hash.hex()},
                    proxies=config.get_proxies(), timeout=15,
                )
            except Exception:
                pass

            return agent_id
        else:
            dash.log("错误", f"{addr[:10]}... 注册交易失败 (status=0)")
            dash.update_account(addr, "注册失败")
            return 0

    except Exception as e:
        error_msg = str(e)
        if "insufficient funds" in error_msg.lower() or "gas" in error_msg.lower():
            dash.log("错误", f"{addr[:10]}... Gas 不足: {error_msg[:80]}")
            dash.update_account(addr, "Gas不足")
        else:
            dash.log("错误", f"{addr[:10]}... 注册交易异常: {error_msg[:80]}")
            dash.update_account(addr, "注册异常")
        return 0


# ─── 工具方法 ───

def _verify_connections():
    """快速验证链连接（仅检查必要项）"""
    try:
        w3 = contracts.get_w3()
        if w3.is_connected():
            console.print(f"[green]✓ Base 链已连接 (区块: {w3.eth.block_number})[/green]")
        else:
            raise ConnectionError("未连接")
    except Exception as e:
        console.print(f"[red]✗ 无法连接 Base 链: {e}[/red]")
        sys.exit(1)


def _print_final_stats(stats: dict):
    """打印最终统计"""
    elapsed = datetime.now() - stats.get("start_time", datetime.now())
    hours = int(elapsed.total_seconds() // 3600)
    minutes = int((elapsed.total_seconds() % 3600) // 60)
    console.print(f"\n[bold]运行统计:[/bold]")
    console.print(f"  运行时间: {hours}小时{minutes}分")
    console.print(f"  已解题数: {stats.get('problems_solved', 0)}")
    console.print(f"  已提交数: {stats.get('problems_submitted', 0)}")
    console.print(f"  累计收益: {stats.get('total_rewards', 0):.2f} AGC")
    console.print()


if __name__ == "__main__":
    main()
