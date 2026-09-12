"""
数据库模块 — SQLAlchemy 多方言（PostgreSQL / MySQL 兼容）
==========================================================
提供用户、任务、扣费记录的增删改查。

连接目标由 DATABASE_URL 环境变量决定（config.env 中配置）：
    - postgresql+psycopg2://… → PostgreSQL（推荐生产环境）
    - mysql+pymysql://… → MySQL

所有函数返回普通 dict / 列表，调用方无需感知底层方言。
"""

import threading
from contextlib import contextmanager
from datetime import datetime

from dotenv import dotenv_values
from sqlalchemy import create_engine

from app.core.config import DATABASE_URL, BACKEND_DIR, provider_env_files

# ============================================================
# 方言识别与引擎
# ============================================================
def _detect_dialect(url: str) -> str:
    scheme = (url.split(":", 1)[0] or "").lower()
    if scheme.startswith("postgres"):
        return "postgresql"
    if scheme.startswith("mysql"):
        return "mysql"
    raise ValueError("DATABASE_URL 必须为 postgresql:// 或 mysql:// 连接串，请检查 config.env")


_dialect = _detect_dialect(DATABASE_URL)
_engine = None
_engine_lock = threading.Lock()


def _get_engine():
    """惰性创建 SQLAlchemy 引擎（按 DATABASE_URL）"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return _engine


def _q(sql: str) -> str:
    """占位符适配：PostgreSQL / MySQL 驱动统一使用 %s"""
    return sql.replace("?", "%s")


def _now() -> str:
    """统一的本地时间字符串（YYYY-MM-DD HH:MM:SS），由代码统一写入，跨方言一致"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_all(sql: str, params=()):
    with _get_engine().connect() as conn:
        rows = conn.exec_driver_sql(_q(sql), params).mappings().all()
    return [dict(r) for r in rows]


def _read_one(sql: str, params=()):
    with _get_engine().connect() as conn:
        row = conn.exec_driver_sql(_q(sql), params).mappings().first()
    return dict(row) if row else None


def _insert_id(conn, sql: str, params=()):
    """执行 INSERT 并返回自增 id（PostgreSQL 通过 RETURNING id 实现）"""
    stmt = _q(sql)
    if _dialect == "postgresql":
        stmt += " RETURNING id"
        return conn.exec_driver_sql(stmt, params).scalar()
    return conn.exec_driver_sql(stmt, params).lastrowid


def get_conn():
    """获取一个短生命周期读连接（池化自动回收，调用方用完即弃）"""
    return _get_engine().connect()


@contextmanager
def get_write_conn():
    """获取写连接：正常退出自动提交，异常自动回滚"""
    with _get_engine().begin() as conn:
        yield conn


# ============================================================
# 建表 DDL（按方言区分）
# ============================================================
# 时间列不设 DB 默认值，由代码统一写入 _now()，保证跨方言一致
_POSTGRES_DDL = [
    """CREATE TABLE IF NOT EXISTS users (
        id          SERIAL PRIMARY KEY,
        account     TEXT    UNIQUE NOT NULL,
        password    TEXT    NOT NULL,
        balance     INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT    NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tasks (
        id              SERIAL PRIMARY KEY,
        user_id         INTEGER NOT NULL REFERENCES users(id),
        prompt          TEXT    NOT NULL,
        reference_image TEXT    DEFAULT '',
        shared_reference_image TEXT DEFAULT '',
        size            TEXT    DEFAULT '1024x1024',
        n               INTEGER DEFAULT 1,
        total_count     INTEGER DEFAULT 1,
        completed_count INTEGER DEFAULT 0,
        status          TEXT    DEFAULT 'pending',
        amount          INTEGER NOT NULL DEFAULT 0,
        remark          TEXT    DEFAULT '',
        batch           INTEGER NOT NULL DEFAULT 0,
        updated_at      TEXT    NOT NULL,
        created_at      TEXT    NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS task_results (
        id          SERIAL PRIMARY KEY,
        task_id     INTEGER NOT NULL REFERENCES tasks(id),
        seq         INTEGER DEFAULT 1,
        image_path  TEXT    DEFAULT '',
        image_url   TEXT    DEFAULT '',
        prompt      TEXT    DEFAULT '',
        status      TEXT    DEFAULT 'pending',
        error       TEXT    DEFAULT '',
        created_at  TEXT    NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS api_providers (
        id          SERIAL PRIMARY KEY,
        name        TEXT    UNIQUE NOT NULL,
        api_key     TEXT    NOT NULL,
        base_url    TEXT    NOT NULL,
        model       TEXT    DEFAULT '',
        enabled     INTEGER DEFAULT 1,
        priority    INTEGER DEFAULT 0,
        created_at  TEXT    NOT NULL,
        updated_at  TEXT    NOT NULL
    )""",
]

