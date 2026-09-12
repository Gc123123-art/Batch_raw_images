"""
认证路由 — 注册 / 登录 / 修改密码
==================================
"""

from fastapi import APIRouter, HTTPException, Request

from app.schemas.schemas import RegisterRequest, LoginRequest, ChangePasswordRequest
from app.core.security import hash_password, verify_password, create_token
from app.db.database import (
    create_user, get_user_by_account, get_user_by_id, change_password,
)
from app.api.deps import (
    check_login_limit, record_login_failure, clear_login_failures, current_user,
)

router = APIRouter(tags=["auth"])


@router.post("/api/auth/register")
def register(req: RegisterRequest, request: Request):
    """注册"""
    check_login_limit(request)
    if not req.account or not req.password:
        raise HTTPException(status_code=400, detail="账号和密码不能为空")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")

    existing = get_user_by_account(req.account)
    if existing:
        record_login_failure(request)
        raise HTTPException(status_code=400, detail="该账号已注册")

    hashed = hash_password(req.password)
    user_id = create_user(req.account, hashed)
    token = create_token(user_id, req.account)

    return {"user_id": user_id, "account": req.account, "token": token,
            "balance": 0, "message": "注册成功，请联系管理员充值后使用"}


@router.post("/api/auth/login")
def login(req: LoginRequest, request: Request):
    """登录"""
    check_login_limit(request)
    user = get_user_by_account(req.account)
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
