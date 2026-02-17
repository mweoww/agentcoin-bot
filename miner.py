"""
AgentCoin 挖矿核心循环
单调度线程轮询题目 → 发现新题目 → 所有矿工并发解题提交

优化要点：
- 单线程轮询题目，避免 N 个账号重复查询
- 链上直接查询题目状态（不依赖 API 的 is_active）
- 链上检查是否已提交（API 已禁用 503）
- EIP-1559 交易加速
- pendingRewards 使用 agentId 而非 address
"""

import time
import traceback
from datetime import datetime
from typing import Callable

import requests
from eth_account import Account
from web3 import Web3

import config
import contracts
from solver import Solver

# 题目状态枚举（链上 ProblemStatus - 实际验证值）
PS_ANSWER = 0           # 答题阶段（status=0 表示活跃答题中）
PS_CLOSED_ANSWER = 1    # 答题已关闭
PS_LOTTERY_READY = 2    # 等待抽奖
PS_VERIFICATION = 3     # 验证阶段
PS_SETTLED = 4          # 已结算
PS_CLOSED = 5           # 已关闭


# ═══════════════════════════════════════════
# 公共题目轮询（全局共享，只查一次）
# ═══════════════════════════════════════════

class ProblemPoller:
    """单例题目轮询器，所有矿工共享"""

    def __init__(self, w3: Web3):
        self.w3 = w3
        self.contracts = contracts.get_contracts(w3)
        self._last_problem_id: int | None = None
        self._last_deadline: int = 0

    def poll(self) -> dict | None:
        """
        查询当前题目（链上 + API），返回题目信息 dict 或 None
        全局只调一次，结果分发给所有矿工
        """
        problem_id = None
        deadline = 0
        status = 0
        template_text = None

        # 链上查询（最可靠）
        try:
            problem_id = self.contracts["problem"].functions.currentProblemId().call()
            if problem_id > 0:
                info = self.contracts["problem"].functions.getProblem(problem_id).call()
                deadline = info[1]
                status = info[3]
        except Exception:
            pass

        # API 获取 template_text
        try:
            resp = requests.get(
                f"{config.AGC_API_BASE}/api/problem/current",
                proxies=config.get_proxies(),
                timeout=10,
            )
            if resp.status_code == 200:
                api_data = resp.json()
                template_text = api_data.get("template_text")
                if not problem_id or problem_id == 0:
                    problem_id = api_data.get("problem_id", 0)
                    deadline = api_data.get("answer_deadline", 0)
        except Exception:
            pass

        if not problem_id or problem_id == 0:
            return None

        now = int(time.time())
        is_active = (status == PS_ANSWER) and (deadline > 0) and (now < deadline)
        is_new = (self._last_problem_id is not None and problem_id != self._last_problem_id)

        self._last_problem_id = problem_id
        self._last_deadline = deadline

        return {
            "problem_id": problem_id,
            "answer_deadline": deadline,
            "status": status,
            "is_active": is_active,
            "is_new": is_new,
            "template_text": template_text,
        }

    def get_smart_interval(self, all_submitted: bool) -> int:
        """
        智能轮询间隔（调度层使用）
        all_submitted: 所有矿工是否都已提交当前题目
        """
        now = int(time.time())
        if not all_submitted and self._last_deadline > now:
            return 3
        if self._last_deadline > 0:
            time_to_next = (self._last_deadline + 10) - now
            if time_to_next > 60:
                return min(30, max(10, time_to_next - 50))
            return 5
        return 10

    def get_problem_template(self, problem_id: int) -> str | None:
        """获取题目模板文本（API）"""
        try:
            resp = requests.get(
                f"{config.AGC_API_BASE}/api/problem/{problem_id}/template",
                proxies=config.get_proxies(),
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("template_text")
        except Exception:
            pass
        return None


# ═══════════════════════════════════════════
# 单账号矿工（只负责解题+提交）
# ═══════════════════════════════════════════

class Miner:
    """单账号矿工：接收题目 → 解题 → 提交"""

    REVERT_SELECTORS = {
        "0x81d820a8": "AlreadySubmitted",
        "0xec2b7666": "AnswerPeriodEnded",
        "0x2d0a3f8e": "ProblemNotActive",
        "0x584a7938": "AgentNotRegistered",
    }

    EMPTY_ANSWER = b"\x00" * 32

    def __init__(self, w3: Web3, account: Account, agent_id: int, log_fn: Callable = None):
        self.w3 = w3
        self.account = account
        self.agent_id = agent_id
        self.solver = Solver()
        self.contracts = contracts.get_contracts(w3)
        self.log = log_fn or self._default_log

        self._submitted_problems: set[int] = set()
        self.gas_exhausted = False

        self.stats = {
            "start_time": datetime.now(),
            "problems_solved": 0,
            "problems_submitted": 0,
            "problems_won": 0,
            "total_rewards": 0.0,
            "current_problem_id": None,
            "current_status": "空闲",
            "last_submit_tx": None,
            "streak": 0,
            "correct_count": 0,
            "agc_balance": 0.0,
            "pending_rewards": 0.0,
            "last_error": None,
        }

    def solve_and_submit(self, problem: dict) -> dict:
        """
        接收公共题目信息，执行解题+提交
        返回 {"action": "submitted"/"skip"/"error", "detail": ...}
        """
        try:
            problem_id = problem["problem_id"]
            deadline = problem["answer_deadline"]
            self.stats["current_problem_id"] = problem_id

            # 1. 已提交检查（本地缓存）
            if problem_id in self._submitted_problems:
                remaining = deadline - int(time.time())
                self.stats["current_status"] = f"已提交 #{problem_id} ({remaining}s)"
                return {"action": "skip", "detail": f"#{problem_id} 已提交（缓存）"}

            # 2. 已提交检查（链上）
            self.stats["current_status"] = f"检查提交状态 #{problem_id}"
            if self._has_submitted_onchain(problem_id):
                self._submitted_problems.add(problem_id)
                remaining = deadline - int(time.time())
                self.stats["current_status"] = f"已提交 #{problem_id} ({remaining}s)"
                return {"action": "skip", "detail": f"#{problem_id} 已提交（链上）"}

            # 3. 获取模板
            template_text = problem.get("template_text")
            if not template_text:
                self.stats["current_status"] = "模板获取失败"
                return {"action": "error", "detail": f"#{problem_id} 无模板"}

            remaining = deadline - int(time.time())
            self.log("信息", f"题目 #{problem_id}，剩余 {remaining}s，开始求解...")

            # 4. 求解
            self.stats["current_status"] = f"求解 #{problem_id}..."
            t0 = time.time()
            solve_result = self.solver.solve(template_text, self.agent_id)
            solve_time = time.time() - t0

            if not solve_result["success"]:
                self.log("错误", f"求解失败: {solve_result.get('error', '未知')}")
                return {"action": "error", "detail": solve_result.get("error")}

            answer = solve_result["answer"]
            answer_hash = solve_result["answer_hash"]
            method = solve_result.get("method", "unknown")
            method_labels = {"local": "本地秒解", "code": "AI代码", "reasoning": "AI推理"}
            method_label = method_labels.get(method, method)
            self.log("信息", f"求解完成 [{method_label}] {solve_time:.1f}s，答案: {answer}")
            self.stats["problems_solved"] += 1

            # 5. 检查 deadline
            now = int(time.time())
            if now >= deadline:
                self.log("警告", f"#{problem_id} 已截止，跳过提交")
                self._submitted_problems.add(problem_id)
                return {"action": "skip", "detail": f"#{problem_id} 已截止"}

            # 6. 提交上链
            self.stats["current_status"] = f"提交 #{problem_id}..."
            self.log("信息", f"提交答案（剩余 {deadline - now}s）...")

            submit_result = self._submit_answer(problem_id, answer_hash)

            if submit_result["success"]:
                tx_hash = submit_result["tx_hash"]
                self._submitted_problems.add(problem_id)
                self.stats["problems_submitted"] += 1
                self.stats["last_submit_tx"] = tx_hash
                self.stats["current_status"] = f"已提交 #{problem_id}"
                self.log("提交", f"答案已提交 TX: {tx_hash[:16]}...")
                return {"action": "submitted", "detail": f"#{problem_id} 已提交"}
            else:
                error = submit_result.get("error", "未知")
                revert = submit_result.get("revert_reason", "")

                if revert == "AlreadySubmitted":
                    self._submitted_problems.add(problem_id)
                    self.stats["problems_submitted"] += 1
                    self.stats["current_status"] = f"已提交 #{problem_id}"
                    self.log("警告", f"#{problem_id} 链上已存在提交")
                    return {"action": "skip", "detail": f"#{problem_id} 已提交（链上）"}
                elif revert == "AnswerPeriodEnded":
                    self._submitted_problems.add(problem_id)
                    self.log("警告", f"#{problem_id} 答题已结束")
                    return {"action": "skip", "detail": f"#{problem_id} 已截止"}
                else:
                    error_lower = str(error).lower()
                    if "insufficient funds" in error_lower:
                        self.gas_exhausted = True
                        self.stats["current_status"] = "Gas不足"
                        self.log("错误", f"Gas 不足，已暂停该矿工")
                        return {"action": "gas_exhausted", "detail": "Gas不足"}
                    self.log("错误", f"提交失败: {error}")
                    return {"action": "error", "detail": error}

        except Exception as e:
            self.stats["last_error"] = str(e)
            self.log("错误", f"异常: {e}")
            traceback.print_exc()
            return {"action": "error", "detail": str(e)}

    def has_submitted(self, problem_id: int) -> bool:
        """快速检查是否已提交（仅缓存，不查链）"""
        return problem_id in self._submitted_problems

    # ─── 兼容旧接口（单账号模式仍使用） ───

    def run_once(self) -> dict:
        """单账号模式：自行轮询+解题（保留兼容）"""
        try:
            self.stats["current_status"] = "获取题目中..."
            problem = self._get_current_problem_smart()

            if not problem:
                self.stats["current_status"] = "等待新题目"
                return {"action": "wait", "detail": "获取题目失败"}

            if not problem["is_active"]:
                pid = problem["problem_id"]
                self.stats["current_problem_id"] = pid
                self.stats["current_status"] = "等待新题目"
                return {"action": "wait", "detail": f"#{pid} 不在答题期"}

            return self.solve_and_submit(problem)

        except Exception as e:
            self.stats["last_error"] = str(e)
            self.log("错误", f"挖矿异常: {e}")
            return {"action": "error", "detail": str(e)}

    def get_smart_poll_interval(self) -> int:
        """单账号模式的轮询间隔"""
        now = int(time.time())
        pid = self.stats.get("current_problem_id")
        if pid and pid not in self._submitted_problems:
            return 3
        return 10

    def check_and_claim_rewards(self):
        """检查并领取奖励"""
        if not config.AUTO_CLAIM:
            return
        try:
            result = self.contracts["reward"].functions.pendingRewards(
                self.agent_id
            ).call()
            total_pending = result[0] if isinstance(result, (list, tuple)) else result
            pending_agc = float(self.w3.from_wei(total_pending, "ether"))
            self.stats["pending_rewards"] = pending_agc

            if total_pending > 0:
                self.log("奖励", f"待领取: {pending_agc:.4f} AGC，正在领取...")
                try:
                    tx_func = self.contracts["reward"].functions.claimRewards()
                    receipt = contracts.send_tx(self.w3, self.account, tx_func)
                    if receipt["status"] == 1:
                        self.stats["total_rewards"] += pending_agc
                        self.stats["pending_rewards"] = 0.0
                        self.log("奖励", f"成功领取 {pending_agc:.4f} AGC！")
                    else:
                        self.log("错误", "领取奖励交易失败")
                except Exception as e:
                    self.log("错误", f"领取奖励失败: {e}")
        except Exception as e:
            err_str = str(e)
            if "revert" not in err_str.lower():
                self.log("警告", f"查询奖励失败: {err_str[:60]}")

    def update_chain_stats(self):
        """更新链上统计数据"""
        try:
            balance = self.contracts["token"].functions.balanceOf(
                self.account.address
            ).call()
            self.stats["agc_balance"] = float(self.w3.from_wei(balance, "ether"))
        except Exception:
            pass
        try:
            agent_info = self.contracts["registry"].functions.getAgent(
                self.agent_id
            ).call()
            streak_raw = agent_info[2]
            if streak_raw > 1_000_000:
                self.stats["streak"] = float(self.w3.from_wei(streak_raw, "ether"))
            else:
                self.stats["streak"] = streak_raw
            self.stats["correct_count"] = agent_info[3]
        except Exception:
            pass

    # ─── 内部方法 ───

    def _get_current_problem_smart(self) -> dict | None:
        """单账号模式自用的题目查询"""
        problem_id = None
        deadline = 0
        status = 0
        template_text = None

        try:
            problem_id = self.contracts["problem"].functions.currentProblemId().call()
            if problem_id > 0:
                info = self.contracts["problem"].functions.getProblem(problem_id).call()
                deadline = info[1]
                status = info[3]
        except Exception:
            pass

        try:
            resp = requests.get(
                f"{config.AGC_API_BASE}/api/problem/current",
                proxies=config.get_proxies(),
                timeout=10,
            )
            if resp.status_code == 200:
                api_data = resp.json()
                template_text = api_data.get("template_text")
                if not problem_id or problem_id == 0:
                    problem_id = api_data.get("problem_id", 0)
                    deadline = api_data.get("answer_deadline", 0)
        except Exception:
            pass

        if not problem_id or problem_id == 0:
            return None

        now = int(time.time())
        is_active = (status == PS_ANSWER) and (deadline > 0) and (now < deadline)

        return {
            "problem_id": problem_id,
            "answer_deadline": deadline,
            "status": status,
            "is_active": is_active,
            "template_text": template_text,
        }

    def _has_submitted_onchain(self, problem_id: int) -> bool:
        """链上检查是否已提交"""
        try:
            answer_hash = self.contracts["problem"].functions.getAgentAnswerHash(
                problem_id, self.agent_id
            ).call()
            return answer_hash != self.EMPTY_ANSWER
        except Exception:
            return False

    def _submit_answer(self, problem_id: int, answer_hash: bytes) -> dict:
        """提交答案上链"""
        try:
            tx_func = self.contracts["problem"].functions.submitAnswer(
                problem_id, answer_hash
            )
            receipt = contracts.send_tx(self.w3, self.account, tx_func, priority="normal")

            if receipt["status"] == 1:
                return {"success": True, "tx_hash": receipt["transactionHash"].hex()}
            else:
                return {"success": False, "error": "交易失败 (status=0)"}
        except Exception as e:
            error_str = str(e)
            revert_reason = self._parse_revert(error_str)
            if revert_reason:
                return {"success": False, "error": revert_reason, "revert_reason": revert_reason}
            return {"success": False, "error": error_str}

    def _parse_revert(self, error_str: str) -> str | None:
        for selector, name in self.REVERT_SELECTORS.items():
            if selector[2:] in error_str:
                return name
        return None

    def _default_log(self, level: str, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"  {ts} [{level}] {message}")