# MySQL（遗留兼容：如仍在使用 5.5，TEXT 列不支持 DEFAULT，故正文列用 VARCHAR）
_MYSQL_DDL = [
    """CREATE TABLE IF NOT EXISTS users (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        account     VARCHAR(255) UNIQUE NOT NULL,
        password    VARCHAR(255) NOT NULL,
        balance     INT NOT NULL DEFAULT 0,
        created_at  VARCHAR(32) NOT NULL
    ) DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS tasks (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        user_id         INT NOT NULL,
        prompt          VARCHAR(4000) NOT NULL,
        reference_image VARCHAR(500) DEFAULT '',
        shared_reference_image VARCHAR(500) DEFAULT '',
        size            VARCHAR(32) DEFAULT '1024x1024',
        n               INT DEFAULT 1,
        total_count     INT DEFAULT 1,
        completed_count INT DEFAULT 0,
        status          VARCHAR(32) DEFAULT 'pending',
        amount          INT NOT NULL DEFAULT 0,
        remark          VARCHAR(500) DEFAULT '',
        batch           INT NOT NULL DEFAULT 0,
        updated_at      VARCHAR(32) NOT NULL,
        created_at      VARCHAR(32) NOT NULL
    ) DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS task_results (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        task_id     INT NOT NULL,
        seq         INT DEFAULT 1,
        image_path  VARCHAR(500) DEFAULT '',
        image_url   VARCHAR(500) DEFAULT '',
        prompt      VARCHAR(4000) DEFAULT '',
        status      VARCHAR(32) DEFAULT 'pending',
        error       VARCHAR(1000) DEFAULT '',
        created_at  VARCHAR(32) NOT NULL
    ) DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS api_providers (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        name        VARCHAR(255) UNIQUE NOT NULL,
        api_key     VARCHAR(500) NOT NULL,
        base_url    VARCHAR(500) NOT NULL,
        model       VARCHAR(255) DEFAULT '',
        enabled     INT DEFAULT 1,
        priority    INT DEFAULT 0,
        created_at  VARCHAR(32) NOT NULL,
        updated_at  VARCHAR(32) NOT NULL
    ) DEFAULT CHARSET=utf8mb4""",
]

_DDL = {
    "postgresql": _POSTGRES_DDL,
    "mysql": _MYSQL_DDL,
}

# 存量表补列迁移（列已存在时报错忽略，保证幂等）
_MIGRATIONS = {
    "postgresql": [
        "ALTER TABLE tasks ADD COLUMN shared_reference_image TEXT DEFAULT ''",
    ],
    "mysql": [
        "ALTER TABLE tasks ADD COLUMN shared_reference_image VARCHAR(500) DEFAULT ''",
    ],
}


def init_db():
    """初始化数据库表"""
    with get_write_conn() as conn:
        for stmt in _DDL[_dialect]:
            conn.exec_driver_sql(stmt)
        for stmt in _MIGRATIONS[_dialect]:
            try:
                conn.exec_driver_sql(stmt)
            except Exception:
                pass  # 列已存在
        _seed_providers_from_env(conn)


# ============================================================
# 供应商初始化
# ============================================================
def _seed_providers_from_env(conn):
    """首次迁移：api_providers 表为空时，从 config.env / config-*.env 导入供应商。

    仅执行一次（表已有数据时跳过），之后供应商改由数据库管理。
    归并规则与 engine._load_providers 一致：同 API_KEY 合并，config.env 优先。
    """
    if conn.exec_driver_sql("SELECT COUNT(*) FROM api_providers").scalar() > 0:
        return

    grouped = {}  # api_key -> {"name": 显示名, "cfg": 合并后的配置}
    order = []

    def _collect(name: str, cfg: dict):
        key = (cfg.get("API_KEY") or "").strip()
        if not key.startswith("sk-"):
            return
        if key not in grouped:
            grouped[key] = {"name": name, "cfg": {}}
            order.append(key)
        existing = grouped[key]["cfg"]
        for k, v in cfg.items():
            if v and k not in existing:
                existing[k] = v

    _collect("config.env", dotenv_values(BACKEND_DIR / "config.env"))
    for env_file in provider_env_files():
        name = env_file.stem.replace("config-", "", 1)
        _collect(name, dotenv_values(env_file))

    now = _now()
    for priority, key in enumerate(order):
        entry = grouped[key]
        cfg = entry["cfg"]
        conn.exec_driver_sql(
            _q("""INSERT INTO api_providers
               (name, api_key, base_url, model, priority, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)"""),
            (
                (cfg.get("SUPPLIER_NAME") or "").strip() or entry["name"],
                key,
                cfg.get("BASE_URL", ""),
                cfg.get("DEFAULT_MODEL", "") or "",
                priority,
                now,
                now,
            ),
        )


