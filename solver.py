"""
AI 求解引擎 - Claude 4.6 via Anthropic 兼容接口
策略优先级：
  1. 本地求解器（毫秒级，匹配已知题型直接计算）
  2. AI 生成代码 → 本地执行 → 获取精确数值答案
  3. AI 纯推理（备用）
"""

import re
import subprocess
import tempfile
import time
import traceback

import anthropic
import httpx
from web3 import Web3

import config
from local_solver import solve_locally

# 代码生成提示词
CODE_SYSTEM_PROMPT = """You are a competitive programming expert. Given a math/algorithm problem, write a self-contained Python script that computes and prints ONLY the final numeric answer.

CRITICAL RULES:
1. Write a COMPLETE Python script inside a single ```python code block
2. The script must print EXACTLY ONE number as its only output (use print())
3. Do NOT print anything else - no labels, no explanations, no extra text
4. The script must be self-contained (no external libraries beyond Python stdlib)
5. Handle edge cases properly
6. The script must terminate within 10 seconds
7. If the answer is an integer, print it as an integer (no decimal point)

Example response:
```python
# solve the problem
result = 42
print(result)
```"""

# 纯推理备用提示词（简单题目用）
REASONING_SYSTEM_PROMPT = """You are a precise math calculator. Solve the given problem step by step, then output ONLY the final numeric answer on the LAST line.

CRITICAL RULES:
- Show your work/reasoning first
- The VERY LAST line of your response must contain ONLY the final numeric answer
- No text, no explanation, no units on the last line - just the number
- If the answer is an integer, do not add a decimal point

Example output format:
[your reasoning here]
42"""


