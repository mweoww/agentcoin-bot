"""
链上 Agent 注册模块
计算 xAccountHash -> 调用 AgentRegistry.registerAgent -> 持久化状态
"""

import json

import requests
from eth_account import Account
from rich.console import Console
from web3 import Web3

import config
import contracts

console = Console()


def register_agent_onchain(w3: Web3, account: Account, x_handle: str) -> dict:
    """
    链上注册 Agent
    1. 检查是否已注册
    2. 计算 xAccountHash
    3. 调用 registerAgent
    4. 回调确认
    返回: {"success": bool, "agent_id": int}
    """
    console.print("\n[bold cyan]═══ 链上注册 Agent ═══[/bold cyan]\n")

    c = contracts.get_contracts(w3)

    # 检查是否已注册
    try:
        existing_id = c["registry"].functions.getAgentId(account.address).call()
        if existing_id > 0:
            console.print(f"[green]✓ 钱包已注册，Agent ID: {existing_id}[/green]")
            return {"success": True, "agent_id": existing_id}
    except Exception:
        pass  # 未注册，继续

    # 计算 xAccountHash
    x_handle_normalized = x_handle.lower().strip()
    if not x_handle_normalized.startswith("@"):
        x_handle_normalized = f"@{x_handle_normalized}"

    x_hash = Web3.keccak(text=x_handle_normalized)
    console.print(f"[dim]  X 账号: {x_handle_normalized}[/dim]")
    console.print(f"[dim]  xAccountHash: {x_hash.hex()}[/dim]")

    # 检查 ETH 余额
    balance = w3.eth.get_balance(account.address)
    eth_balance = w3.from_wei(balance, "ether")
    console.print(f"[dim]  ETH 余额: {eth_balance} ETH[/dim]")

    if balance == 0:
        console.print("[red]  ✗ 钱包 ETH 余额为 0，无法支付 gas 费[/red]")
        console.print(f"[yellow]  请向 {account.address} 充值少量 Base ETH[/yellow]")
        return {"success": False, "error": "余额不足"}

    # 调用 registerAgent
    console.print("[bold]  发送注册交易...[/bold]")
    try:
        tx_func = c["registry"].functions.registerAgent(x_hash)
        receipt = contracts.send_tx(w3, account, tx_func)

        if receipt["status"] == 1:
            # 获取 agentId
            agent_id = c["registry"].functions.getAgentId(account.address).call()
            console.print(f"[bold green]  ✓ 注册成功！Agent ID: {agent_id}[/bold green]")
            console.print(f"[dim]  TX: {receipt['transactionHash'].hex()}[/dim]")

            # 回调确认
            _confirm_registration(account.address, agent_id, x_handle, x_hash.hex())

            return {"success": True, "agent_id": agent_id}
        else:
            console.print(f"[red]  ✗ 交易失败 (status=0)[/red]")
            return {"success": False, "error": "交易失败"}

    except Exception as e:
        console.print(f"[red]  ✗ 注册失败: {e}[/red]")
        return {"success": False, "error": str(e)}


def _confirm_registration(wallet: str, agent_id: int, x_handle: str, x_hash: str):
    """回调 AgentCoin API 确认注册"""
    try:
        resp = requests.post(
            f"{config.AGC_API_BASE}/api/x/confirm-registration",
            json={
                "wallet": wallet,
                "agent_id": agent_id,
                "x_handle": x_handle,
                "x_hash": x_hash,
            },
            proxies=config.get_proxies(),
            timeout=15,
        )
        if resp.status_code == 200:
            console.print("[dim]  ✓ 注册回调确认成功[/dim]")
        else:
            console.print(f"[dim]  ⚠ 注册回调返回 {resp.status_code}（不影响链上注册）[/dim]")
    except Exception as e:
        console.print(f"[dim]  ⚠ 注册回调失败: {e}（不影响链上注册）[/dim]")


def save_state(wallet_address: str, agent_id: int, x_handle: str, private_key: str):
    """保存注册状态到 .state.json"""
    state = {
        "wallet": wallet_address,
        "agent_id": agent_id,
        "x_handle": x_handle,
        "private_key_hint": f"{private_key[:6]}...{private_key[-4:]}",
        "registered": True,
    }
    config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    console.print(f"[dim]  ✓ 状态已保存到 {config.STATE_FILE}[/dim]")


def load_state() -> dict | None:
    """加载注册状态"""
    if not config.STATE_FILE.exists():
        return None
    try:
        return json.loads(config.STATE_FILE.read_text())
    except Exception:
        return None