# ============================================================
# 用户操作
# ============================================================
def create_user(account: str, hashed_password: str) -> int:
    """创建用户，返回 user_id（新用户默认 0 余额，需管理员充值）"""
    with get_write_conn() as conn:
        return _insert_id(
            conn,
            "INSERT INTO users (account, password, balance, created_at) VALUES (?, ?, 0, ?)",
            (account, hashed_password, _now()),
        )


def get_user_by_account(account: str) -> dict:
    """通过账号查找用户"""
    return _read_one("SELECT * FROM users WHERE account = ?", (account,))


def get_user_by_id(user_id: int) -> dict:
    """通过 ID 查找用户"""
    return _read_one("SELECT * FROM users WHERE id = ?", (user_id,))


def deduct_balance(user_id: int, amount: int) -> bool:
    """扣减用户余额，返回是否成功（原子条件更新，避免并发超扣）"""
    with get_write_conn() as conn:
        cur = conn.exec_driver_sql(
            _q("UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?"),
            (amount, user_id, amount),
        )
        return cur.rowcount > 0


def add_balance(user_id: int, amount: int):
    """增加用户余额（管理员充值 / 失败退还）"""
    with get_write_conn() as conn:
        conn.exec_driver_sql(
            _q("UPDATE users SET balance = balance + ? WHERE id = ?"),
            (amount, user_id),
        )


def delete_user(user_id: int):
    """删除用户及其所有关联数据"""
    with get_write_conn() as conn:
        conn.exec_driver_sql(_q("DELETE FROM task_results WHERE task_id IN (SELECT id FROM tasks WHERE user_id = ?)"), (user_id,))
        conn.exec_driver_sql(_q("DELETE FROM tasks WHERE user_id = ?"), (user_id,))
        conn.exec_driver_sql(_q("DELETE FROM users WHERE id = ?"), (user_id,))


def set_balance(user_id: int, new_balance: int):
    """直接设置用户余额（管理员操作）"""
    with get_write_conn() as conn:
        conn.exec_driver_sql(
            _q("UPDATE users SET balance = ? WHERE id = ?"),
            (new_balance, user_id),
        )


def change_password(user_id: int, new_hashed: str):
    """更新用户密码"""
    with get_write_conn() as conn:
        conn.exec_driver_sql(
            _q("UPDATE users SET password = ? WHERE id = ?"),
            (new_hashed, user_id),
        )


# ============================================================
# 任务操作
# ============================================================
def create_task(user_id: int, prompt: str, reference_image: str = "",
                shared_reference_image: str = "",
                size: str = "1024x1024", n: int = 1,
                total_count: int = 1, batch: int = 0) -> int:
    """创建任务，返回 task_id（batch=1 表示批量模式，每张参考图独立生成；
    shared_reference_image 为批量模式下附加到每张批量图的共用参考图）"""
    now = _now()
    with get_write_conn() as conn:
        task_id = _insert_id(
            conn,
            """INSERT INTO tasks
               (user_id, prompt, reference_image, shared_reference_image,
                size, n, total_count, status, batch, updated_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (user_id, prompt, reference_image, shared_reference_image,
             size, n, total_count, batch, now, now),
        )
        for seq in range(1, total_count + 1):
            conn.exec_driver_sql(
                _q("INSERT INTO task_results (task_id, seq, status, created_at) VALUES (?, ?, 'pending', ?)"),
                (task_id, seq, now),
            )
        return task_id


def get_user_tasks(user_id: int, page: int = 1, page_size: int = 20) -> list:
    """获取用户的任务列表（过滤掉全部失败的任务）"""
    offset = (page - 1) * page_size
    return _read_all(
        """SELECT * FROM tasks
           WHERE user_id = ? AND status != 'failed'
           ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        (user_id, page_size, offset),
    )


def get_task_by_id(task_id: int) -> dict:
    """获取任务详情"""
    return _read_one("SELECT * FROM tasks WHERE id = ?", (task_id,))


def get_task_results(task_id: int) -> list:
    """获取任务的生成结果列表"""
    return _read_all(
        "SELECT * FROM task_results WHERE task_id = ? ORDER BY seq",
        (task_id,),
    )


def get_task_result_by_seq(task_id: int, seq: int) -> dict:
    """按 task_id + seq 单条查询（消除 N+1：单张图只查一行）"""
    return _read_one(
        "SELECT * FROM task_results WHERE task_id = ? AND seq = ?",
        (task_id, seq),
    )


def update_task_progress(task_id: int, completed: int, status: str = "running"):
    """更新任务进度（同时刷新 updated_at，供看门狗判断任务是否停滞）"""
    with get_write_conn() as conn:
        conn.exec_driver_sql(
            _q("""UPDATE tasks SET completed_count = ?, status = ?, updated_at = ? WHERE id = ?"""),
            (completed, status, _now(), task_id),
        )


