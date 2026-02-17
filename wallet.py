"""
AgentCoin 钱包管理
自动检测/生成钱包，持久化私钥到 .env
"""

import re
from pathlib import Path
from eth_account import Account
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import config

console = Console()


def load_or_create_wallet() -> Account:
    """
    加载或创建钱包
    - 如果 .env 中有 PRIVATE_KEY，直接加载
    - 否则生成新钱包，写入 .env，提示充值 gas
    返回 eth_account.Account 对象
    """
    if config.PRIVATE_KEY:
        account = Account.from_key(config.PRIVATE_KEY)
        console.print(f"[green]✓ 钱包已加载[/green]: {account.address}")
        return account

    # 生成新钱包
    account = Account.create()
    private_key = account.key.hex()

    # 写入 .env
    _save_private_key(private_key)

    # 更新运行时配置
    config.PRIVATE_KEY = private_key

    # 显示新钱包信息
    table = Table(title="新钱包已生成", show_header=False, border_style="green")
    table.add_row("地址", f"[bold cyan]{account.address}[/bold cyan]")
    table.add_row("私钥", f"[dim]{private_key[:10]}...{private_key[-6:]}[/dim]")
    console.print(Panel(table, border_style="green"))
    console.print(
        "[yellow]⚠ 请向以上地址充值少量 Base ETH 作为 gas 费用[/yellow]\n"
        "[dim]  Base 链 gas 费极低，0.001 ETH 即可支撑大量交易[/dim]"
    )

    return account


def _save_private_key(private_key: str):
    """将私钥写入 .env 文件"""
    env_path = config.ENV_PATH

    if env_path.exists():
        content = env_path.read_text()
        # 替换已有的空 PRIVATE_KEY
        if re.search(r"^PRIVATE_KEY\s*=", content, re.MULTILINE):
            content = re.sub(
                r"^PRIVATE_KEY\s*=.*$",
                f"PRIVATE_KEY={private_key}",
                content,
                flags=re.MULTILINE,
            )
        else:
            content += f"\nPRIVATE_KEY={private_key}\n"
        env_path.write_text(content)
    else:
        env_path.write_text(f"PRIVATE_KEY={private_key}\n")

    console.print(f"[dim]私钥已保存到 {env_path}[/dim]")


def get_balance(w3, address: str) -> dict:
    """查询钱包余额"""
    eth_balance = w3.eth.get_balance(address)
    return {
        "eth_wei": eth_balance,
        "eth": w3.from_wei(eth_balance, "ether"),
    }
