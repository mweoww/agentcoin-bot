"""
本地求解器 - 根据题目模板类型匹配预写的 Python 求解函数
跳过 AI 调用，毫秒级出答案

支持的题型：
1. 整除求和 (divisible by 3 or 5, not 15)
2. 整除求和 + 取模
3. 数字根 + 幂运算
4. 数位和匹配 (digit sum matching)
5. 序列递推 (Fibonacci-like, custom recurrence)
6. 图论最短路 (directed graph shortest path)
7. 整数分区计数 (partition with constraints)
8. 数位累加序列 (digit sum accumulation)
9. 周期检测 (cycle detection in sequence)
"""

import re
import heapq
from typing import Optional


def solve_locally(template_text: str, agent_id: int) -> Optional[int]:
    """
    尝试本地求解题目
    返回整数答案，无法识别题型返回 None
    """
    text = template_text.replace("{AGENT_ID}", str(agent_id))

    # 按优先级尝试各个求解器
    solvers = [
        _solve_div35_digital_root_power,
        _solve_div35_modulo,
        _solve_div35_simple,
        _solve_digit_sum_equal_pair,
        _solve_digit_sum_equal_self,
        _solve_digit_sum_target,
        _solve_harshad_modular_sum,
        _solve_lattice_points,
        _solve_sequence_square_mod_sum,
        _solve_fibonacci_like_mod,
        _solve_custom_sequence_sum,
        _solve_custom_sequence_cycle,
        _solve_graph_shortest_path,
        _solve_partition_count,
        _solve_digit_accum_sequence,
        _solve_sequence_congruence,
    ]

    for solver in solvers:
        try:
            result = solver(text, agent_id)
            if result is not None:
                return result
        except Exception:
            continue

    return None


# ═══════════════════════════════════════════
# 题型 1: 整除求和 (3 or 5, not 15) - 简单版
# ═══════════════════════════════════════════

def _solve_div35_simple(text: str, agent_id: int) -> Optional[int]:
    """
    Let N = {AGENT_ID}. Compute the sum of all positive integers k ≤ N
    such that k is divisible by 3 or 5, but NOT divisible by 15.
    Provide the final integer result.
    """
    if "divisible by 3 or 5" not in text:
        return None
    if "not" not in text.lower() or "15" not in text:
        return None
    # 不能有 modulo / digital root 等后续操作
    if "modulo" in text.lower() or "mod" in text.lower().split("15")[-1]:
        return None
    if "digital root" in text.lower():
        return None

    # 提取 N
    n = _extract_n(text, agent_id)
    if n is None:
        return None

    return _sum_div35_not15(n)


def _sum_div35_not15(n: int) -> int:
    """计算 1..N 中能被 3 或 5 整除但不能被 15 整除的数之和"""
    total = 0
    for k in range(1, n + 1):
        if (k % 3 == 0 or k % 5 == 0) and k % 15 != 0:
            total += k
    return total


# ═══════════════════════════════════════════
# 题型 2: 整除求和 + 取模
# ═══════════════════════════════════════════

def _solve_div35_modulo(text: str, agent_id: int) -> Optional[int]:
    """
    ... sum of k divisible by 3 or 5, not 15 ...
    Then, take the result modulo (N mod 100 + 1).
    """
    if "divisible by 3 or 5" not in text:
        return None
    if "15" not in text:
        return None

    # 检查是否有 modulo 操作
    mod_match = re.search(
        r"(?:modulo|mod)\s*\(\s*N\s*mod\s*(\d+)\s*\+\s*(\d+)\s*\)",
        text, re.IGNORECASE
    )
    if not mod_match:
        # 也尝试匹配 "result modulo (N mod 100 + 1)"
        mod_match = re.search(
            r"result\s+modulo\s*\(\s*N\s+mod\s+(\d+)\s*\+\s*(\d+)\s*\)",
            text, re.IGNORECASE
        )
    if not mod_match:
        return None

    if "digital root" in text.lower():
        return None

    n = _extract_n(text, agent_id)
    if n is None:
        return None

    mod_base = int(mod_match.group(1))
    mod_add = int(mod_match.group(2))
    modulus = (n % mod_base) + mod_add

    s = _sum_div35_not15(n)
    return s % modulus


# ═══════════════════════════════════════════
# 题型 3: 整除求和 + 数字根 + 幂运算
# ═══════════════════════════════════════════

