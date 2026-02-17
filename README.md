# AgentCoin 全自动挖矿机器人

自动参与 [AgentCoin](https://agentcoin.site/) 挖矿：注册 Agent → AI 解题 → 提交答案 → 领取奖励。

## 功能特性

- **全自动注册**: 钱包生成 + X 账号绑定 + 链上注册一键完成
- **AI 解题**: Claude 4.6 自动求解个性化数学题
- **反检测发推**: TLS 指纹伪装 + 行为模拟 + 双通道降级
- **全局代理**: 所有请求走带认证的 HTTP 代理
- **可视化面板**: Rich 实时终端面板（状态/收益/日志）
- **自动领奖**: 定期检查并领取 AGC 代币
- **Docker 部署**: 一键启动，自动重启

## 快速开始

### 1. 配置

```bash
cd agentcoin-bot
cp .env.example .env
# 编辑 .env 填写你的配置
```

必填配置：

| 配置项 | 说明 |
|--------|------|
| `X_HANDLE` | X 用户名（不带@） |
| `X_API_KEY` / `X_AUTH_TOKEN` | X 发推凭证（API 或 Cookie 至少填一组） |
| `ANTHROPIC_AUTH_TOKEN` | Claude AI 密钥 |
| `PROXY_HOST` + `PROXY_AUTH` | 代理地址和认证 |

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 注册 Agent

```bash
python register.py
```

完成后会生成 `data/.state.json` 保存注册状态。

### 4. 开始挖矿

```bash
python mine.py
```

## Docker 部署

```bash
# 注册
docker compose --profile register run agc-register

# 挖矿（后台持续运行）
docker compose up -d agc-miner

# 查看日志
docker compose logs -f agc-miner
```

## 项目结构

```
├── register.py      # [入口1] 注册模块
├── mine.py          # [入口2] 挖矿模块
├── config.py        # 配置管理 + 代理工厂
├── contracts.py     # 合约 ABI + web3 工具
├── wallet.py        # 钱包管理
├── x_client.py      # X 隐身客户端（反检测）
├── x_binding.py     # X 账号绑定
├── registry.py      # 链上注册
├── solver.py        # Claude 4.6 求解引擎
├── miner.py         # 挖矿核心循环
├── dashboard.py     # Rich 可视化面板
└── data/.state.json # 运行时状态（自动生成）
```

## 安全提醒

- 私钥仅存储在本地 `.env` 文件中，已加入 `.gitignore`
- 请勿将 `.env` 提交到版本控制
- Base 链 gas 费极低，0.001 ETH 即可支撑大量交易
