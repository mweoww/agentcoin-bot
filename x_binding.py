"""
X 账号绑定模块
使用 claim 流程：create-claim → 发推 → verify-claim
验证失败则重新创建 claim + 发推 + 验证，最多重试 5 轮
"""

import time

import requests
from rich.console import Console

import config
from accounts import AccountInfo
from x_client import StealthXClient

console = Console()

MAX_ROUNDS = 3
VERIFY_WAIT = 10
VERIFY_RETRIES = 3
VERIFY_INTERVAL = 10


def bind_x_account(wallet_address: str, account_info: AccountInfo = None) -> dict:
    """
    完整的 X 账号绑定流程
    每轮: create-claim → 发推 → 短暂等待 → 验证(3次)
    验证失败则重新开始新一轮（新 claim + 新推文）
    最多 MAX_ROUNDS 轮

    返回: {"success": bool, "x_handle": str, "verification_code": str}
    """
    if account_info:
        auth_token = account_info.auth_token
        ct0 = account_info.ct0
        label = account_info.short_str()
    else:
        auth_token = None
        ct0 = None
        label = f"@{config.X_HANDLE}"

    console.print(f"\n[bold cyan]═══ X 账号绑定 ({label}) ═══[/bold cyan]\n")

    client = StealthXClient(auth_token=auth_token, ct0=ct0)

    for round_num in range(1, MAX_ROUNDS + 1):
        console.print(f"[bold]── 第 {round_num}/{MAX_ROUNDS} 轮 ──[/bold]")

        # 1. 创建 claim
        claim = _create_claim()
        if not claim:
            console.print(f"[red]  ✗ 创建 claim 失败，跳过本轮[/red]")
            time.sleep(5)
            continue

        verification_code = claim["verification_code"]
        claim_token = claim["token"]
        console.print(f"[green]  ✓ 验证码: {verification_code}[/green]")

        # 2. 发推
        tweet_text = (
            f"I want to register my AI Agent! @agentcoinsite\n\n"
            f"Code: {verification_code}"
        )

        result = client.post_tweet(tweet_text)
        if not result["success"]:
            error = result.get("error", "未知错误")
            console.print(f"[red]  ✗ 发推失败: {error}[/red]")
            error_lower = error.lower()
            if "daily limit" in error_lower or "344" in error_lower or "日限" in error_lower or "每日" in error_lower:
                console.print(f"[red]  ✗ 推文日限，终止[/red]")
                return {"success": False, "error": "推文日限"}
            if "automated" in error_lower or "226" in error_lower:
                console.print(f"[red]  ✗ 自动化检测，终止[/red]")
                return {"success": False, "error": "自动化检测"}
            time.sleep(5)
            continue

        console.print(f"[green]  ✓ 发推成功[/green] (通道: {result['channel']})")

        # 3. 短暂等待后验证
        console.print(f"[dim]  等待 {VERIFY_WAIT}s 让 X 索引...[/dim]")
        time.sleep(VERIFY_WAIT)

        for attempt in range(1, VERIFY_RETRIES + 1):
            console.print(f"[dim]  验证 {attempt}/{VERIFY_RETRIES}...[/dim]")
            verify_result = _verify_claim(claim_token)

            if verify_result["success"]:
                x_handle = verify_result.get("x_handle", "")
                console.print(f"[bold green]  ✓ 绑定成功！(@{x_handle})[/bold green]")
                return {
                    "success": True,
                    "x_handle": x_handle,
                    "verification_code": verification_code,
                }

            if attempt < VERIFY_RETRIES:
                console.print(f"[yellow]  ⚠ 未通过，{VERIFY_INTERVAL}s 后重试...[/yellow]")
                time.sleep(VERIFY_INTERVAL)

        console.print(f"[yellow]  ⚠ 第 {round_num} 轮验证失败，重新发推...[/yellow]")

    console.print(f"[red]  ✗ {MAX_ROUNDS} 轮全部失败[/red]")
    return {"success": False, "error": f"{MAX_ROUNDS}轮验证均失败"}


def _create_claim() -> dict | None:
    """调用 POST /api/x/create-claim 获取验证码和 token"""
    try:
        resp = requests.post(
            f"{config.AGC_API_BASE}/api/x/create-claim",
            proxies=config.get_proxies(),
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "verification_code": data.get("verification_code"),
                "token": data.get("token"),
            }
        else:
            console.print(f"[red]  ✗ API 错误 {resp.status_code}: {resp.text[:200]}[/red]")
            return None
    except Exception as e:
        console.print(f"[red]  ✗ 请求失败: {e}[/red]")
        return None


def _verify_claim(token: str) -> dict:
    """调用 GET /api/x/verify-claim?token=xxx 验证"""
    try:
        resp = requests.get(
            f"{config.AGC_API_BASE}/api/x/verify-claim",
            params={"token": token},
            proxies=config.get_proxies(),
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "success": data.get("success", False),
                "x_handle": data.get("x_handle", ""),
            }
        else:
            return {"success": False, "error": resp.text[:200]}
    except Exception as e:
        return {"success": False, "error": str(e)}
