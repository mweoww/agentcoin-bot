"""
账号文件解析器
- data/accounts.txt      : X 凭证（注册用，5字段）
  格式: agentname----apikey----token----ct0----钱包地址(原始,不用)
- data/registered.txt    : 注册成功的账号（挖矿用，4字段）
  格式: auth_token----钱包地址----私钥----x_handle
"""

import threading
from dataclasses import dataclass, field
from pathlib import Path
from rich.console import Console

console = Console()

DATA_DIR = Path(__file__).parent / "data"
ACCOUNTS_FILE = DATA_DIR / "accounts.txt"
REGISTERED_FILE = DATA_DIR / "registered.txt"

_write_lock = threading.Lock()


@dataclass
class AccountInfo:
    """X 凭证账号（accounts.txt）"""
    index: int              # 序号
    agent_name: str         # X 用户名
    api_key: str            # API Key（保留）
    auth_token: str         # X Cookie auth_token
    ct0: str                # X Cookie ct0
    wallet_address: str     # 钱包地址（原始，不用）

    @property
    def x_handle(self) -> str:
        return self.agent_name

    def short_str(self) -> str:
        return f"[{self.index}] {self.agent_name} ({self.wallet_address[:8]}...)"


@dataclass
class RegisteredAccount:
    """注册成功的账号（registered.txt）"""
    index: int              # 序号
    auth_token: str         # X Cookie auth_token
    wallet_address: str     # 新生成的钱包地址
    private_key: str        # 钱包私钥
    x_handle: str = ""      # X 用户名

    def short_str(self) -> str:
        return f"[{self.index}] {self.wallet_address[:10]}..."

    def to_line(self) -> str:
        """序列化为 registered.txt 格式（4字段）"""
        return f"{self.auth_token}----{self.wallet_address}----{self.private_key}----{self.x_handle}"


# ═══════════════════════════════════════════
# 读取方法
# ═══════════════════════════════════════════

def _parse_accounts_file(file_path: Path) -> list[AccountInfo]:
    """解析 accounts.txt（5字段，无私钥）"""
    if not file_path.exists():
        return []
    accounts = []
    for line in file_path.read_text().strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("----")
        if len(parts) < 5:
            continue
        accounts.append(AccountInfo(
            index=len(accounts),
            agent_name=parts[0].strip(),
            api_key=parts[1].strip(),
            auth_token=parts[2].strip(),
            ct0=parts[3].strip(),
            wallet_address=parts[4].strip(),
        ))
    return accounts


def _parse_registered_file(file_path: Path) -> list[RegisteredAccount]:
    """解析 registered.txt（4字段: token----地址----私钥----x_handle，兼容旧3字段）"""
    if not file_path.exists():
        return []
    accounts = []
    for line in file_path.read_text().strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("----")
        if len(parts) < 3:
            continue
        accounts.append(RegisteredAccount(
            index=len(accounts),
            auth_token=parts[0].strip(),
            wallet_address=parts[1].strip(),
            private_key=parts[2].strip(),
            x_handle=parts[3].strip() if len(parts) >= 4 else "",
        ))
    return accounts


def load_accounts(file_path: Path = None) -> list[AccountInfo]:
    """加载全部 X 凭证账号（注册用）"""
    return _parse_accounts_file(file_path or ACCOUNTS_FILE)


def load_registered() -> list[RegisteredAccount]:
    """加载注册成功的账号（挖矿用，含私钥）"""
    return _parse_registered_file(REGISTERED_FILE)


def load_account_range(start: int = 0, count: int = None) -> list[AccountInfo]:
    """加载指定范围的全部账号"""
    all_accounts = load_accounts()
    if count is None:
        return all_accounts[start:]
    return all_accounts[start:start + count]


def load_registered_range(start: int = 0, count: int = None) -> list[RegisteredAccount]:
    """加载指定范围的已注册账号"""
    all_accounts = load_registered()
    if count is None:
        return all_accounts[start:]
    return all_accounts[start:start + count]


# ═══════════════════════════════════════════
# 写入方法
# ═══════════════════════════════════════════

def save_registered_account(auth_token: str, wallet_address: str, private_key: str, x_handle: str = ""):
    """
    将注册成功的账号追加写入 data/registered.txt
    格式: token----地址----私钥----x_handle
    线程安全
    """
    with _write_lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        existing = _get_registered_wallets()
        if wallet_address in existing:
            return
        with open(REGISTERED_FILE, "a", encoding="utf-8") as f:
            f.write(f"{auth_token}----{wallet_address}----{private_key}----{x_handle}\n")


def is_registered_by_token(auth_token: str) -> bool:
    """通过 auth_token 检查是否已注册"""
    return auth_token in _get_registered_tokens()


def _get_registered_wallets() -> set[str]:
    """获取所有已注册钱包地址集合"""
    if not REGISTERED_FILE.exists():
        return set()
    wallets = set()
    for line in REGISTERED_FILE.read_text().strip().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            parts = line.split("----")
            if len(parts) >= 2:
                wallets.add(parts[1].strip())
    return wallets


def _get_registered_tokens() -> set[str]:
    """获取所有已注册 auth_token 集合"""
    if not REGISTERED_FILE.exists():
        return set()
    tokens = set()
    for line in REGISTERED_FILE.read_text().strip().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            parts = line.split("----")
            if parts:
                tokens.add(parts[0].strip())
    return tokens


def lookup_x_handle_by_token(auth_token: str) -> str:
    """通过 auth_token 从 accounts.txt 反查 x_handle（agent_name）"""
    for acc in _parse_accounts_file(ACCOUNTS_FILE):
        if acc.auth_token == auth_token:
            return acc.agent_name
    return ""


def get_stats() -> dict:
    """获取账号统计"""
    total = len(load_accounts())
    registered = len(load_registered())
    return {
        "total": total,
        "registered": registered,
        "pending": total - registered,
    }