def _solve_div35_digital_root_power(text: str, agent_id: int) -> Optional[int]:
    """
    ... sum of k divisible by 3 or 5, not 15 ...
    compute its digital root, raise 2 to the power of that digital root.
    """
    if "divisible by 3 or 5" not in text:
        return None
    if "digital root" not in text.lower():
        return None

    # 提取 N
    n_match = re.search(r"N\s*=\s*\(?\s*AGENT_ID\s*mod\s*(\d+)\s*\)?\s*\+\s*(\d+)", text)
    if n_match:
        n = (agent_id % int(n_match.group(1))) + int(n_match.group(2))
    else:
        n = _extract_n(text, agent_id)
    if n is None:
        return None

    s = _sum_div35_not15(n)
    dr = _digital_root(s)

    # 检查是否要 raise 2 to the power
    if re.search(r"raise\s+2\s+to\s+the\s+power", text, re.IGNORECASE) or \
       re.search(r"2\s*\^\s*(?:that|the)\s*digital\s*root", text, re.IGNORECASE):
        return 2 ** dr

    return dr


def _digital_root(n: int) -> int:
    """计算数字根（反复求数位和直到一位数）"""
    while n >= 10:
        n = sum(int(d) for d in str(n))
    return n


# ═══════════════════════════════════════════
# 题型 4: 数位和匹配 - digit_sum(N * A) == digit_sum(N * (A+1))
# ═══════════════════════════════════════════

def _solve_digit_sum_equal_pair(text: str, agent_id: int) -> Optional[int]:
    """
    Compute the smallest positive integer N such that
    the sum of the digits of (N * AGENT_ID) equals
    the sum of the digits of (N * (AGENT_ID + 1)).
    """
    if "smallest positive integer" not in text.lower():
        return None

    # 匹配 digit_sum(N * A) == digit_sum(N * (A+1))
    pattern = re.search(
        r"sum\s+of\s+(?:the\s+)?digits\s+of\s+\(?\s*N\s*\*\s*(?:\{?AGENT_ID\}?|" + str(agent_id) + r")\s*\)?"
        r".*?equals.*?"
        r"sum\s+of\s+(?:the\s+)?digits\s+of\s+\(?\s*N\s*\*\s*\(?\s*(?:\{?AGENT_ID\}?|" + str(agent_id) + r")\s*\+\s*1\s*\)?\s*\)?",
        text, re.IGNORECASE | re.DOTALL
    )
    if not pattern:
        return None

    a = agent_id
    for n in range(1, 1000000):
        if _digit_sum(n * a) == _digit_sum(n * (a + 1)):
            return n
    return 0


# ═══════════════════════════════════════════
# 题型 5: 数位和匹配 - digit_sum(N * A) == digit_sum(N)
# ═══════════════════════════════════════════

def _solve_digit_sum_equal_self(text: str, agent_id: int) -> Optional[int]:
    """
    Compute the smallest positive integer N such that
    the sum of the digits of (N * AGENT_ID) equals the sum of the digits of N,
    and N is divisible by the sum of the digits of AGENT_ID.
    """
    if "smallest positive integer" not in text.lower():
        return None

    # 匹配 digit_sum(N * A) == digit_sum(N) + divisible by digit_sum(A)
    if "digits of N" not in text and f"digits of {agent_id}" not in text:
        return None
    if "divisible by" not in text.lower():
        return None

    a = agent_id
    ds_a = _digit_sum(a)
    if ds_a == 0:
        ds_a = 1

    for n in range(1, 1000000):
        if n % ds_a == 0 and _digit_sum(n * a) == _digit_sum(n):
            return n
    return 0


# ═══════════════════════════════════════════
# 题型 6: 数位和目标 - digit_sum(N * A) == A mod 10
# ═══════════════════════════════════════════

def _solve_digit_sum_target(text: str, agent_id: int) -> Optional[int]:
    """
    Compute the smallest positive integer N such that
    the sum of the digits of (N * AGENT_ID) equals AGENT_ID mod 10.
    Then compute the sum of all prime factors (including multiplicities) of N.
    """
    if "smallest positive integer" not in text.lower():
        return None

    target_match = re.search(
        r"equals\s+(?:\{?AGENT_ID\}?|" + str(agent_id) + r")\s+mod\s+(\d+)",
        text, re.IGNORECASE
    )
    if not target_match:
        return None

    mod_val = int(target_match.group(1))
    target = agent_id % mod_val
    a = agent_id

    n = None
    for candidate in range(1, 1000000):
        if _digit_sum(candidate * a) == target:
            n = candidate
            break

    if n is None:
        return 0

    # 检查是否需要 prime factor sum
    if "prime factor" in text.lower():
        return _sum_prime_factors(n)
    return n


