"""
AgentCoin 合约地址、ABI 定义和 web3 连接工具
所有链上交互的基础层
"""

import requests as _req
from web3 import Web3
from web3.providers.rpc import HTTPProvider

import config

# ═══════════════════════════════════════════
# 合约地址 (Base Mainnet)
# ═══════════════════════════════════════════

ADDRESSES = {
    "AGCToken":          "0x48778537634Fa47Ff9CDBFdcEd92F3B9DB50bd97",
    "AgentRegistry":     "0x5A899d52C9450a06808182FdB1D1e4e23AdFe04D",
    "ProblemManager":    "0x7D563ae2881D2fC72f5f4c66334c079B4Cc051c6",
    "RewardDistributor": "0xD85aCAC804c074d3c57A422d26bAfAF04Ed6b899",
}

# ═══════════════════════════════════════════
# ABI 定义（仅包含需要调用的函数）
# ═══════════════════════════════════════════

AGENT_REGISTRY_ABI = [
    {
        "name": "registerAgent",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "xAccountHash", "type": "bytes32"}],
        "outputs": [{"name": "agentId", "type": "uint256"}],
    },
    {
        "name": "getAgent",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "outputs": [
            {"name": "wallet", "type": "address"},
            {"name": "xAccountHash", "type": "bytes32"},
            {"name": "streak", "type": "uint256"},
            {"name": "correctCount", "type": "uint256"},
            {"name": "active", "type": "bool"},
            {"name": "registered", "type": "bool"},
        ],
    },
    {
        "name": "getAgentId",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "wallet", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

PROBLEM_MANAGER_ABI = [
    {
        "name": "submitAnswer",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "problemId", "type": "uint256"},
            {"name": "answer", "type": "bytes32"},
        ],
        "outputs": [],
    },
    {
        "name": "getProblem",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "problemId", "type": "uint256"}],
        "outputs": [
            {"name": "answerHash", "type": "bytes32"},
            {"name": "answerDeadline", "type": "uint256"},
            {"name": "revealDeadline", "type": "uint256"},
            {"name": "status", "type": "uint8"},
            {"name": "correctCount", "type": "uint256"},
            {"name": "totalCorrectWeight", "type": "uint256"},
            {"name": "winnerCount", "type": "uint256"},
            {"name": "verifiedWinnerCount", "type": "uint256"},
        ],
    },
    {
        "name": "currentProblemId",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "getAgentAnswerHash",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "problemId", "type": "uint256"},
            {"name": "agentId", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
]

AGC_TOKEN_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "totalSupply",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

REWARD_DISTRIBUTOR_ABI = [
    {
        "name": "claimRewards",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [],
        "outputs": [],
    },
    {
        "name": "pendingRewards",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "outputs": [
            {"name": "totalPending", "type": "uint256"},
            {"name": "minerReward", "type": "uint256"},
            {"name": "verifierReward", "type": "uint256"},
            {"name": "streakBonus", "type": "uint256"},
            {"name": "lastClaimedProblem", "type": "uint256"},
            {"name": "claimable", "type": "bool"},
        ],
    },
    {
        "name": "emissionStartTime",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]


# ═══════════════════════════════════════════
# Web3 连接（带代理支持 + 多 RPC 备用）
# ═══════════════════════════════════════════

FALLBACK_RPCS = [
    "https://mainnet.base.org",
    "https://base.llamarpc.com",
    "https://base-rpc.publicnode.com",
    "https://1rpc.io/base",
    "https://base.drpc.org",
]


def _build_w3(rpc_url: str, use_proxy: bool = False) -> Web3:
    """
    用指定 RPC 构建 Web3 实例
    use_proxy: 是否走代理（Alchemy 等专用 RPC 建议直连）
    """
    if use_proxy:
        proxy_url = config.get_proxy_url()
        if proxy_url:
            session = _req.Session()
            session.proxies = config.get_proxies()
            provider = HTTPProvider(
                rpc_url,
                session=session,
                request_kwargs={"timeout": 20},
            )
            return Web3(provider)

    provider = HTTPProvider(
        rpc_url,
        request_kwargs={"timeout": 20},
    )
    return Web3(provider)


def get_w3() -> Web3:
    """
    获取 Web3 实例，自动尝试多个 RPC 节点
    主 RPC (Alchemy 等) 直连不走代理；备用公共 RPC 走代理
    """
    primary = config.BASE_RPC_URL
    rpc_list = [primary] + [r for r in FALLBACK_RPCS if r != primary]

    for rpc_url in rpc_list:
        try:
            # 主 RPC 直连（Alchemy/Infura 等专用节点不需要代理）
            # 备用公共 RPC 走代理
            use_proxy = (rpc_url != primary)
            w3 = _build_w3(rpc_url, use_proxy=use_proxy)
            if w3.is_connected():
                if rpc_url != primary:
                    print(f"  [RPC 备用] 已切换到 {rpc_url}")
                return w3
        except Exception:
            continue

    # 全部失败，返回主 RPC 的实例（让调用方处理错误）
    return _build_w3(primary)


def get_contracts(w3: Web3) -> dict:
    """获取所有合约实例"""
    return {
        "registry": w3.eth.contract(
            address=Web3.to_checksum_address(ADDRESSES["AgentRegistry"]),
            abi=AGENT_REGISTRY_ABI,
        ),
        "problem": w3.eth.contract(
            address=Web3.to_checksum_address(ADDRESSES["ProblemManager"]),
            abi=PROBLEM_MANAGER_ABI,
        ),
        "token": w3.eth.contract(
            address=Web3.to_checksum_address(ADDRESSES["AGCToken"]),
            abi=AGC_TOKEN_ABI,
        ),
        "reward": w3.eth.contract(
            address=Web3.to_checksum_address(ADDRESSES["RewardDistributor"]),
            abi=REWARD_DISTRIBUTOR_ABI,
        ),
    }


def send_tx(w3: Web3, account, tx_func, priority: str = "normal", **kwargs):
    """
    构建、签名并发送交易（EIP-1559 优先，兼容 legacy）
    tx_func: 合约函数调用（如 contract.functions.registerAgent(hash)）
    priority: "normal" | "fast" | "urgent" - 影响 priority fee
    返回: tx_receipt
    """
    nonce = w3.eth.get_transaction_count(account.address, "pending")

    # EIP-1559 gas 参数
    try:
        base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
        priority_map = {"normal": 100_000_000, "fast": 500_000_000, "urgent": 1_500_000_000}
        max_priority = priority_map.get(priority, 100_000_000)
        max_fee = base_fee * 2 + max_priority

        tx = tx_func.build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": 300_000,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": max_priority,
            "chainId": 8453,
            "type": 2,
            **kwargs,
        })
    except Exception:
        tx = tx_func.build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": 300_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": 8453,
            **kwargs,
        })

    # 估算精确 gas（使用简化参数避免 gas=0 问题）
    try:
        estimated = w3.eth.estimate_gas({
            "from": account.address,
            "to": tx["to"],
            "data": tx["data"],
            "value": tx.get("value", 0),
        })
        tx["gas"] = int(estimated * 1.2)
    except Exception:
        tx["gas"] = 300_000

    signed = w3.eth.account.sign_transaction(tx, account.key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    return receipt
