"""
上传路由 — 参考图上传 / 压缩包上传 / 签名预览
===============================================
"""

import os
import shutil
from datetime import datetime
from io import BytesIO
from pathlib import Path

import requests
from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.core.config import UPLOAD_DIR, PREVIEW_SIG_TTL
from app.core.security import sign_preview_url, verify_preview_signature
from app.api.deps import current_user
from app.db.database import (
    get_task_by_id,
    get_task_result_by_seq,
    list_incomplete_tasks,
)

router = APIRouter(tags=["upload"])

# 支持的图片扩展名
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

# 单图上传上限 10MB；压缩包上限 50MB；解压后总量上限 200MB；文件数上限 300
MAX_IMAGE_SIZE = 10 * 1024 * 1024
MAX_ARCHIVE_SIZE = 50 * 1024 * 1024
MAX_EXTRACTED_SIZE = 200 * 1024 * 1024
MAX_FILES = 300


# ============================================================
# RAR / ZIP 解压
# ============================================================
def _find_unrar() -> str:
    """自动探测 unrar 解压工具路径：环境变量 -> PATH -> 常见安装目录"""
    env_path = os.getenv("UNRAR_TOOL", "").strip()
    if env_path and os.path.isfile(env_path):
        return env_path
    for name in ("unrar", "UnRAR.exe", "rar", "Rar.exe", "unar"):
        p = shutil.which(name)
        if p:
            return p
    candidates = [
        r"C:\Program Files\WinRAR\UnRAR.exe",
        r"C:\Program Files\WinRAR\Rar.exe",
        r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
        r"C:\Program Files (x86)\WinRAR\Rar.exe",
        "/usr/bin/unrar",
        "/usr/local/bin/unrar",
        "/usr/bin/unar",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return ""


UNRAR_TOOL = _find_unrar()


def _extract_archive(content: bytes, filename: str, extract_dir: Path):
    """统一解压 ZIP / RAR 压缩包，带防 zip 炸弹限制（总大小 / 文件数）"""
    if len(content) > MAX_ARCHIVE_SIZE:
        raise HTTPException(status_code=400,
                            detail=f"压缩包大小超过 {MAX_ARCHIVE_SIZE // (1024 * 1024)}MB 限制")

    suffix = Path(filename).suffix.lower()
    if suffix == ".rar":
        if not UNRAR_TOOL:
            raise HTTPException(
                status_code=400,
                detail="服务器缺少 unrar 解压工具，无法处理 RAR 文件（请安装 WinRAR 或设置 UNRAR_TOOL 环境变量）",
            )
        import rarfile
        rarfile.UNRAR_TOOL = UNRAR_TOOL
        archive = rarfile.RarFile(BytesIO(content))
    elif suffix == ".zip":
        import zipfile
        archive = zipfile.ZipFile(BytesIO(content))
    else:
        raise HTTPException(status_code=400, detail="不支持的压缩包格式，仅支持 .zip / .rar")

    try:
        infos = archive.infolist()
        if len(infos) > MAX_FILES:
            raise HTTPException(status_code=400, detail=f"压缩包文件过多，上限 {MAX_FILES} 个")
        total_size = sum(i.file_size for i in infos)
        if total_size > MAX_EXTRACTED_SIZE:
            raise HTTPException(status_code=400,
                                detail="压缩包解压后总大小超过 200MB 限制")
        archive.extractall(extract_dir)
    finally:
        archive.close()


# ============================================================
# 上传接口
# ============================================================
@router.post("/api/upload")
async def upload_file(file: UploadFile = File(...), user: dict = current_user):
    """上传参考图，返回文件路径（需登录）"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名为空")

    # 校验文件类型
    ext = Path(file.filename).suffix.lower()
    if ext not in IMAGE_EXTS:
        raise HTTPException(status_code=400, detail="不支持的文件格式")

    # 保存文件（按日期分目录）
    date_dir = UPLOAD_DIR / datetime.now().strftime("%Y%m%d")
    os.makedirs(date_dir, exist_ok=True)

    ts = int(datetime.now().timestamp())
    save_name = f"{ts}_{file.filename}"
    save_path = date_dir / save_name

    # 限制读取大小，防止超大文件打爆内存
    content = await file.read(MAX_IMAGE_SIZE + 1)
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="文件大小超过 10MB 限制")

    with open(save_path, "wb") as f:
        f.write(content)

    # 构建可预览的 URL（相对路径，前端拼接 API_BASE；带短时效签名，不暴露登录 token）
    rel_path = str(save_path.relative_to(UPLOAD_DIR)).replace("\\", "/")
    return {
        "path": str(save_path),
        "filename": file.filename,
        "url": sign_preview_url(rel_path, PREVIEW_SIG_TTL),
    }


@router.post("/api/upload/zip")
async def upload_zip(file: UploadFile = File(...), user: dict = current_user):
    """上传 ZIP / RAR 压缩包（批量上传参考图），解压后返回文件夹路径 + 文件列表（需登录）"""
    if not file.filename or Path(file.filename).suffix.lower() not in {".zip", ".rar"}:
        raise HTTPException(status_code=400, detail="请上传 .zip 或 .rar 文件")

    # 解压到独立文件夹
    date_dir = UPLOAD_DIR / datetime.now().strftime("%Y%m%d")
    zip_name = Path(file.filename).stem
    extract_dir = date_dir / f"{zip_name}_{int(datetime.now().timestamp())}"
    os.makedirs(extract_dir, exist_ok=True)

    content = await file.read(MAX_ARCHIVE_SIZE + 1)
    if len(content) > MAX_ARCHIVE_SIZE:
        raise HTTPException(status_code=400, detail="压缩包大小超过 50MB 限制")

    _extract_archive(content, file.filename, extract_dir)

    # 收集所有图片文件，构建可预览的 URL
    image_paths = sorted(
        p for p in extract_dir.rglob("*")
        if p.suffix.lower() in IMAGE_EXTS and p.is_file()
    )

    return {
        "path": str(extract_dir),
        "image_count": len(image_paths),
        "files": [
            {"path": str(p), "name": p.name,
             "url": sign_preview_url(str(p.relative_to(UPLOAD_DIR)).replace(chr(92), '/'), PREVIEW_SIG_TTL)}
            for p in image_paths
        ],
        "message": f"解压完成，共 {len(image_paths)} 张图片",
    }


@router.delete("/api/upload")
async def delete_upload_file(path: str = Query(...), user: dict = current_user):
    """删除已上传的参考图文件（前端在参考图被移除 / 会话结束时调用）。

    - 支持绝对路径或相对 uploads 根的路径；
    - 被未完成任务（pending/running/partial）引用的文件不删除，
      防止任务恢复执行时缺图；该文件随后由看门狗周期清扫兜底。
    """
    # 路径安全：仅允许删除 uploads 目录内的图片
    raw = Path(path)
    target = raw if raw.is_absolute() else (UPLOAD_DIR / raw)
    target = target.resolve()
    upload_root = UPLOAD_DIR.resolve()
    if upload_root not in target.parents:
        raise HTTPException(status_code=403, detail="非法路径")
    if not target.is_file():
        return {"ok": True, "message": "文件不存在或已删除"}
    if target.suffix.lower() not in IMAGE_EXTS:
        raise HTTPException(status_code=403, detail="不支持的文件类型")

    if os.path.normcase(str(target)) in _protected_upload_paths():
        raise HTTPException(status_code=409, detail="该参考图正被进行中的任务使用，已保留")

    os.remove(target)
    # 顺带清理空目录（非空会抛 OSError 被忽略，由看门狗兜底）
    try:
        os.rmdir(target.parent)
    except OSError:
        pass
    return {"ok": True, "message": "已删除"}


def _protected_upload_paths() -> set:
    """未被完成任务（running/pending）引用的上传文件集合。

    批量任务的 reference_image（批量图）与 shared_reference_image（共用参考图）
    都算被引用，任务恢复/继续执行时都可能用到。
    """
    protected = set()
    for t in list_incomplete_tasks():
        for field in ("reference_image", "shared_reference_image"):
            for p in (t.get(field) or "").split(","):
                p = p.strip()
                if p:
                    protected.add(os.path.normcase(os.path.normpath(p)))
    return protected


def cleanup_stale_uploads(min_age_seconds: int = 3600) -> int:
    """清扫 uploads 目录中过期的残留参考图（后端兜底，不依赖前端删除）。

    删除条件（同时满足）：
      - 未被任何未完成任务（running/pending）引用，避免任务恢复/继续执行时缺图；
      - 文件修改时间距今超过 min_age_seconds（默认 1 小时），避免误删刚上传还没创建任务的图。

    顺带清理因此变空的子目录（保留 uploads 根目录）。返回删除的文件数。
    """
    import time as _time

    # 与 delete_upload_file 相同的保护集合：未完成任务引用的文件（含共用参考图）不删
    protected = _protected_upload_paths()

    upload_root = UPLOAD_DIR.resolve()
    if not upload_root.is_dir():
        return 0

    now = _time.time()
    deleted = 0
    empty_dirs = []
    for root, _dirs, files in os.walk(upload_root, topdown=False):
        for name in files:
            fp = Path(root) / name
            if fp.suffix.lower() not in IMAGE_EXTS:
                continue
            if os.path.normcase(str(fp)) in protected:
                continue
            try:
                if now - fp.stat().st_mtime < min_age_seconds:
                    continue
            except OSError:
                continue
            try:
                fp.unlink()
                deleted += 1
            except OSError:
                pass
        # 记录可能变空的目录（上传日期目录 / 解压目录）
        if Path(root) != upload_root:
            try:
                if not any(Path(root).iterdir()):
                    empty_dirs.append(Path(root))
            except OSError:
                pass

    for d in empty_dirs:
        try:
            d.rmdir()
        except OSError:
            pass
    return deleted


@router.get("/api/upload-preview")
async def preview_upload_file(path: str = Query(...), expires: int = Query(0),
                              sig: str = Query("")):
    """提供上传文件的预览（短时效签名 URL）。path 为相对于 uploads 根的路径。

    URL 由后端在上传时生成，携带 HMAC 签名与过期时间（不携带登录 token，
    避免 token 进入浏览器历史 / 反向代理日志 / Referer）。过期后需重新上传获取。
    """
    # 校验签名与有效期
    if not verify_preview_signature(path, expires, sig):
        raise HTTPException(status_code=403, detail="预览链接无效或已过期，请重新上传")

    # 防止路径穿越
    target = (UPLOAD_DIR / path).resolve()
    if UPLOAD_DIR.resolve() not in target.parents and target != UPLOAD_DIR.resolve():
        raise HTTPException(status_code=403, detail="非法路径")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.suffix.lower() not in IMAGE_EXTS:
        raise HTTPException(status_code=403, detail="不支持的文件类型")
    from fastapi.responses import FileResponse
    resp = FileResponse(str(target))
    resp.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@router.get("/api/files/{task_id}/{seq}")
def get_image(task_id: int, seq: int, user: dict = current_user):
    """根据任务ID和序号提供生成的图片文件

    加速策略（零服务器磁盘占用，缓存落在浏览器端）：
    - 旧记录（存本地路径）：直接 FileResponse + 长缓存
    - 裁切记录（存 base64 data URL）：解码后直接返回 + 长缓存
    - 新记录（存云端 URL）：流式代理转发，并加 Cache-Control 头，
      让浏览器本地缓存代理结果 —— 同一浏览器再次访问秒开，
      且不消耗服务器磁盘（AI 平台 URL 过期前访问过一次即永久可用）
    """
    from fastapi.responses import FileResponse, StreamingResponse
    import requests

    # 权限检查
    task = get_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问该任务")

    # 单条查询（消除 N+1，不查全表）
    target = get_task_result_by_seq(task_id, seq)
    if not target:
        raise HTTPException(status_code=404, detail="图片不存在")

    # 1) 旧记录：本地文件
    if target.get("image_path"):
        file_path = Path(target["image_path"])
        if file_path.exists() and file_path.is_file():
            response = FileResponse(str(file_path))
            response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Cache-Control"] = "public, max-age=86400"
            return response

    # 2) 裁切结果存的是 base64 data URL（不落盘，直接解码返回）
    if target.get("image_url", "").startswith("data:image/"):
        try:
            import base64
            from fastapi.responses import Response
            header, _, b64data = target["image_url"].partition(",")
            content_type = header[5:].split(";")[0] or "image/png"
            img_bytes = base64.b64decode(b64data)
            response = Response(content=img_bytes, media_type=content_type)
            # 浏览器缓存 24 小时：同一浏览器再次访问命中缓存，秒开且不重复解码
            response.headers["Cache-Control"] = "public, max-age=86400"
            response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response
        except Exception:
            raise HTTPException(status_code=502, detail="图片数据解析失败")

    # 3) 新记录：代理云端 URL（浏览器端缓存，不落服务器磁盘）
    if target.get("image_url"):
        try:
            resp = requests.get(target["image_url"], stream=True, timeout=60)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/png")
            response = StreamingResponse(resp.iter_content(chunk_size=8192),
                                         media_type=content_type)
            # 浏览器缓存 24 小时：第二次访问直接命中浏览器本地缓存，秒开
            response.headers["Cache-Control"] = "public, max-age=86400"
            response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response
        except requests.exceptions.RequestException:
            raise HTTPException(status_code=502, detail="云端图片暂时无法访问")

    # 3) 没有可用数据
    raise HTTPException(status_code=404, detail="图片不存在")