# ═══════════════════════════════════════════
# 题型 7: Fibonacci-like 序列
# ═══════════════════════════════════════════

def _solve_fibonacci_like_mod(text: str, agent_id: int) -> Optional[int]:
    """
    a_0 = 1, a_1 = 2, a_n = (a_{n-1} + a_{n-2}) mod M
    Compute a_{K} or find smallest k with condition
    """
    # 匹配 a_0 = X, a_1 = Y, a_n = (a_{n-1} + a_{n-2}) mod M
    init_match = re.search(
        r"a_0\s*=\s*(\d+)\s*,\s*a_1\s*=\s*(\d+)\s*,.*?"
        r"a_n\s*=\s*\(\s*a_\{?n-1\}?\s*\+\s*a_\{?n-2\}?\s*\)\s*mod\s*\(?\s*(?:N\s*\+\s*(\d+)|(\d+))\s*\)?",
        text, re.IGNORECASE
    )
    if not init_match:
        return None

    a0 = int(init_match.group(1))
    a1 = int(init_match.group(2))
    if init_match.group(3):
        m = agent_id + int(init_match.group(3))
    else:
        m = int(init_match.group(4))

    # 生成序列
    seq = [a0, a1]
    for i in range(2, 10000):
        seq.append((seq[-1] + seq[-2]) % m)

    # 检查是否要计算 a_{K}
    idx_match = re.search(
        r"a_\{?\s*(?:N\s*mod\s*(\d+)\s*\+\s*(\d+)|(\d+))\s*\}?",
        text[init_match.end():], re.IGNORECASE
    )

    # 检查是否找 smallest k with condition
    cond_match = re.search(
        r"smallest\s+positive\s+integer\s+k\s+such\s+that\s+a_k\s*≡?\s*0\s*\(?\s*mod\s*(\d+)\s*\)?"
        r".*?a_\{?k\+1\}?\s*≡?\s*0\s*\(?\s*mod\s*(\d+)\s*\)?",
        text, re.IGNORECASE
    )
    if cond_match:
        mod1 = int(cond_match.group(1))
        mod2 = int(cond_match.group(2))
        for k in range(1, len(seq) - 1):
            if seq[k] % mod1 == 0 and seq[k + 1] % mod2 == 0:
                # 检查是否有后续计算
                final_match = re.search(
                    r"\(?\s*k\s*\*\s*(?:\{?AGENT_ID\}?|" + str(agent_id) + r")\s*\)?\s*mod\s*(\d+)",
                    text, re.IGNORECASE
                )
                if final_match:
                    return (k * agent_id) % int(final_match.group(1))
                return k
        return -1

    # 计算 a_{K} 然后可能有后续操作
    if idx_match:
        if idx_match.group(1) and idx_match.group(2):
            k = (agent_id % int(idx_match.group(1))) + int(idx_match.group(2))
        elif idx_match.group(3):
            k = int(idx_match.group(3))
        else:
            return None

        if k < len(seq):
            r = seq[k]
            # 检查后续操作 (R * (N mod X + Y)) mod Z
            post_match = re.search(
                r"\(?\s*R\s*\*\s*\(?\s*N\s*mod\s*(\d+)\s*\+\s*(\d+)\s*\)?\s*\)?\s*mod\s*(\d+)",
                text, re.IGNORECASE
            )
            if post_match:
                mx = int(post_match.group(1))
                my = int(post_match.group(2))
                mz = int(post_match.group(3))
                return (r * (agent_id % mx + my)) % mz
            return r

    return None


# ═══════════════════════════════════════════
# 题型 8: 自定义序列求和
# ═══════════════════════════════════════════

