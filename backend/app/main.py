"""
BOLI AI 批量图片生成 — 后端应用入口
======================================
组装 FastAPI 应用：CORS、路由注册、启动初始化。

启动方式：
    python -m app.main                     # 默认 0.0.0.0:8000
    python -m app.main --port 8080         # 指定端口
"""

import os
import argparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import CORS_ORIGINS, LOGIN_MAX_ATTEMPTS, LOGIN_LOCK_MINUTES
from app.core.secrets import get_or_create_secret
from app.db.database import init_db
from app.api.routes import auth, users, tasks, admin, upload
from app.api.routes.tasks import resume_incomplete_tasks, start_watchdog

app = FastAPI(
    title="BOLI AI 批量图片生成 API",
    version="2.0.0",
    description="批量图片生成平台后端（FastAPI + PostgreSQL）",
)

# CORS — 允许的跨域来源（生产环境通过 CORS_ORIGINS 环境变量配置具体前端域名）
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(tasks.router)
app.include_router(admin.router)
app.include_router(upload.router)


@app.get("/api/health")
def health_check():
    """健康检查"""
    return {"status": "ok", "version": "2.0.0"}


def main():
    parser = argparse.ArgumentParser(description="BOLI AI API 服务器")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    args = parser.parse_args()

    # 初始化数据库
    init_db()
    print("[INFO] 数据库初始化完成")

    # 恢复未完成任务（服务重启/崩溃后自动续跑或退款兜底）
    resume_incomplete_tasks()
    start_watchdog()

    print(f"[INFO] API 服务器启动: http://{args.host}:{args.port}")
    print(f"[INFO] API 文档: http://{args.host}:{args.port}/docs")

    # 安全提示：密钥来源（不打印密钥本身）
    print(f"[安全] JWT_SECRET 来源: {'环境变量' if os.getenv('JWT_SECRET') else '.secrets/jwt_secret 文件'}")
    print(f"[安全] ADMIN_KEY 来源: {'环境变量' if os.getenv('ADMIN_KEY') else '.secrets/admin_key 文件'}")
    print(f"[安全] CORS 允许来源: {', '.join(CORS_ORIGINS)}")
    print(f"[安全] 登录限流: 同一 IP 连续失败 {LOGIN_MAX_ATTEMPTS} 次锁定 {LOGIN_LOCK_MINUTES} 分钟")

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
