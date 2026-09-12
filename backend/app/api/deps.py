"""
API 依赖 — 认证与鉴权
======================
提供：
  - get_current_user：JWT 用户认证依赖
  - get_admin_key：管理员密钥依赖（X-Admin-Key header）
  - 登录/注册限流（内存版，防暴力破解）
"""

import threading
from datetime import datetime, timedelta

from fastapi import HTTPException, Header, Depends, Request

from app.core.security import decode_token
from app.core.secrets import get_or_create_secret
from app.core.config import LOGIN_MAX_ATTEMPTS, LOGIN_LOCK_MINUTES
from app.db.database import get_user_by_id

# 管理员密钥：环境变量 ADMIN_KEY 优先，否则自动生成持久化到 .secrets/admin_key
ADMIN_KEY = get_or_create_secret("ADMIN_KEY", "admin_key")

# 登录/注册限流（内存版）：同一 IP 连续失败 N 次后锁定一段时间
_login_attempts = {}  # key=ip -> {"fails": n, "locked_until": datetime, "window_start": datetime}
_login_attempts_lock = threading.Lock()


def client_ip(request: Request) -> str:
    """获取客户端 IP（兼容 Nginx 反代透传 X-Forwarded-For）"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_login_limit(request: Request):
    """检查是否被限流锁定，锁定则抛 429"""
    key = client_ip(request)
    with _login_attempts_lock:
        record = _login_attempts.get(key)
        now = datetime.now()
        if record and record.get("locked_until") and record["locked_until"] > now:
            remain_min = int((record["locked_until"] - now).total_seconds() // 60) + 1
            raise HTTPException(status_code=429,
                                detail=f"尝试次数过多，请 {remain_min} 分钟后重试")
        # 清理过期记录，防止内存无限增长
        if record and record["window_start"] < now - timedelta(hours=1):
            _login_attempts.pop(key, None)


def record_login_failure(request: Request):
    """记录一次失败尝试，达到阈值则锁定"""
    key = client_ip(request)
    now = datetime.now()
    with _login_attempts_lock:
        record = _login_attempts.get(key)
        if not record or record["window_start"] < now - timedelta(minutes=15):
            record = {"fails": 0, "locked_until": None, "window_start": now}
        record["fails"] += 1
        if record["fails"] >= LOGIN_MAX_ATTEMPTS:
            record["locked_until"] = now + timedelta(minutes=LOGIN_LOCK_MINUTES)
            record["fails"] = 0
        _login_attempts[key] = record


def clear_login_failures(request: Request):
    """登录成功后清除失败记录"""
    key = client_ip(request)
    with _login_attempts_lock:
        _login_attempts.pop(key, None)


def get_current_user(authorization: str = Header(None, alias="Authorization")):
    """从请求头解析当前用户"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未提供认证信息")

    # 支持 Authorization: Bearer xxx 和直接传 token
    token = authorization
    if token.startswith("Bearer "):
        token = token[7:]

    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    user = get_user_by_id(payload["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def get_admin_key(x_admin_key: str = Header(None, alias="X-Admin-Key")):
    """从 Header 取管理员密钥"""
    if not x_admin_key or x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="管理员密钥错误")
    return x_admin_key


# 供路由层复用的依赖别名（保持 FastAPI Depends 用法直观）
current_user = Depends(get_current_user)
admin_key = Depends(get_admin_key)