class Solver:
    """Claude 4.6 AI 求解引擎（本地求解 → 代码执行 → 纯推理）"""

    def __init__(self):
        proxy_url = config.get_proxy_url()
        http_client = httpx.Client(proxy=proxy_url, timeout=90) if proxy_url else None

        self.client = anthropic.Anthropic(
            base_url=config.ANTHROPIC_BASE_URL,
            api_key=config.ANTHROPIC_AUTH_TOKEN,
            http_client=http_client,
        )
        self.model = "claude-sonnet-4-20250514"
        self.max_retries = 3

    def solve(self, problem_text: str, agent_id: int) -> dict:
        """
        求解个性化题目
        策略0: 本地求解器（毫秒级，匹配已知题型）
        策略1: AI 生成代码 → 本地执行 → 获取答案
        策略2: 代码执行失败 → AI 纯推理
        """
        personalized = problem_text.replace("{AGENT_ID}", str(agent_id))

        # 策略0: 本地求解器（最快）
        try:
            local_answer = solve_locally(problem_text, agent_id)
            if local_answer is not None:
                answer_bytes = self._int_to_bytes32(local_answer)
                return {
                    "success": True,
                    "answer": str(local_answer),
                    "answer_hash": answer_bytes,
                    "raw_response": f"本地求解: {local_answer}",
                    "method": "local",
                }
        except Exception:
            pass

        # 策略1: 代码执行（优先）
        code_result = self._solve_with_code(personalized)
        if code_result and code_result["success"]:
            return code_result

        # 策略2: 纯推理（备用）
        reasoning_result = self._solve_with_reasoning(personalized)
        if reasoning_result and reasoning_result["success"]:
            return reasoning_result

        error = "三种策略均失败"
        if code_result:
            error += f" | 代码: {code_result.get('error', '?')}"
        if reasoning_result:
            error += f" | 推理: {reasoning_result.get('error', '?')}"
        return {"success": False, "error": error}

    @staticmethod
    def _int_to_bytes32(value: int) -> bytes:
        """将整数转为 bytes32 格式（合约 submitAnswer 需要的格式）"""
        if value < 0:
            value = value & ((1 << 256) - 1)
        return value.to_bytes(32, byteorder='big')

    def _solve_with_code(self, problem_text: str) -> dict | None:
        """策略1: AI 写代码 → 本地执行"""
        for attempt in range(1, self.max_retries + 1):
            try:
                raw_response = self._call_ai(problem_text, mode="code")
                code = self._extract_code(raw_response)
                if not code:
                    continue

                output = self._execute_code(code)
                if output is None:
                    continue

                numeric = self._extract_number(output)
                if numeric is not None:
                    answer_bytes = self._int_to_bytes32(int(numeric))
                    return {
                        "success": True,
                        "answer": numeric,
                        "answer_hash": answer_bytes,
                        "raw_response": raw_response,
                        "method": "code",
                    }
            except Exception as e:
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)

        return {"success": False, "error": "代码执行未产生有效数值"}

    def _solve_with_reasoning(self, problem_text: str) -> dict | None:
        """策略2: AI 纯推理"""
        for attempt in range(1, self.max_retries + 1):
            try:
                raw_response = self._call_ai(problem_text, mode="reasoning")
                numeric = self._extract_number(raw_response)
                if numeric is not None:
                    answer_bytes = self._int_to_bytes32(int(numeric))
                    return {
                        "success": True,
                        "answer": numeric,
                        "answer_hash": answer_bytes,
                        "raw_response": raw_response,
                        "method": "reasoning",
                    }
            except Exception as e:
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)

        return {"success": False, "error": "推理未产生有效数值"}

    def _call_ai(self, problem_text: str, mode: str = "code") -> str:
        """调用 Claude API"""
        if mode == "code":
            system = CODE_SYSTEM_PROMPT
            user_msg = (
                f"Write a Python script to solve this problem. "
                f"The script must print ONLY the final numeric answer.\n\n{problem_text}"
            )
        else:
            system = REASONING_SYSTEM_PROMPT
            user_msg = (
                f"Solve this problem. Show your work, then put ONLY the final number "
                f"on the last line:\n\n{problem_text}"
            )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text.strip()

    def _extract_code(self, text: str) -> str | None:
        """从 AI 响应中提取 Python 代码块"""
        # 匹配 ```python ... ``` 代码块
        match = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # 匹配 ``` ... ``` 通用代码块
        match = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
        if match:
            code = match.group(1).strip()
            if "print" in code:
                return code

        return None

    def _execute_code(self, code: str) -> str | None:
        """在沙盒中执行 Python 代码，返回 stdout"""
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                f.flush()
                tmp_path = f.name

            result = subprocess.run(
                ["python3", tmp_path],
                capture_output=True,
                text=True,
                timeout=15,
            )

            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()

            return None
        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None

    def _extract_number(self, text: str) -> str | None:
        """从文本中提取最终数值答案"""
        text = text.strip()

        # 移除 markdown 代码块
        text = re.sub(r"```[\s\S]*?```", "", text)
        text = re.sub(r"`([^`]+)`", r"\1", text)

        lines = text.strip().splitlines()

        # 策略1: 从最后一行开始往上找纯数字行
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            line = re.sub(r"\*\*", "", line).strip()
            match = re.match(r"^-?\d[\d,]*\.?\d*$", line)
            if match:
                num_str = match.group(0).replace(",", "")
                return self._normalize_number(num_str)

        # 策略2: 从最后一行提取包含的数字
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            match = re.search(r"(-?\d[\d,]*\.?\d*)", line)
            if match:
                num_str = match.group(1).replace(",", "")
                return self._normalize_number(num_str)

        return None

    def _normalize_number(self, num_str: str) -> str | None:
        """标准化数字字符串"""
        try:
            val = float(num_str)
            if val == int(val):
                return str(int(val))
            return num_str
        except ValueError:
            return None

    def test_connection(self) -> bool:
        """测试 AI 连接"""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=16,
                messages=[{"role": "user", "content": "1+1=?只回答数字"}],
            )
            result = response.content[0].text.strip()
            return "2" in result
        except Exception:
            return False
