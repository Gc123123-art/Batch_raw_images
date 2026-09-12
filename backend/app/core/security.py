"""
安全模块 — 密码哈希、JWT、预览签名
====================================
提供 bcrypt 密码加密、JWT token 签发与验证、上传预览 URL 的
HMAC 短时效签名（避免把登录 token 放进 URL/日志）。
"""

import os
import time
import hmac
import hashlib
from datetime import datetime, timedelta

import jwt
import bcrypt as bcrypt_lib

from app.core.secrets import get_or_create_secret
from app.core.config import BACKEND_DIR
from dotenv import load_dotenv

load_dotenv(BACKEND_DIR / "config.env", override=False)

# JWT 签名密钥：环境变量优先，否则自动生成并持久化到 .secrets/jwt_secret
JWT_SECRET = get_or_create_secret("JWT_SECRET", "jwt_secret")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24


# ============================================================
# 密码加密
# ============================================================
def hash_password(password: str) -> str:
    """bcrypt 加密密码"""
    return bcrypt_lib.hashpw(password.encode("utf-8"), bcrypt_lib.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """验证密码"""
    return bcrypt_lib.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ============================================================
# JWT
# ============================================================
def create_token(user_id: int, account: str) -> str:
    """生成 JWT token"""
    payload = {
        "user_id": user_id,
        "account": account,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """解析 JWT token，返回 payload 或 None"""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ============================================================
# 上传预览签名（短时效，不携带登录凭证）
# ============================================================
def sign_preview_url(rel_path: str, ttl: int = 3600) -> str:
    """生成带 HMAC 签名的上传文件预览 URL。

    签名 = HMAC-SHA256(JWT_SECRET, "path|expires")，URL 中不带登录 token，
    过期即失效；即使泄漏也仅能访问这一个文件、且限时有效。
    """
    from urllib.parse import quote
    expires = int(time.time()) + ttl
    msg = f"{rel_path}|{expires}"
    sig = hmac.new(JWT_SECRET.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"/api/upload-preview?path={quote(rel_path)}&expires={expires}&sig={sig}"


def verify_preview_signature(path: str, expires: int, sig: str) -> bool:
    """校验预览 URL 签名与有效期"""
    try:
        expires = int(expires)
    except (TypeError, ValueError):
        return False
    if int(time.time()) > expires:
        return False
    msg = f"{path}|{expires}"
    expected = hmac.new(JWT_SECRET.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, (sig or "").lower())