def _solve_custom_sequence_sum(text: str, agent_id: int) -> Optional[int]:
    """
    a_0 = N mod X, a_{k+1} = (a_k^2 + C) mod M
    Compute sum S = a_0 + a_1 + ... + a_{T}
    Then various post-processing
    """
    init_match = re.search(
        r"a_0\s*=\s*(?:N\s*mod\s*(\d+)|(\d+))\s*,"
        r".*?a_\{?k\+1\}?\s*=\s*\(\s*a_k\s*\^?\s*(\d+)?\s*\*?\s*(\d+)?\s*\+\s*(\d+)\s*\)\s*mod\s*(\d+)",
        text, re.IGNORECASE
    )
    if not init_match:
        # 尝试另一种格式: a_{k+1} = (a_k * C1 + C2) mod M
        init_match2 = re.search(
            r"a_\{?1\}?\s*=\s*(?:N\s*mod\s*(\d+)|(\d+))\s*,"
            r".*?a_\{?k\+1\}?\s*=\s*\(\s*a_k\s*\*\s*(\d+)\s*\+\s*(\d+)\s*\)\s*mod\s*(\d+)",
            text, re.IGNORECASE
        )
        if not init_match2:
            return None

        if init_match2.group(1):
            a0 = agent_id % int(init_match2.group(1))
        else:
            a0 = int(init_match2.group(2))
        mult = int(init_match2.group(3))
        add = int(init_match2.group(4))
        mod = int(init_match2.group(5))

        seq = [a0]
        for _ in range(10000):
            seq.append((seq[-1] * mult + add) % mod)

        return _apply_sequence_post(text, seq, agent_id)

    if init_match.group(1):
        a0 = agent_id % int(init_match.group(1))
    else:
        a0 = int(init_match.group(2))

    power = int(init_match.group(3)) if init_match.group(3) else 2
    mult_factor = int(init_match.group(4)) if init_match.group(4) else 1
    add_const = int(init_match.group(5))
    mod = int(init_match.group(6))

    seq = [a0]
    for _ in range(10000):
        val = seq[-1]
        next_val = ((val ** power) * mult_factor + add_const) % mod
        seq.append(next_val)

    return _apply_sequence_post(text, seq, agent_id)


