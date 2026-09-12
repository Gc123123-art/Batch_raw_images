"""
用户路由 — 个人信息 / 余额 / 扣费记录
======================================
"""

from fastapi import APIRouter, Query

from app.db.database import get_user_by_id, get_user_deductions
from app.api.deps import current_user

router = APIRouter(tags=["user"])


@router.get("/api/user/profile")
def get_profile(user: dict = current_user):
    """获取用户信息"""
    return {
        "user_id": user["id"],
        "account": user["account"],
        "balance": user["balance"],
        "created_at": user["created_at"],
    }


@router.get("/api/user/balance")
def get_balance(user: dict = current_user):
    """获取余额"""
    fresh = get_user_by_id(user["id"])
    return {"balance": fresh["balance"]}


@router.get("/api/user/deductions")
def list_my_deductions(page: int = Query(1, ge=1),
                       page_size: int = Query(20, ge=1, le=100),
                       user: dict = current_user):
    """查询自己的扣费记录"""
    records = get_user_deductions(user["id"], page, page_size)
    return {"records": records, "page": page, "page_size": page_size}
