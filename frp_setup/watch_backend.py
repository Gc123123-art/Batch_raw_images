"""
BOLI AI 后端守护脚本（Linux 云服务器专用）
============================================
功能：
1. 常驻运行 uvicorn（后台运行，日志写 /www/server/boli-watch/backend.log）
2. 每 2 秒检测 backend 代码 / config.env / .secrets / requirements.txt 变化，
   变化即自动重启后端（覆盖任何文件都立即生效）
3. 后端崩溃自动拉起
4. 改了 requirements.txt 时自动先 pip install 再重启
5. 自身不在监控范围（要重启本脚本需手动操作）

用法（手动启动测试）：
    /www/wwwroot/BoliAi/backend/venv/bin/python /www/server/boli-watch/watch_backend.py

生产环境由 systemd 服务 boli-backend-watch.service 拉起（开机自启 + 崩溃自愈）。
"""

import os
import time
import hashlib
import subprocess
import signal
import sys
from pathlib import Path

# ============================================================
# 配置（Linux 路径）
# ============================================================
BACKEND = Path("/www/wwwroot/BoliAi/backend")
VENV_PY = BACKEND / "venv" / "bin" / "python"
LOG_FILE = "/www/frp/boli-watch/backend.log"

# 监控范围：代码目录 + 配置文件
WATCH_DIRS = [BACKEND / "app"]
WATCH_FILES = [BACKEND / "config.env", BACKEND / "requirements.txt"]
SECRETS_DIR = BACKEND / ".secrets"
if SECRETS_DIR.exists():
    WATCH_FILES += list(SECRETS_DIR.glob("*"))

INTERVAL = 2  # 检测间隔（秒）


# ============================================================
# 文件指纹
# ============================================================
def _hash(path: Path) -> str:
    try:
        return hashlib.md5(path.read_bytes()).hexdigest()
    except OSError:
        return "error"


def snapshot() -> dict:
    """返回 {路径: 内容指纹}，用于检测变化"""
    hashes = {}
    for d in WATCH_DIRS:
        if d.exists():
            for p in d.rglob("*.py"):
                hashes[str(p)] = _hash(p)
    for f in WATCH_FILES:
        if f.exists():
            hashes[str(f)] = _hash(f)
    return hashes


# ============================================================
# 后端进程管理
# ============================================================
def start_backend(log_handle):
    print(f"[{time.strftime('%H:%M:%S')}] 启动后端...")
    return subprocess.Popen(
        [str(VENV_PY), "-m", "uvicorn", "app.main:app",
         "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(BACKEND),
        stdout=log_handle,
        stderr=log_handle,
        # Linux 下没有 CREATE_NO_WINDOW；用 start_new_session 把进程放到新会话
        start_new_session=True,
    )


def install_requirements():
    print(f"[{time.strftime('%H:%M:%S')}] requirements.txt 变化，先安装依赖...")
    subprocess.run(
        [str(VENV_PY), "-m", "pip", "install", "-r", str(BACKEND / "requirements.txt")],
        cwd=str(BACKEND),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def shutdown_handler(signum, frame):
    """systemd 停止时优雅退出，杀掉子进程"""
    print(f"[{time.strftime('%H:%M:%S')}] 收到停止信号 {signum}，退出...")
    sys.exit(0)


def main():
    # 注册信号处理（systemd stop 时会发 SIGTERM）
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    # 确保日志目录存在
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

    log = open(LOG_FILE, "ab", buffering=0)
    proc = start_backend(log)
    prev = snapshot()

    while True:
        time.sleep(INTERVAL)

        cur = snapshot()
        changed = [k for k in cur if prev.get(k) != cur[k]] + \
                  [k for k in prev if k not in cur]
        prev = cur

        if changed:
            print(f"[{time.strftime('%H:%M:%S')}] 检测到文件变化: {changed[:3]}{'...' if len(changed) > 3 else ''}")
            if str(BACKEND / "requirements.txt") in changed:
                install_requirements()
            print(f"[{time.strftime('%H:%M:%S')}] 自动重启后端...")
            # Linux 下用 SIGTERM 优雅杀掉整个进程组
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc = start_backend(log)
            continue

        if proc.poll() is not None:
            print(f"[{time.strftime('%H:%M:%S')}] 后端进程退出(code={proc.returncode})，自动拉起...")
            proc = start_backend(log)


if __name__ == "__main__":
    main()
