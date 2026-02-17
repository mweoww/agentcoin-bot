#!/usr/bin/env python3
"""
AgentCoin Web 控制面板
三页面：注册管理 + 挖矿监控 + 控制中心
内置进程管理，直接在容器内启动/停止注册和挖矿子进程
访问 http://localhost:8080
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template_string, jsonify, request, Response

app = Flask(__name__)

DATA_DIR = Path(__file__).parent / "data"
ACCOUNTS_FILE = DATA_DIR / "accounts.txt"
REGISTERED_FILE = DATA_DIR / "registered.txt"
MINE_STATUS_FILE = DATA_DIR / "mine_status.json"
REG_LOG_FILE = DATA_DIR / "register_log.jsonl"

_logs: deque = deque(maxlen=200)
_logs_lock = threading.Lock()

# 进程管理
_processes = {
    "register": None,
    "mine": None,
}
_proc_lock = threading.Lock()


# ═══════════════════════════════════════════
# 数据读取
# ═══════════════════════════════════════════

def _count_lines(filepath: Path) -> int:
    if not filepath.exists():
        return 0
    text = filepath.read_text().strip()
    if not text:
        return 0
    return len([l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")])


def _load_registered():
    if not REGISTERED_FILE.exists():
        return []
    accounts = []
    for line in REGISTERED_FILE.read_text().strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("----")
        if len(parts) >= 3:
            accounts.append({
                "token": parts[0][:12] + "...",
                "wallet": parts[1],
                "key": parts[2][:8] + "..." + parts[2][-4:] if len(parts[2]) > 12 else "***",
            })
    return accounts


def _get_stats():
    total = _count_lines(ACCOUNTS_FILE)
    registered = _count_lines(REGISTERED_FILE)
    return {
        "total": total,
        "registered": registered,
        "pending": total - registered,
        "success_rate": f"{registered/total*100:.1f}" if total > 0 else "0",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _get_mine_status():
    default = {"running": False, "active": 0, "total_accounts": 0,
               "total_solved": 0, "total_submitted": 0, "total_rewards": 0,
               "total_errors": 0, "elapsed_seconds": 0, "miners": [], "logs": [],
               "updated_at": "-"}
    if not MINE_STATUS_FILE.exists():
        # 尝试从持久化统计文件读取历史数据
        stats_file = DATA_DIR / "mine_stats.json"
        if stats_file.exists():
            try:
                hist = json.loads(stats_file.read_text())
                default["total_solved"] = hist.get("total_solved", 0)
                default["total_submitted"] = hist.get("total_submitted", 0)
                default["total_rewards"] = hist.get("total_rewards", 0)
                default["total_errors"] = hist.get("total_errors", 0)
                default["updated_at"] = hist.get("updated_at", "-")
            except Exception:
                pass
        return default
    try:
        return json.loads(MINE_STATUS_FILE.read_text())
    except Exception:
        return default


def _get_process_status():
    with _proc_lock:
        result = {}
        for name, proc in _processes.items():
            if proc is None:
                result[name] = {"running": False, "pid": None}
            elif proc.poll() is None:
                result[name] = {"running": True, "pid": proc.pid}
            else:
                result[name] = {"running": False, "pid": None, "exit_code": proc.returncode}
                _processes[name] = None
        return result


def _read_reg_logs():
    """读取注册进程的实时日志"""
    if not REG_LOG_FILE.exists():
        return []
    try:
        lines = REG_LOG_FILE.read_text().strip().splitlines()[-100:]
        logs = []
        for line in lines:
            line = line.strip()
            if line:
                try:
                    logs.append(json.loads(line))
                except Exception:
                    logs.append({"time": "", "level": "系统", "message": line})
        return logs
    except Exception:
        return []


def _load_registered_full():
    """读取 registered.txt 完整数据（含私钥）"""
    if not REGISTERED_FILE.exists():
        return []
    accounts = []
    for line in REGISTERED_FILE.read_text().strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("----")
        if len(parts) >= 3:
            accounts.append({
                "wallet": parts[1].strip(),
                "private_key": parts[2].strip(),
                "x_handle": parts[3].strip() if len(parts) >= 4 else "",
            })
    return accounts


def _get_gas_insufficient_wallets() -> set:
    """获取 Gas 不足的钱包地址集合（从缓存读取）"""
    if _gas_cache is not None:
        return {w for w, bal in _gas_cache.items() if bal < 0.0001}
    return set()


def _query_all_balances() -> dict:
    """
    实时查询所有已注册账号的链上 ETH 余额
    返回: {wallet: eth_balance_float, ...}
    """
    global _gas_cache, _gas_cache_time, _gas_query_elapsed

    all_accounts = _load_registered_full()
    if not all_accounts:
        return {}

    balances = {}
    t0 = time.time()

    try:
        import contracts
        from concurrent.futures import ThreadPoolExecutor, as_completed

        w3 = contracts.get_w3()
        if not w3.is_connected():
            return {}

        wallets = [acc["wallet"] for acc in all_accounts]

        def check_balance(wallet: str) -> tuple:
            try:
                balance = w3.eth.get_balance(w3.to_checksum_address(wallet))
                eth_bal = float(w3.from_wei(balance, "ether"))
                return wallet, eth_bal
            except Exception:
                return wallet, -1.0

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(check_balance, w): w for w in wallets}
            for future in as_completed(futures, timeout=90):
                try:
                    wallet, bal = future.result()
                    if bal >= 0:
                        balances[wallet] = bal
                except Exception:
                    pass

    except Exception:
        pass

    elapsed = time.time() - t0
    _gas_cache = balances
    _gas_cache_time = time.time()
    _gas_query_elapsed = round(elapsed, 1)
    return balances


# Gas 缓存: {wallet: eth_balance, ...}
_gas_cache: dict | None = None
_gas_cache_time: float = 0
_gas_query_elapsed: float = 0


def _get_export_data(filter_type: str, export_format: str) -> str:
    """
    生成导出文本
    filter_type: all / gas_insufficient / gas_ok
    export_format: address / address_key
    """
    all_accounts = _load_registered_full()

    # 获取 Gas 不足的钱包（实时查链 + mine_status 双重判断）
    gas_insufficient_wallets = _get_gas_insufficient_wallets()

    # 筛选
    if filter_type == "gas_insufficient":
        accounts = [a for a in all_accounts if a["wallet"] in gas_insufficient_wallets]
    elif filter_type == "gas_ok":
        accounts = [a for a in all_accounts if a["wallet"] not in gas_insufficient_wallets]
    else:
        accounts = all_accounts

    # 格式化
    lines = []
    for a in accounts:
        if export_format == "address_key":
            lines.append(f"{a['wallet']}----{a['private_key']}")
        else:
            lines.append(a["wallet"])

    return "\n".join(lines)


# ═══════════════════════════════════════════
# 进程管理
# ═══════════════════════════════════════════

def _start_register(start: int, count: int, workers: int):
    with _proc_lock:
        proc = _processes.get("register")
        if proc and proc.poll() is None:
            return {"ok": False, "error": "注册进程已在运行"}

        # 清空旧日志
        if REG_LOG_FILE.exists():
            REG_LOG_FILE.unlink()

        cmd = [sys.executable, "-u", "register.py", "--batch",
               "--start", str(start), "--count", str(count), "--workers", str(workers)]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(Path(__file__).parent),
            env={**os.environ},
        )
        _processes["register"] = proc

        # 后台线程读取输出写入日志文件
        def _read_output():
            for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                entry = json.dumps({"time": datetime.now().strftime("%H:%M:%S"),
                                     "level": "信息", "message": line}, ensure_ascii=False)
                with open(REG_LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(entry + "\n")
                add_log("信息", f"[注册] {line}")

        threading.Thread(target=_read_output, daemon=True).start()
        add_log("系统", f"注册进程已启动 (start={start}, count={count}, workers={workers})")
        return {"ok": True, "pid": proc.pid}


def _stop_register():
    with _proc_lock:
        proc = _processes.get("register")
        if proc and proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            _processes["register"] = None
            add_log("系统", "注册进程已停止")
            return {"ok": True}
        return {"ok": False, "error": "注册进程未在运行"}


def _start_mine(start: int, count: int, workers: int):
    with _proc_lock:
        proc = _processes.get("mine")
        if proc and proc.poll() is None:
            return {"ok": False, "error": "挖矿进程已在运行"}

        cmd = [sys.executable, "-u", "mine.py", "--batch",
               "--start", str(start), "--count", str(count), "--workers", str(workers)]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(Path(__file__).parent),
            env={**os.environ},
        )
        _processes["mine"] = proc

        def _read_output():
            for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if line:
                    add_log("信息", f"[挖矿] {line}")

        threading.Thread(target=_read_output, daemon=True).start()
        add_log("系统", f"挖矿进程已启动 (start={start}, count={count}, workers={workers})")
        return {"ok": True, "pid": proc.pid}


def _stop_mine():
    with _proc_lock:
        proc = _processes.get("mine")
        if proc and proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            _processes["mine"] = None
            add_log("系统", "挖矿进程已停止")
            return {"ok": True}
        return {"ok": False, "error": "挖矿进程未在运行"}


# ═══════════════════════════════════════════
# HTML 模板
# ═══════════════════════════════════════════

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AgentCoin 控制面板</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:#0a0e17; color:#e0e6ed; min-height:100vh; }
  .header { background:linear-gradient(135deg,#1a1f35,#0d1321); border-bottom:1px solid #1e2a3a; padding:16px 30px; display:flex; align-items:center; justify-content:space-between; }
  .header h1 { font-size:20px; color:#00d4ff; }
  .header .status { font-size:13px; color:#7a8ba3; }
  .header .dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:#00e676; margin-right:6px; animation:pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

  .tabs { display:flex; background:#111827; border-bottom:1px solid #1e2a3a; padding:0 30px; }
  .tab { padding:12px 24px; font-size:14px; color:#5a6a7a; cursor:pointer; border-bottom:2px solid transparent; transition:all .2s; user-select:none; }
  .tab:hover { color:#a0b0c0; }
  .tab.active { color:#00d4ff; border-bottom-color:#00d4ff; }

  .page { display:none; }
  .page.active { display:block; }
  .container { max-width:1400px; margin:0 auto; padding:24px; }

  .cards { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:24px; }
  .card { background:#111827; border:1px solid #1e2a3a; border-radius:12px; padding:20px; text-align:center; }
  .card .label { font-size:11px; color:#7a8ba3; text-transform:uppercase; letter-spacing:1px; margin-bottom:8px; }
  .card .value { font-size:32px; font-weight:700; }
  .card .sub { font-size:12px; color:#5a6a7a; margin-top:4px; }
  .c-blue .value{color:#00d4ff} .c-green .value{color:#00e676} .c-orange .value{color:#ffa726}
  .c-purple .value{color:#ab47bc} .c-red .value{color:#ef5350} .c-pink .value{color:#f06292}

  .progress-wrap { background:#111827; border:1px solid #1e2a3a; border-radius:12px; padding:20px; margin-bottom:24px; }
  .progress-wrap .title { font-size:14px; color:#7a8ba3; margin-bottom:12px; }
  .progress-bar { height:22px; background:#1a2332; border-radius:11px; overflow:hidden; }
  .progress-bar .fill { height:100%; border-radius:11px; transition:width 1s ease; }
  .fill-reg { background:linear-gradient(90deg,#00d4ff,#00e676); }
  .progress-text { text-align:center; margin-top:8px; font-size:13px; color:#7a8ba3; }

  .panels { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  .panel { background:#111827; border:1px solid #1e2a3a; border-radius:12px; overflow:hidden; }
  .panel-header { padding:14px 20px; border-bottom:1px solid #1e2a3a; font-size:14px; font-weight:600; color:#00d4ff; }
  .panel-body { padding:0; max-height:400px; overflow-y:auto; }
  .panel-body::-webkit-scrollbar{width:6px} .panel-body::-webkit-scrollbar-track{background:#0a0e17} .panel-body::-webkit-scrollbar-thumb{background:#2a3a4a;border-radius:3px}

  table{width:100%;border-collapse:collapse}
  table th{padding:10px 14px;text-align:left;font-size:11px;color:#5a6a7a;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid #1e2a3a;position:sticky;top:0;background:#111827}
  table td{padding:8px 14px;font-size:12px;border-bottom:1px solid #0d1321;font-family:'SF Mono','Fira Code',monospace}
  table tr:hover{background:#151d2e}
  .wallet{color:#00d4ff} .key-masked{color:#5a6a7a}

  .log-entry{padding:5px 14px;font-size:12px;font-family:'SF Mono','Fira Code',monospace;border-bottom:1px solid #0d1321;display:flex;gap:8px}
  .log-entry:hover{background:#151d2e}
  .log-time{color:#5a6a7a;min-width:65px} .log-level{min-width:36px;font-weight:600}
  .log-level.info{color:#00d4ff} .log-level.success{color:#00e676} .log-level.warn{color:#ffa726} .log-level.error{color:#ef5350} .log-level.system{color:#5a6a7a}
  .log-msg{color:#c0c8d4}

  .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
  .badge-run{background:#00e67622;color:#00e676} .badge-stop{background:#ef535022;color:#ef5350}

  /* 控制面板样式 */
  .ctrl-section { background:#111827; border:1px solid #1e2a3a; border-radius:12px; padding:24px; margin-bottom:24px; }
  .ctrl-section h3 { font-size:16px; color:#00d4ff; margin-bottom:16px; padding-bottom:10px; border-bottom:1px solid #1e2a3a; }
  .ctrl-row { display:flex; gap:16px; align-items:flex-end; flex-wrap:wrap; margin-bottom:16px; }
  .ctrl-field { display:flex; flex-direction:column; gap:6px; }
  .ctrl-field label { font-size:12px; color:#7a8ba3; text-transform:uppercase; letter-spacing:1px; }
  .ctrl-field input, .ctrl-field select {
    background:#0a0e17; border:1px solid #2a3a4a; border-radius:8px; padding:10px 14px;
    color:#e0e6ed; font-size:14px; width:140px; outline:none; transition:border .2s;
  }
  .ctrl-field input:focus, .ctrl-field select:focus { border-color:#00d4ff; }
  .btn {
    padding:10px 24px; border:none; border-radius:8px; font-size:14px; font-weight:600;
    cursor:pointer; transition:all .2s; display:inline-flex; align-items:center; gap:8px;
  }
  .btn:disabled { opacity:0.5; cursor:not-allowed; }
  .btn-start { background:linear-gradient(135deg,#00d4ff,#00e676); color:#0a0e17; }
  .btn-start:hover:not(:disabled) { transform:translateY(-1px); box-shadow:0 4px 15px rgba(0,212,255,0.3); }
  .btn-stop { background:linear-gradient(135deg,#ef5350,#f06292); color:#fff; }
  .btn-stop:hover:not(:disabled) { transform:translateY(-1px); box-shadow:0 4px 15px rgba(239,83,80,0.3); }
  .proc-status { display:flex; align-items:center; gap:10px; padding:12px 16px; background:#0a0e17; border-radius:8px; margin-top:8px; }
  .proc-dot { width:10px; height:10px; border-radius:50%; }
  .proc-dot.on { background:#00e676; animation:pulse 2s infinite; }
  .proc-dot.off { background:#5a6a7a; }

  /* 导出面板 */
  .export-section { background:#111827; border:1px solid #1e2a3a; border-radius:12px; padding:20px; margin-bottom:24px; }
  .export-section h3 { font-size:15px; color:#00d4ff; margin-bottom:14px; padding-bottom:10px; border-bottom:1px solid #1e2a3a; display:flex; align-items:center; gap:8px; }
  .export-row { display:flex; gap:14px; align-items:flex-end; flex-wrap:wrap; }
  .export-field { display:flex; flex-direction:column; gap:5px; }
  .export-field label { font-size:11px; color:#7a8ba3; text-transform:uppercase; letter-spacing:1px; }
  .export-field select {
    background:#0a0e17; border:1px solid #2a3a4a; border-radius:8px; padding:9px 14px;
    color:#e0e6ed; font-size:13px; min-width:160px; outline:none; transition:border .2s;
  }
  .export-field select:focus { border-color:#00d4ff; }
  .export-stats { display:flex; gap:16px; margin-top:12px; }
  .export-stat { font-size:12px; color:#7a8ba3; }
  .export-stat b { color:#e0e6ed; }
  .btn-export { background:linear-gradient(135deg,#ab47bc,#7c4dff); color:#fff; }
  .btn-export:hover:not(:disabled) { transform:translateY(-1px); box-shadow:0 4px 15px rgba(171,71,188,0.3); }
  .btn-copy { background:#1e2a3a; color:#00d4ff; border:1px solid #2a3a4a; }
  .btn-copy:hover { background:#2a3a4a; }
  .export-preview { margin-top:14px; background:#0a0e17; border:1px solid #1e2a3a; border-radius:8px; padding:12px; max-height:200px; overflow-y:auto; font-family:'SF Mono','Fira Code',monospace; font-size:11px; color:#7a8ba3; white-space:pre; display:none; }
  .export-preview.show { display:block; }
  .toast { position:fixed; top:20px; right:20px; background:#00e676; color:#0a0e17; padding:10px 20px; border-radius:8px; font-size:13px; font-weight:600; z-index:9999; display:none; animation:fadeIn .3s; }
  @keyframes fadeIn { from{opacity:0;transform:translateY(-10px)} to{opacity:1;transform:translateY(0)} }

  .footer{text-align:center;padding:16px;font-size:12px;color:#3a4a5a}
  @media(max-width:768px){.cards{grid-template-columns:repeat(2,1fr)}.panels{grid-template-columns:1fr}.ctrl-row{flex-direction:column}.export-row{flex-direction:column}}
</style>
</head>
<body>
  <div class="header">
    <h1>AgentCoin 控制面板</h1>
    <div class="status"><span class="dot"></span>实时监控 | <span id="update-time">-</span></div>
  </div>

  <div class="tabs">
    <div class="tab" data-tab="control" onclick="switchTab('control',this)">控制中心</div>
    <div class="tab active" data-tab="register" onclick="switchTab('register',this)">注册管理</div>
    <div class="tab" data-tab="mining" onclick="switchTab('mining',this)">挖矿监控</div>
  </div>

  <!-- ═══ 控制中心 ═══ -->
  <div id="page-control" class="page">
    <div class="container">
      <!-- 批量注册控制 -->
      <div class="ctrl-section">
        <h3>批量注册</h3>
        <div class="ctrl-row">
          <div class="ctrl-field">
            <label>起始序号</label>
            <input type="number" id="reg-start" value="0" min="0">
          </div>
          <div class="ctrl-field">
            <label>注册数量</label>
            <input type="number" id="reg-count" value="500" min="1">
          </div>
          <div class="ctrl-field">
            <label>并发数</label>
            <input type="number" id="reg-workers" value="3" min="1" max="10">
          </div>
          <button class="btn btn-start" id="btn-reg-start" onclick="startRegister()">启动注册</button>
          <button class="btn btn-stop" id="btn-reg-stop" onclick="stopRegister()" disabled>停止注册</button>
        </div>
        <div class="proc-status">
          <div class="proc-dot off" id="reg-dot"></div>
          <span id="reg-status-text" style="font-size:13px;color:#7a8ba3">未运行</span>
        </div>
      </div>

      <!-- 批量挖矿控制 -->
      <div class="ctrl-section">
        <h3>批量挖矿</h3>
        <div class="ctrl-row">
          <div class="ctrl-field">
            <label>起始序号</label>
            <input type="number" id="mine-start" value="0" min="0">
          </div>
          <div class="ctrl-field">
            <label>账号数量</label>
            <input type="number" id="mine-count" value="100" min="1">
          </div>
          <div class="ctrl-field">
            <label>并发数</label>
            <input type="number" id="mine-workers" value="5" min="1" max="20">
          </div>
          <button class="btn btn-start" id="btn-mine-start" onclick="startMine()">启动挖矿</button>
          <button class="btn btn-stop" id="btn-mine-stop" onclick="stopMine()" disabled>停止挖矿</button>
        </div>
        <div class="proc-status">
          <div class="proc-dot off" id="mine-dot"></div>
          <span id="mine-status-text" style="font-size:13px;color:#7a8ba3">未运行</span>
        </div>
      </div>

      <!-- 系统日志 -->
      <div class="panel" style="margin-top:0">
        <div class="panel-header">系统日志</div>
        <div class="panel-body" id="ctrl-logs" style="max-height:300px"></div>
      </div>
    </div>
  </div>

  <!-- ═══ 注册页面 ═══ -->
  <div id="page-register" class="page active">
    <div class="container">
      <div class="cards">
        <div class="card c-blue"><div class="label">账号总数</div><div class="value" id="r-total">-</div></div>
        <div class="card c-green"><div class="label">已注册</div><div class="value" id="r-registered">-</div></div>
        <div class="card c-orange"><div class="label">待注册</div><div class="value" id="r-pending">-</div></div>
        <div class="card c-purple"><div class="label">成功率</div><div class="value" id="r-rate">-</div><div class="sub">%</div></div>
      </div>
      <div class="progress-wrap">
        <div class="title">注册进度</div>
        <div class="progress-bar"><div class="fill fill-reg" id="r-fill" style="width:0%"></div></div>
        <div class="progress-text" id="r-progress-text">0 / 0</div>
      </div>
      <!-- Gas 余额查询 -->
      <div class="export-section" id="gas-section">
        <h3>&#9889; Gas 余额查询</h3>
        <div class="export-row" style="align-items:center">
          <button class="btn" id="btn-query-gas" style="background:linear-gradient(135deg,#ffa726,#ff7043);color:#fff;font-size:15px;padding:12px 28px" onclick="queryGas()">一键查询 Gas</button>
          <span id="gas-status-text" style="font-size:13px;color:#7a8ba3">点击按钮查询所有账号链上 ETH 余额</span>
        </div>
        <div id="gas-summary" style="display:none;margin-top:14px">
          <div style="display:flex;gap:20px;flex-wrap:wrap">
            <div style="background:#0a0e17;border:1px solid #1e2a3a;border-radius:10px;padding:14px 22px;text-align:center;min-width:130px">
              <div style="font-size:11px;color:#7a8ba3;text-transform:uppercase;letter-spacing:1px">已注册</div>
              <div id="gas-total" style="font-size:28px;font-weight:700;color:#00d4ff;margin-top:4px">-</div>
            </div>
            <div style="background:#0a0e17;border:1px solid #1e2a3a;border-radius:10px;padding:14px 22px;text-align:center;min-width:130px">
              <div style="font-size:11px;color:#7a8ba3;text-transform:uppercase;letter-spacing:1px">Gas 不足</div>
              <div id="gas-bad" style="font-size:28px;font-weight:700;color:#ef5350;margin-top:4px">-</div>
            </div>
            <div style="background:#0a0e17;border:1px solid #1e2a3a;border-radius:10px;padding:14px 22px;text-align:center;min-width:130px">
              <div style="font-size:11px;color:#7a8ba3;text-transform:uppercase;letter-spacing:1px">Gas 正常</div>
              <div id="gas-good" style="font-size:28px;font-weight:700;color:#00e676;margin-top:4px">-</div>
            </div>
            <div style="background:#0a0e17;border:1px solid #1e2a3a;border-radius:10px;padding:14px 22px;text-align:center;min-width:130px">
              <div style="font-size:11px;color:#7a8ba3;text-transform:uppercase;letter-spacing:1px">查询耗时</div>
              <div id="gas-time" style="font-size:28px;font-weight:700;color:#ffa726;margin-top:4px">-</div>
            </div>
          </div>
        </div>
        <div id="gas-detail-wrap" style="display:none;margin-top:14px">
          <div style="font-size:13px;color:#7a8ba3;margin-bottom:8px">余额明细（Gas不足排在前面）</div>
          <div style="max-height:300px;overflow-y:auto;background:#0a0e17;border:1px solid #1e2a3a;border-radius:8px">
            <table>
              <thead><tr><th>#</th><th>钱包地址</th><th>ETH 余额</th><th>状态</th></tr></thead>
              <tbody id="gas-detail-body"></tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- 数据导出 -->
      <div class="export-section">
        <h3>&#128229; 数据导出</h3>
        <div class="export-row">
          <div class="export-field">
            <label>筛选条件</label>
            <select id="exp-filter" onchange="updateExportPreview()">
              <option value="all">全部账号</option>
              <option value="gas_insufficient">Gas 不足</option>
              <option value="gas_ok">Gas 正常</option>
            </select>
          </div>
          <div class="export-field">
            <label>导出格式</label>
            <select id="exp-format" onchange="updateExportPreview()">
              <option value="address">仅地址</option>
              <option value="address_key">地址 + 私钥</option>
            </select>
          </div>
          <button class="btn btn-export" onclick="doExportDownload()">下载文件</button>
          <button class="btn btn-copy" onclick="doExportCopy()">复制到剪贴板</button>
        </div>
        <div class="export-stats" id="exp-stats"></div>
        <div class="export-preview" id="exp-preview"></div>
      </div>

      <div class="panels">
        <div class="panel"><div class="panel-header">已注册账号</div><div class="panel-body"><table><thead><tr><th>#</th><th>钱包地址</th><th>Token</th><th>私钥</th></tr></thead><tbody id="r-accounts"></tbody></table></div></div>
        <div class="panel"><div class="panel-header">注册日志</div><div class="panel-body" id="r-logs"></div></div>
      </div>
    </div>
  </div>

  <!-- ═══ 挖矿页面 ═══ -->
  <div id="page-mining" class="page">
    <div class="container">
      <div class="cards">
        <div class="card c-green"><div class="label">活跃矿工</div><div class="value" id="m-active">-</div><div class="sub" id="m-badge"></div></div>
        <div class="card c-blue"><div class="label">已解题</div><div class="value" id="m-solved">-</div></div>
        <div class="card c-purple"><div class="label">已提交</div><div class="value" id="m-submitted">-</div></div>
        <div class="card c-pink"><div class="label">累计收益</div><div class="value" id="m-rewards">-</div><div class="sub">AGC</div></div>
      </div>
      <div class="cards" style="grid-template-columns:repeat(3,1fr)">
        <div class="card c-red"><div class="label">错误次数</div><div class="value" id="m-errors">-</div></div>
        <div class="card c-orange"><div class="label">运行时间</div><div class="value" id="m-elapsed" style="font-size:24px">-</div></div>
        <div class="card c-blue"><div class="label">最后更新</div><div class="value" id="m-updated" style="font-size:16px;color:#7a8ba3">-</div></div>
      </div>
      <div class="panels">
        <div class="panel"><div class="panel-header">矿工列表</div><div class="panel-body"><table><thead><tr><th>钱包</th><th>状态</th><th>解题</th><th>提交</th><th>收益</th></tr></thead><tbody id="m-miners"></tbody></table></div></div>
        <div class="panel"><div class="panel-header">挖矿日志</div><div class="panel-body" id="m-logs"></div></div>
      </div>
    </div>
  </div>

  <div id="toast" class="toast"></div>
  <div class="footer">AgentCoin Bot - 自动刷新 3s</div>

<script>
function switchTab(name, el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  if (el) el.classList.add('active');
}

function levelClass(l) {
  return {'信息':'info','成功':'success','警告':'warn','错误':'error','系统':'system','奖励':'success','提交':'success'}[l]||'info';
}
function renderLogs(c, logs) {
  c.innerHTML='';
  logs.forEach(l => {
    const d=document.createElement('div'); d.className='log-entry';
    d.innerHTML='<span class="log-time">'+l.time+'</span><span class="log-level '+levelClass(l.level)+'">'+l.level+'</span><span class="log-msg">'+l.message+'</span>';
    c.appendChild(d);
  });
  c.scrollTop=c.scrollHeight;
}
function fmtTime(s){const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60;if(h>0)return h+'时'+m+'分';if(m>0)return m+'分'+sec+'秒';return sec+'秒';}

function apiPost(url, data) {
  return fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)}).then(r=>r.json());
}

function startRegister() {
  const s=+document.getElementById('reg-start').value, c=+document.getElementById('reg-count').value, w=+document.getElementById('reg-workers').value;
  apiPost('/api/control/register/start', {start:s,count:c,workers:w}).then(d => {
    if(!d.ok) alert(d.error);
  });
}
function stopRegister() { apiPost('/api/control/register/stop',{}).then(d=>{if(!d.ok)alert(d.error)}); }
function startMine() {
  const s=+document.getElementById('mine-start').value, c=+document.getElementById('mine-count').value, w=+document.getElementById('mine-workers').value;
  apiPost('/api/control/mine/start', {start:s,count:c,workers:w}).then(d => {
    if(!d.ok) alert(d.error);
  });
}
function stopMine() { apiPost('/api/control/mine/stop',{}).then(d=>{if(!d.ok)alert(d.error)}); }

function fetchAll() {
  // 进程状态
  fetch('/api/control/status').then(r=>r.json()).then(d => {
    const rr=d.register, mm=d.mine;
    document.getElementById('reg-dot').className = 'proc-dot ' + (rr.running?'on':'off');
    document.getElementById('reg-status-text').textContent = rr.running ? '运行中 (PID: '+rr.pid+')' : '未运行';
    document.getElementById('btn-reg-start').disabled = rr.running;
    document.getElementById('btn-reg-stop').disabled = !rr.running;

    document.getElementById('mine-dot').className = 'proc-dot ' + (mm.running?'on':'off');
    document.getElementById('mine-status-text').textContent = mm.running ? '运行中 (PID: '+mm.pid+')' : '未运行';
    document.getElementById('btn-mine-start').disabled = mm.running;
    document.getElementById('btn-mine-stop').disabled = !mm.running;
  });

  // 注册统计
  fetch('/api/stats').then(r=>r.json()).then(d => {
    document.getElementById('r-total').textContent=d.total;
    document.getElementById('r-registered').textContent=d.registered;
    document.getElementById('r-pending').textContent=d.pending;
    document.getElementById('r-rate').textContent=d.success_rate;
    document.getElementById('update-time').textContent=d.updated_at;
    const pct=d.total>0?(d.registered/d.total*100):0;
    document.getElementById('r-fill').style.width=pct+'%';
    document.getElementById('r-progress-text').textContent=d.registered+' / '+d.total;
  });

  fetch('/api/accounts').then(r=>r.json()).then(data => {
    const tb=document.getElementById('r-accounts'); tb.innerHTML='';
    data.forEach((a,i)=>{const tr=document.createElement('tr');tr.innerHTML='<td>'+(i+1)+'</td><td class="wallet">'+a.wallet+'</td><td style="color:#7a8ba3">'+a.token+'</td><td class="key-masked">'+a.key+'</td>';tb.appendChild(tr);});
  });

  // 注册日志
  fetch('/api/reg_logs').then(r=>r.json()).then(data => { renderLogs(document.getElementById('r-logs'), data); });

  // 系统日志
  fetch('/api/logs').then(r=>r.json()).then(data => { renderLogs(document.getElementById('ctrl-logs'), data); });

  // 挖矿
  fetch('/api/mine').then(r=>r.json()).then(d => {
    document.getElementById('m-active').textContent=(d.active||0)+' / '+(d.total_accounts||0);
    document.getElementById('m-solved').textContent=d.total_solved||0;
    document.getElementById('m-submitted').textContent=d.total_submitted||0;
    document.getElementById('m-rewards').textContent=(d.total_rewards||0).toFixed(2);
    document.getElementById('m-errors').textContent=d.total_errors||0;
    document.getElementById('m-elapsed').textContent=fmtTime(d.elapsed_seconds||0);
    document.getElementById('m-updated').textContent=d.updated_at||'-';
    document.getElementById('m-badge').innerHTML=d.running?'<span class="badge badge-run">运行中</span>':'<span class="badge badge-stop">已停止</span>';

    const mt=document.getElementById('m-miners');mt.innerHTML='';
    (d.miners||[]).forEach(m=>{const tr=document.createElement('tr');const w=m.wallet.length>12?m.wallet.slice(0,6)+'...'+m.wallet.slice(-4):m.wallet;let st=m.status;let color='#ffa726';if(st.includes('求解')||st.includes('提交'))color='#00e676';else if(st.includes('运行'))color='#00e676';else if(st.includes('等待中'))color='#29b6f6';else if(st.includes('已提交'))color='#66bb6a';else if(st.includes('获取'))color='#ab47bc';else if(st.includes('停止')||st.includes('未注册')||st.includes('Gas'))color='#ef5350';else if(st.includes('异常'))color='#ff7043';st='<span style="color:'+color+'">'+st+'</span>';tr.innerHTML='<td class="wallet">'+w+'</td><td style="white-space:nowrap">'+st+'</td><td>'+m.solved+'</td><td>'+m.submitted+'</td><td style="color:#f06292">'+(m.rewards||0).toFixed(2)+'</td>';mt.appendChild(tr);});

    renderLogs(document.getElementById('m-logs'), d.logs||[]);
  });
}

// ═══ 导出功能 ═══
let _exportText = '';

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 2500);
}

function updateExportPreview() {
  const filter = document.getElementById('exp-filter').value;
  const fmt = document.getElementById('exp-format').value;
  fetch('/api/export?filter=' + filter + '&format=' + fmt).then(r => r.json()).then(d => {
    _exportText = d.text;
    const preview = document.getElementById('exp-preview');
    if (d.count > 0) {
      const lines = d.text.split('\n');
      const shown = lines.slice(0, 20).join('\n');
      preview.textContent = shown + (lines.length > 20 ? '\n... 共 ' + d.count + ' 条' : '');
      preview.classList.add('show');
    } else {
      preview.textContent = '（无匹配数据）';
      preview.classList.add('show');
    }
  });
}

function fetchExportStats() {
  fetch('/api/export/stats').then(r => r.json()).then(d => {
    document.getElementById('exp-stats').innerHTML =
      '<span class="export-stat">总计: <b>' + d.total + '</b></span>' +
      '<span class="export-stat" style="color:#ef5350">Gas不足: <b>' + d.gas_insufficient + '</b></span>' +
      '<span class="export-stat" style="color:#00e676">Gas正常: <b>' + d.gas_ok + '</b></span>';
  });
}

function doExportDownload() {
  const filter = document.getElementById('exp-filter').value;
  const fmt = document.getElementById('exp-format').value;
  window.open('/api/export?filter=' + filter + '&format=' + fmt + '&action=download', '_blank');
}

function doExportCopy() {
  const filter = document.getElementById('exp-filter').value;
  const fmt = document.getElementById('exp-format').value;
  fetch('/api/export?filter=' + filter + '&format=' + fmt).then(r => r.json()).then(d => {
    if (!d.text || d.count === 0) { showToast('无数据可复制'); return; }
    navigator.clipboard.writeText(d.text).then(() => {
      showToast('已复制 ' + d.count + ' 条到剪贴板');
    }).catch(() => {
      const ta = document.createElement('textarea');
      ta.value = d.text; document.body.appendChild(ta);
      ta.select(); document.execCommand('copy');
      document.body.removeChild(ta);
      showToast('已复制 ' + d.count + ' 条到剪贴板');
    });
  });
}

function queryGas() {
  const btn = document.getElementById('btn-query-gas');
  const statusText = document.getElementById('gas-status-text');
  btn.disabled = true;
  btn.textContent = '查询中...';
  statusText.textContent = '正在查询所有账号链上 ETH 余额，请稍候...';
  statusText.style.color = '#ffa726';

  fetch('/api/export/refresh', {method:'POST'}).then(r => r.json()).then(d => {
    btn.disabled = false;
    btn.textContent = '一键查询 Gas';
    if (d.ok) {
      statusText.textContent = d.message;
      statusText.style.color = '#00e676';
      document.getElementById('gas-summary').style.display = 'block';
      document.getElementById('gas-total').textContent = d.total;
      document.getElementById('gas-bad').textContent = d.gas_insufficient;
      document.getElementById('gas-good').textContent = d.gas_ok;
      document.getElementById('gas-time').textContent = d.query_time + 's';
      showToast(d.message);
      fetchExportStats();
      updateExportPreview();
      loadGasDetail();
    } else {
      statusText.textContent = '查询失败';
      statusText.style.color = '#ef5350';
      showToast('查询失败');
    }
  }).catch(() => {
    btn.disabled = false;
    btn.textContent = '一键查询 Gas';
    statusText.textContent = '请求失败，请检查网络';
    statusText.style.color = '#ef5350';
    showToast('请求失败');
  });
}

function loadGasDetail() {
  fetch('/api/export/gas_detail').then(r => r.json()).then(d => {
    if (!d.queried) return;
    const wrap = document.getElementById('gas-detail-wrap');
    const tbody = document.getElementById('gas-detail-body');
    wrap.style.display = 'block';
    tbody.innerHTML = '';
    d.accounts.forEach((a, i) => {
      const tr = document.createElement('tr');
      const w = a.wallet.length > 16 ? a.wallet.slice(0, 8) + '...' + a.wallet.slice(-6) : a.wallet;
      let ethStr = a.eth !== null ? a.eth.toFixed(6) : '查询失败';
      let statusStr, statusColor;
      if (a.status === 'insufficient') {
        statusStr = 'Gas 不足';
        statusColor = '#ef5350';
      } else if (a.status === 'ok') {
        statusStr = '正常';
        statusColor = '#00e676';
      } else {
        statusStr = '未知';
        statusColor = '#7a8ba3';
      }
      tr.innerHTML = '<td>' + (i+1) + '</td><td class="wallet" title="' + a.wallet + '">' + w + '</td><td style="color:' + (a.status === 'insufficient' ? '#ef5350' : '#e0e6ed') + '">' + ethStr + '</td><td style="color:' + statusColor + ';font-weight:600">' + statusStr + '</td>';
      tbody.appendChild(tr);
    });
  });
}

// 页面加载时检查是否已有缓存
function initGasPanel() {
  fetch('/api/export/stats').then(r => r.json()).then(d => {
    if (d.queried) {
      document.getElementById('gas-summary').style.display = 'block';
      document.getElementById('gas-total').textContent = d.total;
      document.getElementById('gas-bad').textContent = d.gas_insufficient;
      document.getElementById('gas-good').textContent = d.gas_ok;
      document.getElementById('gas-time').textContent = d.query_time + 's';
      document.getElementById('gas-status-text').textContent = '上次查询结果（点击按钮刷新）';
      document.getElementById('gas-status-text').style.color = '#7a8ba3';
      loadGasDetail();
    }
  });
}

fetchAll();
fetchExportStats();
initGasPanel();
setInterval(fetchAll, 3000);
setInterval(fetchExportStats, 15000);
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════
# Flask 路由
# ═══════════════════════════════════════════

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/stats")
def api_stats():
    return jsonify(_get_stats())

@app.route("/api/accounts")
def api_accounts():
    return jsonify(_load_registered())

@app.route("/api/logs")
def api_logs():
    with _logs_lock:
        return jsonify(list(_logs))

@app.route("/api/reg_logs")
def api_reg_logs():
    return jsonify(_read_reg_logs())

@app.route("/api/mine")
def api_mine():
    return jsonify(_get_mine_status())

@app.route("/api/control/status")
def api_control_status():
    return jsonify(_get_process_status())

@app.route("/api/control/register/start", methods=["POST"])
def api_register_start():
    data = request.get_json() or {}
    start = data.get("start", 0)
    count = data.get("count", 500)
    workers = data.get("workers", 3)
    return jsonify(_start_register(start, count, workers))

@app.route("/api/control/register/stop", methods=["POST"])
def api_register_stop():
    return jsonify(_stop_register())

@app.route("/api/control/mine/start", methods=["POST"])
def api_mine_start():
    data = request.get_json() or {}
    start = data.get("start", 0)
    count = data.get("count", 100)
    workers = data.get("workers", 5)
    return jsonify(_start_mine(start, count, workers))

@app.route("/api/control/mine/stop", methods=["POST"])
def api_mine_stop():
    return jsonify(_stop_mine())


@app.route("/api/export")
def api_export():
    """导出账号数据，支持筛选和格式选择"""
    filter_type = request.args.get("filter", "all")
    export_format = request.args.get("format", "address")
    action = request.args.get("action", "preview")

    text = _get_export_data(filter_type, export_format)
    count = len([l for l in text.splitlines() if l.strip()]) if text.strip() else 0

    if action == "download":
        fname = f"export_{filter_type}_{export_format}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        return Response(
            text,
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename={fname}"},
        )

    return jsonify({"count": count, "text": text})


@app.route("/api/export/stats")
def api_export_stats():
    """导出统计：各类别数量"""
    all_accounts = _load_registered_full()
    gas_bad = _get_gas_insufficient_wallets()

    total = len(all_accounts)
    insufficient = len([a for a in all_accounts if a["wallet"] in gas_bad])
    queried = _gas_cache is not None
    return jsonify({
        "total": total,
        "gas_insufficient": insufficient,
        "gas_ok": total - insufficient,
        "queried": queried,
        "query_time": _gas_query_elapsed if queried else 0,
    })


@app.route("/api/export/refresh", methods=["POST"])
def api_export_refresh():
    """一键查询所有账号 Gas 余额"""
    balances = _query_all_balances()
    all_accounts = _load_registered_full()
    total = len(all_accounts)
    insufficient = len([w for w, b in balances.items() if b < 0.0001])
    return jsonify({
        "ok": True,
        "total": total,
        "gas_insufficient": insufficient,
        "gas_ok": total - insufficient,
        "query_time": _gas_query_elapsed,
        "message": f"查询完成 ({_gas_query_elapsed}s)，共 {total} 个账号，Gas不足: {insufficient}，Gas正常: {total - insufficient}",
    })


@app.route("/api/export/gas_detail")
def api_gas_detail():
    """返回每个账号的 Gas 余额明细"""
    all_accounts = _load_registered_full()
    if _gas_cache is None:
        return jsonify({"queried": False, "accounts": []})

    result = []
    for acc in all_accounts:
        w = acc["wallet"]
        bal = _gas_cache.get(w, -1)
        result.append({
            "wallet": w,
            "eth": round(bal, 8) if bal >= 0 else None,
            "status": "insufficient" if 0 <= bal < 0.0001 else ("ok" if bal >= 0.0001 else "unknown"),
        })

    # 按余额排序：不足的在前
    result.sort(key=lambda x: (0 if x["status"] == "insufficient" else 1, x["eth"] or 0))
    return jsonify({"queried": True, "accounts": result, "query_time": _gas_query_elapsed})


# ═══════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════

def add_log(level: str, message: str):
    with _logs_lock:
        _logs.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        })


def _file_watcher():
    last_count = _count_lines(REGISTERED_FILE)
    while True:
        try:
            time.sleep(5)
            current = _count_lines(REGISTERED_FILE)
            if current > last_count:
                diff = current - last_count
                add_log("成功", f"新增 {diff} 个注册成功账号（累计 {current}）")
                last_count = current
        except Exception:
            pass


def start_web(host="0.0.0.0", port=8080):
    add_log("系统", "Web 控制面板启动")
    add_log("系统", f"监听 {host}:{port}")

    stats = _get_stats()
    add_log("系统", f"账号总数: {stats['total']} | 已注册: {stats['registered']}")

    watcher = threading.Thread(target=_file_watcher, daemon=True)
    watcher.start()

    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    start_web()
