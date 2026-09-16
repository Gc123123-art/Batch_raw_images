"""
Pydantic 请求/响应模型
========================
集中定义 API 请求体模型。
"""

from pydantic import BaseModel


class RegisterRequest(BaseModel):
    account: str
    password: str


class LoginRequest(BaseModel):
    account: str
    password: str


class TaskCreateRequest(BaseModel):
    prompt: str
    reference_image: str = ""  # 已有上传文件的路径（多个用逗号分隔）
    shared_reference_image: str = ""  # 批量模式下的共用参考图路径（多个用逗号分隔），会附加到每张批量图
    size: str  # 比例（Web 表单必传，不设后端默认值）
    n: int     # 张数（Web 表单必传，不设后端默认值）
    model: str = "gpt-image-2"  # 模型名
    quality: str = "1K"  # 清晰度：1K / 2K
    batch: int = 0  # 1 = 批量模式（压缩包）：每张参考图独立生成 1 张


class RechargeRequest(BaseModel):
    user_id: int
    amount: int
    remark: str = ""


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class UpdateBalanceRequest(BaseModel):
    user_id: int
    balance: int  # 直接设置余额
    remark: str = ""


class DeleteUserRequest(BaseModel):
    user_id: int


class ResetPasswordRequest(BaseModel):
    user_id: int
    new_password: str


class SetUserChangeRequest(BaseModel):
    """设置用户分组数字（与供应商 change 数字相等才匹配使用）"""
    user_id: int
    change: int


class ProviderCreateRequest(BaseModel):
    """新增 API 供应商（只需填写核心 3 项）"""
    name: str
    api_key: str
    base_url: str
    model: str = ""
    enabled: int = 1
    priority: int = 0
    change: int = 0


class ProviderUpdateRequest(BaseModel):
    """更新 API 供应商（全部字段重填）"""
    name: str
    api_key: str
    base_url: str
    model: str = ""
    enabled: int = 1
    priority: int = 0
    change: int = 0


class ProviderToggleRequest(BaseModel):
    """启用/停用 API 供应商"""
    enabled: bool


class ProviderDeleteRequest(BaseModel):
    """删除 API 供应商"""
    provider_id: int
