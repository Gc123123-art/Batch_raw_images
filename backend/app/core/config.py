"""
应用配置模块
=============
统一管理目录路径、环境变量与运行参数。

所有模块都应从本模块读取路径与配置，避免硬编码相对路径（防止
依赖启动时的工作目录导致找不到 config.env 等问题）。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ============================================================
# 目录结构（基于文件绝对位置推导，不依赖 cwd）
# ============================================================
# backend/ 目录（config.py 位于 backend/app/core/ 下，向上三级）
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
# 项目根（Batch_raw_images/）
PROJECT_ROOT = BACKEND_DIR.parent
# 数据目录：上传文件、运行时状态统一存放
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"        # 用户上传的参考图
API_MEMORY_FILE = DATA_DIR / ".api_memory.json"  # API 供应商记忆
SECRETS_DIR = BACKEND_DIR / ".secrets"   # 自动生成的密钥落盘目录

for _d in (DATA_DIR, UPLOAD_DIR, SECRETS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ============================================================
# 环境变量加载
# 生产环境通过系统环境变量 / systemd EnvironmentFile 注入；
# config.env 仅用于本地开发兜底（override=False，环境变量优先）。
# ============================================================
# 记录 load_dotenv 之前系统环境已有的 key（即"真正的系统注入"），
# 供 engine 判断哪些环境变量可以覆盖 config-*.env 中的值。
_SYSTEM_ENV_KEYS = frozenset(os.environ.keys())
load_dotenv(BACKEND_DIR / "config.env", override=False)


def provider_env_files() -> list:
    """多供应商配置文件：backend/ 下 config-*.env，按文件名排序"""
    return sorted(BACKEND_DIR.glob("config-*.env"), key=lambda p: p.name)


# ============================================================
# 业务常量
# ============================================================
COST_PER_IMAGE = 1  # 每张图扣费次数

# 上传预览签名有效期（秒）：参考图删除后链接自动失效
PREVIEW_SIG_TTL = int(os.getenv("PREVIEW_SIG_TTL", "86400"))

# 任务停滞超时（秒）：running 任务超过该时长无进度更新，视为卡死，由看门狗标记失败并退款
TASK_STALL_TIMEOUT = int(os.getenv("TASK_STALL_TIMEOUT", "900"))

# CORS 允许来源（生产环境通过 CORS_ORIGINS 环境变量配置）
CORS_ORIGINS = [
    o.strip() for o in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:8001,http://127.0.0.1:8001",
    ).split(",") if o.strip()
]

# 数据库连接串（必填，PostgreSQL / MySQL）
# 例如 postgresql+psycopg2://... / mysql+pymysql://...；在 config.env 中配置
DATABASE_URL = os.getenv("DATABASE_URL", "")

# 登录/注册限流
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCK_MINUTES = int(os.getenv("LOGIN_LOCK_MINUTES", "5"))
