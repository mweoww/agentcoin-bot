"""
AgentCoin 全自动挖矿机器人 - 配置管理
优先从环境变量读取，fallback ke file .env
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env sebagai fallback (untuk development lokal)
ENV_PATH = Path(__file__).parent / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

# ─── SEMUA VARIABLE DIBACA DARI ENVIRONMENT (Railway Priority) ───
# Wallet
PRIVATE_KEY = os.environ.get("PRIVATE_KEY", "").strip()

# X (Twitter)
X_HANDLE = os.environ.get("X_HANDLE", "").strip()
X_API_KEY = os.environ.get("X_API_KEY", "").strip()
X_API_SECRET = os.environ.get("X_API_SECRET", "").strip()
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN", "").strip()
X_ACCESS_SECRET = os.environ.get("X_ACCESS_SECRET", "").strip()
X_AUTH_TOKEN = os.environ.get("X_AUTH_TOKEN", "").strip()
X_CT0 = os.environ.get("X_CT0", "").strip()

# Proxy
PROXY_HOST = os.environ.get("PROXY_HOST", "").strip()
PROXY_AUTH = os.environ.get("PROXY_AUTH", "").strip()

# AI (Claude)
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
# BISA TERIMA DUA NAMA: ANTHROPIC_AUTH_TOKEN atau ANTHROPIC_KEY
ANTHROPIC_AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_KEY") or ""

# Chain
BASE_RPC_URL = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org").strip()

# Runtime
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
AUTO_CLAIM = os.environ.get("AUTO_CLAIM", "true").lower() == "true"

# ─── Sisanya tetap sama ───
AGC_API_BASE = "https://api.agentcoin.site"
STATE_FILE = Path(__file__).parent / "data" / ".state.json"


# ═══════════════════════════════════════════
# 代理工厂方法
# ═══════════════════════════════════════════

def get_proxy_url() -> str | None:
    """构建带认证的代理URL: http://user:pass@host:port"""
    if not PROXY_HOST:
        return None
    if PROXY_AUTH:
        return f"http://{PROXY_AUTH}@{PROXY_HOST}"
    return f"http://{PROXY_HOST}"


def get_proxies() -> dict | None:
    """返回 requests 库格式的代理字典"""
    url = get_proxy_url()
    if not url:
        return None
    return {"http": url, "https": url}


def verify_proxy() -> str | None:
    """
    验证代理连通性，返回出口 IP 地址
    失败返回 None（尝试多个检测 URL）
    """
    import requests as _req
    proxy_url = get_proxy_url()
    if not proxy_url:
        return None
    proxies = {"http": proxy_url, "https": proxy_url}
    check_urls = [
        "https://ipv4.icanhazip.com",
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
    ]
    for url in check_urls:
        try:
            resp = _req.get(url, proxies=proxies, timeout=10)
            ip = resp.text.strip()
            if ip:
                return ip
        except Exception:
            continue
    return None


def check_required_config(mode: str = "mine"):
    """
    检查必要配置是否完整
    mode: 'register' 或 'mine'
    """
    missing = []

    if mode == "register":
        if not X_HANDLE:
            missing.append("X_HANDLE")
        # X 发推需要至少一个通道
        has_api = all([X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET])
        has_cookie = all([X_AUTH_TOKEN, X_CT0])
        if not has_api and not has_cookie:
            missing.append("X API 凭证 (X_API_KEY等) 或 X Cookie (X_AUTH_TOKEN+X_CT0)")

    if mode == "mine":
        if not ANTHROPIC_AUTH_TOKEN:
            missing.append("ANTHROPIC_AUTH_TOKEN")

    if missing:
        from rich.console import Console
        console = Console()
        console.print("\n[bold red]⚠ 配置缺失：[/bold red]")
        for m in missing:
            console.print(f"  [red]• {m}[/red]")
        console.print(f"\n[dim]请编辑 {ENV_PATH} 填写以上配置[/dim]\n")
        sys.exit(1)
