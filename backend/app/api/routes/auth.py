"""
认证路由 — 注册 / 登录 / 修改密码
==================================
"""

import re

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from app.schemas.schemas import RegisterRequest, LoginRequest, ChangePasswordRequest
from app.core.security import hash_password, verify_password, create_token
from app.db.database import (
    create_user, get_user_by_account, get_user_by_id, change_password,
)
from app.api.deps import (
    check_login_limit, record_login_failure, clear_login_failures, current_user,
)

router = APIRouter(tags=["auth"])

_WHITESPACE_RE = re.compile(r"\s")


def normalize_account(account: str) -> str:
    """账号规范化：去掉首尾空白（内部含空白由调用方另行校验）"""
    return (account or "").strip()


@router.post("/api/auth/register")
def register(req: RegisterRequest, request: Request):
    """注册（账号区分大小写、不允许空格、不允许完全相同）"""
    check_login_limit(request)
    account = normalize_account(req.account)
    if not account or not req.password:
        raise HTTPException(status_code=400, detail="账号和密码不能为空")
    if _WHITESPACE_RE.search(account):
        raise HTTPException(status_code=400, detail="账号不能有空格")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")

    existing = get_user_by_account(account)
    if existing:
        record_login_failure(request)
        raise HTTPException(status_code=400, detail="该账号已注册")

    hashed = hash_password(req.password)
    try:
        user_id = create_user(account, hashed)
    except IntegrityError:
        # 并发注册兜底：数据库 UNIQUE 约束拦截，返回与查重一致的结果
        record_login_failure(request)
        raise HTTPException(status_code=400, detail="该账号已注册")
    token = create_token(user_id, account)

    return {"user_id": user_id, "account": account, "token": token,
            "balance": 0, "message": "注册成功，请联系管理员充值后使用"}


@router.post("/api/auth/login")
def login(req: LoginRequest, request: Request):
    """登录（账号区分大小写）"""
    check_login_limit(request)
    account = normalize_account(req.account)
    user = get_user_by_account(account)
    if not user or not verify_password(req.password, user["password"]):
        record_login_failure(request)
        raise HTTPException(status_code=401, detail="账号或密码错误")

    clear_login_failures(request)
    token = create_token(user["id"], user["account"])
    return {"user_id": user["id"], "account": user["account"],
            "token": token, "balance": user["balance"]}


@router.post("/api/user/change_password")
def change_my_password(req: ChangePasswordRequest, user: dict = current_user):
    """修改自己的密码"""
    if not verify_password(req.old_password, user["password"]):
        raise HTTPException(status_code=400, detail="原密码错误")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")

    new_hashed = hash_password(req.new_password)
    change_password(user["id"], new_hashed)
    return {"message": "密码修改成功"}
