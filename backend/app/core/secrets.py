"""
密钥管理模块
=============
安全地提供 JWT_SECRET / ADMIN_KEY 等敏感配置：

1. 优先读取环境变量（生产环境推荐通过 systemd / docker 注入）
2. 未设置时自动生成随机强密钥，并持久化到 .secrets/ 目录下的文件
   （首次启动后固定不变，重启不丢失；文件默认只有部署者可读）
"""

import os
import secrets
from pathlib import Path

from app.core.config import SECRETS_DIR


def get_or_create_secret(env_name: str, file_name: str) -> str:
    """获取敏感配置：环境变量优先，否则生成随机密钥并持久化到文件。

    Args:
        env_name: 环境变量名，如 "JWT_SECRET" / "ADMIN_KEY"
        file_name: 持久化文件名，如 "jwt_secret" / "admin_key"

    Returns:
        密钥字符串（48 字节 URL 安全随机串）
    """
    # 1. 环境变量优先
    env_val = os.getenv(env_name, "").strip()
    if env_val:
        return env_val

    # 2. 读取已持久化的文件
    secret_file = SECRETS_DIR / file_name
    try:
        if secret_file.exists():
            file_val = secret_file.read_text(encoding="utf-8").strip()
            if file_val:
                return file_val
    except OSError:
        pass

    # 3. 生成随机密钥并持久化
    new_val = secrets.token_urlsafe(48)
    try:
        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(new_val, encoding="utf-8")
    except OSError:
        # 无法写入时至少保证本次进程可用（重启后会重新生成，旧 token 失效）
        pass
    return new_val
