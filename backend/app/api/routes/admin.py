"""
管理路由 — 用户管理 / 充值 / 额度设置 / 供应商管理（X-Admin-Key 鉴权）
========================================================================
"""

from fastapi import APIRouter, HTTPException

from app.schemas.schemas import (
    RechargeRequest, UpdateBalanceRequest, DeleteUserRequest, ResetPasswordRequest,
    SetUserChangeRequest,
    ProviderCreateRequest, ProviderUpdateRequest,
    ProviderToggleRequest, ProviderDeleteRequest,
)
from app.db.database import (
    get_user_by_id, add_balance, set_balance, delete_user, list_all_users,
    change_password, set_user_change,
    list_api_providers, get_api_provider, create_api_provider,
    update_api_provider, delete_api_provider, set_api_provider_enabled,
)
from app.api.deps import admin_key
from app.core.security import hash_password

router = APIRouter(tags=["admin"])


@router.get("/api/admin/users")
def admin_list_users(_: str = admin_key):
    """管理员查看所有用户"""
    return {"users": list_all_users()}


@router.post("/api/admin/recharge")
def recharge(req: RechargeRequest, _: str = admin_key):
    """管理员为客户充值（增加余额）"""
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="充值金额必须大于 0")
    user = get_user_by_id(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    add_balance(req.user_id, req.amount)
    fresh = get_user_by_id(req.user_id)
    return {"user_id": req.user_id, "amount": req.amount,
            "new_balance": fresh["balance"]}


@router.post("/api/admin/set_balance")
def admin_set_balance(req: UpdateBalanceRequest, _: str = admin_key):
    """管理员直接设置用户余额"""
    user = get_user_by_id(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if req.balance < 0:
        raise HTTPException(status_code=400, detail="余额不能为负数")

    set_balance(req.user_id, req.balance)
    return {"user_id": req.user_id, "new_balance": req.balance}


@router.post("/api/admin/delete_user")
def admin_delete_user(req: DeleteUserRequest, _: str = admin_key):
    """管理员删除用户（同时删除其任务和扣费记录）"""
    user = get_user_by_id(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    account = user["account"]
    delete_user(req.user_id)
    return {"message": f"用户 {account} 及其所有数据已删除"}


@router.post("/api/admin/reset_password")
def admin_reset_password(req: ResetPasswordRequest, _: str = admin_key):
    """管理员重置用户密码（bcrypt 哈希后写库，新密码立即生效）

    用途：用户忘记密码时，管理员用此接口设置一个临时密码，
    用户登录后通过 /api/user/change_password 自己改回。
    """
    user = get_user_by_id(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码长度不能少于 6 位")

    change_password(req.user_id, hash_password(req.new_password))
    return {"message": f"用户 {user['account']} 的密码已重置", "user_id": req.user_id}


@router.post("/api/admin/set_user_change")
def admin_set_user_change(req: SetUserChangeRequest, _: str = admin_key):
    """设置用户分组数字（users.change 与 api_providers.change 相等才匹配使用）"""
    user = get_user_by_id(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if req.change < 0:
        raise HTTPException(status_code=400, detail="change 不能为负数")

    set_user_change(req.user_id, req.change)
    return {"user_id": req.user_id, "change": req.change,
            "message": f"用户 {user['account']} 分组已设为 {req.change}"}


# ============================================================
# API 供应商管理（数据库管理，替代 config-*.env 文件）
# ============================================================

def _mask_key(key: str) -> str:
    """脱敏：只保留前 8 位和后 4 位"""
    if len(key) <= 12:
        return key
    return f"{key[:8]}...{key[-4:]}"


def _public_provider(row: dict) -> dict:
    """返回给管理员的供应商信息（key 脱敏）"""
    return {
        "id": row["id"],
        "name": row["name"],
        "api_key_masked": _mask_key(row["api_key"]),
        "base_url": row["base_url"],
        "model": row["model"],
        "enabled": row["enabled"],
        "priority": row["priority"],
        "change": int(row.get("change", 0)),
    }


@router.get("/api/admin/providers")
def admin_list_providers(_: str = admin_key):
    """管理员查看所有 API 供应商"""
    return {"providers": [_public_provider(p) for p in list_api_providers()]}


@router.post("/api/admin/providers")
def admin_create_provider(req: ProviderCreateRequest, _: str = admin_key):
    """新增 API 供应商"""
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="供应商名称不能为空")
    if not req.api_key.startswith("sk-"):
        raise HTTPException(status_code=400, detail="API Key 必须以 sk- 开头")
    if not req.base_url.strip():
        raise HTTPException(status_code=400, detail="接口地址不能为空")

    pid = create_api_provider(
        name=req.name.strip(),
        api_key=req.api_key.strip(),
        base_url=req.base_url.strip(),
        model=req.model.strip(),
        enabled=req.enabled,
        priority=req.priority,
        change=req.change,
    )
    return {"message": "供应商已创建", "provider_id": pid}


@router.put("/api/admin/providers/{provider_id}")
def admin_update_provider(provider_id: int, req: ProviderUpdateRequest,
                          _: str = admin_key):
    """更新 API 供应商"""
    if not get_api_provider(provider_id):
        raise HTTPException(status_code=404, detail="供应商不存在")
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="供应商名称不能为空")
    if not req.api_key.startswith("sk-"):
        raise HTTPException(status_code=400, detail="API Key 必须以 sk- 开头")
    if not req.base_url.strip():
        raise HTTPException(status_code=400, detail="接口地址不能为空")

    update_api_provider(
        provider_id=provider_id,
        name=req.name.strip(),
        api_key=req.api_key.strip(),
        base_url=req.base_url.strip(),
        model=req.model.strip(),
        enabled=req.enabled,
        priority=req.priority,
        change=req.change,
    )
    return {"message": "供应商已更新", "provider_id": provider_id}


@router.patch("/api/admin/providers/{provider_id}/enabled")
def admin_toggle_provider(provider_id: int, req: ProviderToggleRequest,
                          _: str = admin_key):
    """启用 / 停用 API 供应商"""
    if not get_api_provider(provider_id):
        raise HTTPException(status_code=404, detail="供应商不存在")
    set_api_provider_enabled(provider_id, 1 if req.enabled else 0)
    return {"message": "供应商已启用" if req.enabled else "供应商已停用",
            "provider_id": provider_id}


@router.delete("/api/admin/providers/{provider_id}")
def admin_delete_provider(provider_id: int, _: str = admin_key):
    """删除 API 供应商"""
    if not get_api_provider(provider_id):
        raise HTTPException(status_code=404, detail="供应商不存在")
    delete_api_provider(provider_id)
    return {"message": "供应商已删除", "provider_id": provider_id}