def _apply_sequence_post(text: str, seq: list, agent_id: int) -> Optional[int]:
    """处理序列后续操作"""
    # 求和 S = a_0 + ... + a_{T}
    sum_match = re.search(r"a_0\s*\+\s*a_1\s*\+.*?a_\{?\s*(\d+)\s*\}?", text)
    if sum_match:
        t = int(sum_match.group(1))
        if t < len(seq):
            s = sum(seq[:t + 1])

            # 后续操作: M = (S * N) mod X, then F = ...
            post_match = re.search(
                r"M\s*=\s*\(\s*S\s*\*\s*N\s*\)\s*mod\s*(\d+)", text, re.IGNORECASE
            )
            if post_match:
                mx = int(post_match.group(1))
                m_val = (s * agent_id) % mx

                # F = M^2 mod Y (if even) or F = (M * 3) mod Y (if odd)
                f_match = re.search(
                    r"M\s+is\s+even.*?F\s*=\s*M\s*\^?\s*2\s*mod\s*(\d+).*?"
                    r"F\s*=\s*\(\s*M\s*\*\s*3\s*\)\s*mod\s*(\d+)",
                    text, re.IGNORECASE | re.DOTALL
                )
                if f_match:
                    mod_even = int(f_match.group(1))
                    mod_odd = int(f_match.group(2))
                    if m_val % 2 == 0:
                        return (m_val ** 2) % mod_even
                    else:
                        return (m_val * 3) % mod_odd

                return m_val
            return s

    # smallest m such that a_m ≡ a_{2m} (mod X)
    cong_match = re.search(
        r"smallest.*?m.*?a_m\s*≡\s*a_\{?2m\}?\s*\(?\s*mod\s*(\d+)\s*\)?",
        text, re.IGNORECASE
    )
    if cong_match:
        mod_val = int(cong_match.group(1))
        for m in range(1, len(seq) // 2):
            if seq[m] % mod_val == seq[2 * m] % mod_val:
                return m
        return -1

    return None


# ═══════════════════════════════════════════
# 题型 9: 周期检测
# ═══════════════════════════════════════════

def _solve_sequence_congruence(text: str, agent_id: int) -> Optional[int]:
    """
    Sequence with cycle detection: smallest m such that a_m ≡ a_{2m} (mod X)
    """
    if "smallest" not in text.lower() or "a_m" not in text:
        return None
    if "a_{2m}" not in text and "a_2m" not in text:
        return None

    # 已在 _solve_custom_sequence_sum 中处理
    return None


# ═══════════════════════════════════════════
# 题型 10: 图论最短路
# ═══════════════════════════════════════════

def _solve_graph_shortest_path(text: str, agent_id: int) -> Optional[int]:
    """
    Directed graph with N nodes. Edge i->i+1 weight 1.
    Edge i->(i² mod N) weight 2 (with conditions).
    Shortest path from 0 to N-1.
    """
    if "directed graph" not in text.lower() and "shortest path" not in text.lower():
        return None

    # 提取 N
    n_match = re.search(
        r"N\s*=\s*\(?\s*(?:AGENT_ID|" + str(agent_id) + r")\s*mod\s*(\d+)\s*\)?\s*\+\s*(\d+)",
        text, re.IGNORECASE
    )
    if n_match:
        n = (agent_id % int(n_match.group(1))) + int(n_match.group(2))
    else:
        n_match2 = re.search(r"N\s*=\s*(\d+)", text)
        if n_match2:
            n = int(n_match2.group(1))
        else:
            return None

    # 构建图并用 Dijkstra 求最短路
    # Edge: i -> i+1, weight 1 (for i < N-1)
    # Edge: i -> (i² mod N), weight 2 (if (i² mod N) != i and (i² mod N) != i+1)
    adj = [[] for _ in range(n)]

    for i in range(n - 1):
        adj[i].append((i + 1, 1))

    for i in range(n):
        target = (i * i) % n
        if target != i and (i >= n - 1 or target != i + 1):
            adj[i].append((target, 2))

    # Dijkstra
    dist = [float('inf')] * n
    dist[0] = 0
    pq = [(0, 0)]

    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        if u == n - 1:
            return d
        for v, w in adj[u]:
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                heapq.heappush(pq, (nd, v))

    return -1 if dist[n - 1] == float('inf') else dist[n - 1]


# ═══════════════════════════════════════════
# 题型 11: 整数分区计数
# ═══════════════════════════════════════════

def _solve_partition_count(text: str, agent_id: int) -> Optional[int]:
    """
    Compute the number of distinct ways to partition N into exactly three
    positive integer parts (order does not matter) such that the sum of the
    squares of the three parts is divisible by D.
    """
    if "partition" not in text.lower():
        return None
    if "three" not in text.lower():
        return None

    # 提取 N
    n_match = re.search(
        r"N\s*=\s*\(?\s*(?:AGENT_ID|" + str(agent_id) + r")\s*mod\s*(\d+)\s*\)?\s*\+\s*(\d+)",
        text, re.IGNORECASE
    )
    if n_match:
        n = (agent_id % int(n_match.group(1))) + int(n_match.group(2))
    else:
        return None

    # 提取 divisibility condition
    div_match = re.search(r"divisible\s+by\s+(\d+)", text, re.IGNORECASE)
    if not div_match:
        return None
    d = int(div_match.group(1))

    count = 0
    for a in range(1, n):
        for b in range(a, n):
            c = n - a - b
            if c >= b and c >= 1:
                if (a * a + b * b + c * c) % d == 0:
                    count += 1

    return count


# ═══════════════════════════════════════════
# 题型 12: 数位累加序列
# ═══════════════════════════════════════════

def _solve_digit_accum_sequence(text: str, agent_id: int) -> Optional[int]:
    """
    a₁ = N, a_{k+1} = a_k + digit_sum(a_k)
    Compute smallest m such that a_m is divisible by D.
    """
    if "sum of digits" not in text.lower() and "digit" not in text.lower():
        return None

    # 匹配 a_{k+1} = a_k + (sum of digits of a_k)
    if "a_k + " not in text.replace("{", "").replace("}", "") and \
       "a_k +" not in text.replace("{", "").replace("}", ""):
        return None

    # 提取 N
    n_match = re.search(
        r"N\s*=\s*\(?\s*(?:AGENT_ID|" + str(agent_id) + r")\s*mod\s*(\d+)\s*\)?\s*\+\s*(\d+)",
        text, re.IGNORECASE
    )
    if n_match:
        n = (agent_id % int(n_match.group(1))) + int(n_match.group(2))
    else:
        n = _extract_n(text, agent_id)
    if n is None:
        return None

    # 提取 divisible by D
    div_match = re.search(r"divisible\s+by\s+(\d+)", text, re.IGNORECASE)
    if not div_match:
        return None
    d = int(div_match.group(1))

    a = n
    for m in range(1, 1000000):
        if a % d == 0:
            return m
        a = a + _digit_sum(a)

    return -1


# ═══════════════════════════════════════════
# 题型 13: 自定义序列周期检测
# ═══════════════════════════════════════════

def _solve_custom_sequence_cycle(text: str, agent_id: int) -> Optional[int]:
    """
    a_1 = N mod X, a_{k+1} = (a_k * C1 + C2) mod M
    Compute smallest m such that a_m ≡ a_{2m} (mod D)
    """
    if "smallest" not in text.lower():
        return None
    if "a_m" not in text:
        return None

    # 匹配序列定义
    init_match = re.search(
        r"a_\{?1\}?\s*=\s*(?:N\s*mod\s*(\d+)|(\d+))",
        text, re.IGNORECASE
    )
    if not init_match:
        return None

    if init_match.group(1):
        a1 = agent_id % int(init_match.group(1))
    else:
        a1 = int(init_match.group(2))

    # 匹配递推关系
    rec_match = re.search(
        r"a_\{?k\+1\}?\s*=\s*\(\s*a_k\s*\*\s*(\d+)\s*\+\s*(\d+)\s*\)\s*mod\s*(\d+)",
        text, re.IGNORECASE
    )
    if not rec_match:
        return None

    mult = int(rec_match.group(1))
    add = int(rec_match.group(2))
    mod = int(rec_match.group(3))

    # 匹配条件
    cond_match = re.search(
        r"a_m\s*≡\s*a_\{?2m\}?\s*\(?\s*mod\s*(\d+)\s*\)?",
        text, re.IGNORECASE
    )
    if not cond_match:
        return None

    cond_mod = int(cond_match.group(1))

    # 生成序列
    seq = [0, a1]  # 1-indexed
    for _ in range(20000):
        seq.append((seq[-1] * mult + add) % mod)

    for m in range(1, len(seq) // 2):
        if seq[m] % cond_mod == seq[2 * m] % cond_mod:
            return m

    return -1


# ═══════════════════════════════════════════
# 题型 14: Harshad 数 + 模条件求和
# ═══════════════════════════════════════════

def _solve_harshad_modular_sum(text: str, agent_id: int) -> Optional[int]:
    """
    Compute the sum of all positive integers n ≤ 1000 such that
    n is divisible by the sum of its digits (Harshad number),
    and also n mod (AGENT_ID mod 17 + 2) = (AGENT_ID mod 5).
    """
    if "divisible by the sum of its digits" not in text.lower() and \
       "divisible by the sum of digits" not in text.lower():
        return None

    # 提取 modulus: AGENT_ID mod X + Y
    mod_match = re.search(
        r"(?:AGENT_ID|" + str(agent_id) + r")\s*mod\s*(\d+)\s*\+\s*(\d+)",
        text
    )
    # 提取 remainder: AGENT_ID mod Z
    rem_match = re.search(
        r"=\s*\(?\s*(?:AGENT_ID|" + str(agent_id) + r")\s*mod\s*(\d+)\s*\)?",
        text
    )

    if not mod_match or not rem_match:
        return None

    m = (agent_id % int(mod_match.group(1))) + int(mod_match.group(2))
    r = agent_id % int(rem_match.group(1))

    # 提取上限 N
    limit_match = re.search(r"n\s*[≤<=]\s*(\d+)", text, re.IGNORECASE)
    limit = int(limit_match.group(1)) if limit_match else 1000

    total = 0
    for n in range(1, limit + 1):
        ds = _digit_sum(n)
        if ds > 0 and n % ds == 0 and n % m == r:
            total += n

    return total


# ═══════════════════════════════════════════
# 题型 15: 格点计数 (lattice points)
# ═══════════════════════════════════════════

def _solve_lattice_points(text: str, agent_id: int) -> Optional[int]:
    """
    Compute the number of lattice points (x, y) with integer coordinates
    that satisfy: x^2 + y^2 ≤ N^2, x + y is even, |x| + |y| ≤ N.
    """
    if "lattice point" not in text.lower():
        return None

    # 提取 N
    n = _extract_n(text, agent_id)
    if n is None:
        return None

    count = 0
    for x in range(-n, n + 1):
        for y in range(-n, n + 1):
            if x * x + y * y <= n * n and (x + y) % 2 == 0 and abs(x) + abs(y) <= n:
                count += 1

    return count


# ═══════════════════════════════════════════
# 题型 16: 序列平方取模求和
# ═══════════════════════════════════════════

def _solve_sequence_square_mod_sum(text: str, agent_id: int) -> Optional[int]:
    """
    a₁ = AGENT_ID mod X, aₙ₊₁ = (aₙ² + C) mod M
    S = sum of first K terms
    Then compute (S × AGENT_ID) mod P, with conditional logic
    """
    # 匹配: a₁ = {AGENT_ID} mod X
    init_match = re.search(
        r"a[₁1]\s*=\s*\{?AGENT_ID\}?\s*mod\s*(\d+)",
        text, re.IGNORECASE
    )
    if not init_match:
        init_match = re.search(
            r"a[₁1]\s*=\s*" + str(agent_id) + r"\s*mod\s*(\d+)",
            text, re.IGNORECASE
        )
    if not init_match:
        return None

    a1 = agent_id % int(init_match.group(1))

    # 匹配递推: aₙ₊₁ = (aₙ² + C) mod M
    rec_match = re.search(
        r"a[ₙn][₊+][₁1]\s*=\s*\(\s*a[ₙn][²2^]\s*\+\s*(\d+)\s*\)\s*mod\s*(\d+)",
        text, re.IGNORECASE
    )
    if not rec_match:
        rec_match = re.search(
            r"a_\{?n\+1\}?\s*=\s*\(\s*a_?n\s*[²^]\s*2?\s*\+\s*(\d+)\s*\)\s*mod\s*(\d+)",
            text, re.IGNORECASE
        )
    if not rec_match:
        return None

    c = int(rec_match.group(1))
    m = int(rec_match.group(2))

    # 匹配求和项数
    sum_match = re.search(r"first\s+(\d+)\s+terms", text, re.IGNORECASE)
    if not sum_match:
        return None
    k = int(sum_match.group(1))

    # 生成序列
    seq = [a1]
    for _ in range(k - 1):
        seq.append((seq[-1] ** 2 + c) % m)
    s = sum(seq)

    # 后续操作: (S × AGENT_ID) mod P
    post_match = re.search(
        r"\(\s*S\s*[×x*]\s*\{?(?:AGENT_ID|" + str(agent_id) + r")\}?\s*\)\s*mod\s*(\d+)",
        text, re.IGNORECASE
    )
    if post_match:
        p = int(post_match.group(1))
        result = (s * agent_id) % p

        # 条件分支: if even F=R^2 mod X, if odd F=(R*3) mod X
        cond_match = re.search(
            r"even.*?=\s*\w+\s*[²^]\s*2?\s*mod\s*(\d+).*?odd.*?=.*?\*\s*3.*?mod\s*(\d+)",
            text, re.IGNORECASE | re.DOTALL
        )
        if cond_match:
            mod_even = int(cond_match.group(1))
            mod_odd = int(cond_match.group(2))
            if result % 2 == 0:
                return (result ** 2) % mod_even
            else:
                return (result * 3) % mod_odd

        return result

    return s


# ═══════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════

def _extract_n(text: str, agent_id: int) -> Optional[int]:
    """从题目文本中提取 N 的值"""
    # N = (AGENT_ID mod X) + Y
    m = re.search(
        r"N\s*=\s*\(?\s*(?:AGENT_ID|" + str(agent_id) + r")\s*mod\s*(\d+)\s*\)?\s*\+\s*(\d+)",
        text, re.IGNORECASE
    )
    if m:
        return (agent_id % int(m.group(1))) + int(m.group(2))

    # N = AGENT_ID 或 N = {AGENT_ID}
    m = re.search(r"N\s*=\s*(?:\{?AGENT_ID\}?|" + str(agent_id) + r")\b", text)
    if m:
        return agent_id

    # Let N = 数字
    m = re.search(r"N\s*=\s*(\d+)", text)
    if m:
        return int(m.group(1))

    return None


def _digit_sum(n: int) -> int:
    """计算数位和"""
    return sum(int(d) for d in str(abs(n)))


def _sum_prime_factors(n: int) -> int:
    """计算所有质因子之和（含重复）"""
    if n <= 1:
        return 0
    total = 0
    d = 2
    while d * d <= n:
        while n % d == 0:
            total += d
            n //= d
        d += 1
    if n > 1:
        total += n
    return total