def update_task_result(task_id: int, seq: int, status: str,
                       image_path: str = "", image_url: str = "",
                       error: str = "", prompt: str = ""):
    """更新子任务结果"""
    with get_write_conn() as conn:
        conn.exec_driver_sql(
            _q("""UPDATE task_results SET status = ?, image_path = ?, image_url = ?,
               error = ?, prompt = ? WHERE task_id = ? AND seq = ?"""),
            (status, image_path, image_url, error, prompt, task_id, seq),
        )


def list_incomplete_tasks() -> list:
    """获取所有未完成任务（running / pending），服务启动时恢复执行用"""
    return _read_all(
        "SELECT * FROM tasks WHERE status IN ('running', 'pending') ORDER BY id"
    )


def list_stalled_running_tasks(stall_seconds: int) -> list:
    """获取停滞超时的 running 任务（超过 stall_seconds 无进度更新），看门狗用

    时间字段统一为本地时间字符串（YYYY-MM-DD HH:MM:SS），在 Python 侧比较，
    避免方言相关的日期函数差异。
    """
    rows = _read_all(
        "SELECT * FROM tasks WHERE status = 'running' AND updated_at IS NOT NULL AND updated_at != ''"
    )
    now = datetime.now()
    stalled = []
    for r in rows:
        try:
            ts = datetime.strptime(r["updated_at"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        if (now - ts).total_seconds() > stall_seconds:
            stalled.append(r)
    return stalled


# ============================================================
# 扣费记录（写入任务表 amount/remark，支持负数退款）
# ============================================================
def record_deduction(user_id: int, task_id: int, amount: int, remark: str = ""):
    """记录扣费（累加到任务的 amount 上）"""
    with get_write_conn() as conn:
        conn.exec_driver_sql(
            _q("UPDATE tasks SET amount = COALESCE(amount, 0) + ?, remark = ? WHERE id = ?"),
            (amount, remark, task_id),
        )


def get_user_deductions(user_id: int, page: int = 1, page_size: int = 20) -> list:
    """获取用户扣费记录（从任务表读取有扣费/退款的任务）"""
    offset = (page - 1) * page_size
    return _read_all(
        """SELECT id AS task_id, user_id, amount, remark, created_at
           FROM tasks WHERE user_id = ? AND amount != 0
           ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        (user_id, page_size, offset),
    )


def list_all_users() -> list:
    """获取全部用户（管理员用）"""
    return _read_all(
        "SELECT id, account, balance, created_at FROM users ORDER BY id"
    )


# ============================================================
# API 供应商操作（数据库管理，替代 config-*.env 文件）
# ============================================================
def list_api_providers() -> list:
    """获取全部供应商（按 priority 升序，管理员用）"""
    return _read_all("SELECT * FROM api_providers ORDER BY priority, id")


def list_enabled_providers() -> list:
    """获取启用的供应商（引擎调用用）"""
    return _read_all("SELECT * FROM api_providers WHERE enabled = 1 ORDER BY priority, id")


def get_api_provider(provider_id: int) -> dict:
    return _read_one("SELECT * FROM api_providers WHERE id = ?", (provider_id,))


def create_api_provider(name: str, api_key: str, base_url: str, model: str = "",
                        enabled: int = 1, priority: int = 0) -> int:
    """新增供应商，返回 id"""
    now = _now()
    with get_write_conn() as conn:
        return _insert_id(
            conn,
            """INSERT INTO api_providers
               (name, api_key, base_url, model, enabled, priority, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, api_key, base_url, model, enabled, priority, now, now),
        )


def update_api_provider(provider_id: int, name: str, api_key: str, base_url: str,
                        model: str = "", enabled: int = 1, priority: int = 0):
    """更新供应商信息"""
    with get_write_conn() as conn:
        conn.exec_driver_sql(
            _q("""UPDATE api_providers SET name = ?, api_key = ?, base_url = ?,
               model = ?, enabled = ?, priority = ?, updated_at = ? WHERE id = ?"""),
            (name, api_key, base_url, model, enabled, priority, _now(), provider_id),
        )


def delete_api_provider(provider_id: int):
    """删除供应商"""
    with get_write_conn() as conn:
        conn.exec_driver_sql(_q("DELETE FROM api_providers WHERE id = ?"), (provider_id,))


def set_api_provider_enabled(provider_id: int, enabled: int):
    """启用/停用供应商"""
    with get_write_conn() as conn:
        conn.exec_driver_sql(
            _q("UPDATE api_providers SET enabled = ?, updated_at = ? WHERE id = ?"),
            (1 if enabled else 0, _now(), provider_id),
        )
