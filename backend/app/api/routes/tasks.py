"""
任务路由 — 创建 / 列表 / 详情 / 结果 / 执行
============================================
后台任务在独立线程中执行（daemon 线程），进度与结果持久化到数据库。
服务启动时自动恢复未完成任务；看门狗线程兜底处理停滞的 running 任务。
"""

import os
import time
import threading

from fastapi import APIRouter, HTTPException

from app.schemas.schemas import TaskCreateRequest
from app.core.config import COST_PER_IMAGE, TASK_STALL_TIMEOUT
from app.db.database import (
    create_task, get_user_tasks, get_task_by_id, get_task_results,
    deduct_balance, add_balance, record_deduction,
    update_task_progress, update_task_result,
    list_incomplete_tasks, list_stalled_running_tasks,
)
from app.api.deps import current_user
from app.services.engine import process_single_task

router = APIRouter(tags=["task"])

# ============================================================
# 尺寸（比例）映射
# gpt-image 系列模型只精确支持 1024x1024 / 1024x1536 / 1536x1024 三种标准尺寸。
# 前端传目标比例字符串，这里映射到最近的标准尺寸请求，
# 同时把"图片比例：X:Y"追加进提示词交由模型按比例构图（不做后处理裁切）。
# 旧任务遗留的像素值也一并兼容。
# ============================================================
SIZE_MAP = {
    "1:1":       "1024x1024",
    "3:4":       "1024x1536",
    "4:3":       "1536x1024",
    "2:3":       "1024x1536",
    "3:2":       "1536x1024",
    "9:16":      "1024x1536",
    "16:9":      "1536x1024",
    # 旧版遗留：前端曾传自定义像素值（平台不支持，导致比例错乱）
    "1024x1024": "1024x1024",
    "1024x1365": "1024x1536",
    "1365x1024": "1536x1024",
}

# 所有比例（含 1:1）都追加到提示词，与 size 参数形成双重保险
RATIO_HINTS = {
    "1:1": "图片比例为1:1",
    "3:4": "图片比例为3:4",
    "4:3": "图片比例为4:3",
    "2:3": "图片比例为2:3",
    "3:2": "图片比例为3:2",
    "9:16": "图片比例为9:16",
    "16:9": "图片比例为16:9",
}


def _resolve_size(size: str) -> str:
    """把前端比例 / 旧像素值解析为平台请求 size"""
    return SIZE_MAP.get(size, size)  # 未知值原样传给平台


# ============================================================
# 后台任务执行
# ============================================================
def _run_task_background(task_id: int, prompt: str, ref_image: str,
                          shared_image: str, size: str, n: int,
                          user_id: int, batch: int = 0):
    """后台任务执行（在独立线程中运行）

    batch=1（批量模式）：每张批量图独立生成 1 张，子任务数 = 批量图数量；
      shared_image（共用参考图）会附加到每张批量图后面一起送模型，
      例如批量图是"老人+护工"、共用参考图是两件衣服，提示词描述换装，
      每张批量图都会与这两件衣服一起编辑生成。
    否则（默认）：所有参考图合成后生成 n 张。
    任务结束后自动删除上传的参考图临时文件。
    """
    try:
        # 解析参考图列表（逗号分隔）；文生图（无参考图）时保持空列表
        ref_list = []
        if ref_image and ref_image.strip():
            paths = [x.strip() for x in ref_image.split(",") if x.strip()]
            if paths:
                ref_list = paths

        # 共用参考图（仅批量模式使用）
        shared_list = []
        if batch and shared_image and shared_image.strip():
            shared_list = [x.strip() for x in shared_image.split(",") if x.strip()]

        # 子任务总数：批量模式 = 批量图数量，否则 = n
        seq_count = len(ref_list) if (batch and ref_list) else n

        # 尺寸映射：前端比例 -> 平台标准 size
        platform_size = _resolve_size(size)

        # 所有比例：把"图片比例：X:Y"追加进提示词末尾（逗号衔接、句号收尾），与 size 参数双重保险
        gen_prompt = prompt
        if size in RATIO_HINTS:
            gen_prompt = f"{prompt}，{RATIO_HINTS[size]}。"

        # 恢复场景：跳过已成功的子任务，避免重复调用 AI 重复扣供应商费用
        done_seqs = {r["seq"] for r in get_task_results(task_id)
                     if r.get("status") == "success"}

        completed = 0
        first_error = ""
        for seq in range(1, seq_count + 1):
            if seq in done_seqs:
                completed += 1
                continue
            # 批量模式：每个子任务用对应的 1 张批量图 + 全部共用参考图；
            # 普通模式：所有参考图一起送模型
            refs_for_seq = ([ref_list[seq - 1]] if (batch and ref_list) else list(ref_list)) + shared_list
            result = process_single_task(
                prompt=gen_prompt,
                reference_images=refs_for_seq,
                size=platform_size,
                n=1,
            )

            if result["status"] == "success":
                image_path = ""  # 纯 URL / data URL 模式，不本地落地
                image_url = result["image_urls"][0] if result["image_urls"] else ""
                update_task_result(task_id, seq, "success",
                                   image_path=image_path,
                                   image_url=image_url,
                                   prompt=prompt)
                completed += 1
            else:
                update_task_result(task_id, seq, "failed",
                                   error=result["error"])
                if not first_error:
                    first_error = result["error"]

            update_task_progress(task_id, completed)

        if completed == 0:
            # 全部失败：退还扣费并标记为 failed（不进历史任务列表）
            cost = seq_count * COST_PER_IMAGE
            add_balance(user_id, cost)
            record_deduction(user_id, task_id, -cost,
                             f"生成失败退还: {first_error[:100]}")
            update_task_progress(task_id, 0, "failed")
        else:
            status = "completed" if completed == seq_count else "partial"
            update_task_progress(task_id, completed, status)

    except Exception as e:
        # 异常也按全失败处理
        try:
            cost = seq_count * COST_PER_IMAGE
            add_balance(user_id, cost)
            record_deduction(user_id, task_id, -cost, f"生成异常退还: {str(e)[:100]}")
            update_task_progress(task_id, 0, "failed")
        except Exception:
            pass
    # 参考图由前端负责删除：参考图被移除 / 会话结束时调用 DELETE /api/upload 立即删除


# ============================================================
# 任务恢复（服务重启/崩溃兜底）
# ============================================================
def resume_incomplete_tasks():
    """服务启动时恢复未完成任务。

    - running：已扣费，直接续跑（跳过已成功的子任务）；
    - pending：创建后未执行，先扣费再跑；余额不足则标记失败。
    """
    for task in list_incomplete_tasks():
        task_id = task["id"]
        user_id = task["user_id"]
        cost = task.get("total_count") or task.get("n", 1)

        if task["status"] == "pending":
            if not deduct_balance(user_id, cost):
                update_task_progress(task_id, 0, "failed")
                record_deduction(user_id, task_id, 0, "恢复执行失败：余额不足")
                continue
            record_deduction(user_id, task_id, cost, f"批量生成 {cost} 张（恢复执行）")
            update_task_progress(task_id, 0, "running")

        threading.Thread(
            target=_run_task_background,
            args=(task_id, task["prompt"], task["reference_image"],
                  task.get("shared_reference_image") or "",
                  task["size"], task["n"], user_id, task.get("batch", 0)),
            daemon=True,
        ).start()


def _stall_watchdog_loop():
    """看门狗线程：周期性检查停滞的 running 任务。

    单次 AI 调用最长 DEFAULT_TIMEOUT(300s)，每个子任务结束都会刷新进度，
    因此超过 TASK_STALL_TIMEOUT（默认 15 分钟）无进度的 running 任务基本
    已卡死（线程被杀等），标记失败并把已扣费用退还给用户。
    """
    last_cleanup = 0.0
    while True:
        time.sleep(60)
        try:
            for task in list_stalled_running_tasks(TASK_STALL_TIMEOUT):
                task_id = task["id"]
                user_id = task["user_id"]
                cost = task.get("amount") or (task.get("total_count") or 0) * COST_PER_IMAGE
                if cost > 0:
                    add_balance(user_id, cost)
                    record_deduction(user_id, task_id, -cost, "生成超时失败退还")
                update_task_progress(task_id, task.get("completed_count", 0), "failed")
        except Exception:
            continue
        # 兜底清扫：每 10 分钟扫一次 uploads 残留参考图（前端漏删/会话异常时兜底）
        try:
            if time.time() - last_cleanup >= 600:
                from app.api.routes.upload import cleanup_stale_uploads
                cleanup_stale_uploads(3600)  # 未被任务引用且超过 1 小时的残留文件删除
                last_cleanup = time.time()
        except Exception:
            pass


def start_watchdog():
    """启动停滞任务看门狗（daemon 线程，随服务退出）"""
    threading.Thread(target=_stall_watchdog_loop, daemon=True).start()


# ============================================================
# 任务接口
# ============================================================
@router.post("/api/tasks")
def create_batch_task(req: TaskCreateRequest, user: dict = current_user):
    """创建批量任务（单条 prompt）"""
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt 不能为空")
    if not req.size:
        raise HTTPException(status_code=400, detail="比例 size 必传（由前端选择）")
    if req.size not in SIZE_MAP:
        raise HTTPException(status_code=400,
                            detail="不支持的尺寸，仅支持 1:1 / 3:4 / 4:3 / 9:16 / 16:9 / 2:3 / 3:2")
    if not req.n or req.n < 1:
        raise HTTPException(status_code=400, detail="张数 n 必传且 >= 1")

    ref_image = req.reference_image.strip() if req.reference_image else ""
    shared_image = req.shared_reference_image.strip() if req.shared_reference_image else ""
    n = max(1, min(req.n, 4))

    # 解析参考图列表
    ref_list = [x.strip() for x in ref_image.split(",") if x.strip()] if ref_image else []
    shared_list = [x.strip() for x in shared_image.split(",") if x.strip()] if shared_image else []

    # 批量模式（压缩包/文件夹）：每张批量图独立生成 1 张，总张数 = 批量图数量；
    # 共用参考图附加到每张批量图（1 张批量图 + 共用 ≤ 6 张，平台单次上限）
    if req.batch:
        if not ref_list:
            raise HTTPException(status_code=400, detail="批量模式需要上传压缩包/文件夹批量图")
        if len(shared_list) > 5:
            raise HTTPException(status_code=400,
                                detail="共用参考图最多 5 张（每张批量图 + 共用参考图不超过 6 张）")
        n = 1
        total_count = len(ref_list)
    else:
        if shared_list:
            raise HTTPException(status_code=400, detail="共用参考图仅用于批量模式")
        total_count = n

    # 如果是图生图，校验参考图是否存在
    if ref_image and not os.path.isfile(ref_image):
        # 允许多张（逗号分隔），跳过单文件校验（在子任务执行时再校验）
        if "," not in ref_image:
            raise HTTPException(status_code=400, detail=f"参考图不存在: {ref_image}")

    # 计算扣费
    cost = total_count * COST_PER_IMAGE

    # 检查余额
    if user["balance"] < cost:
        raise HTTPException(status_code=402, detail=f"余额不足，需要 {cost} 次，当前余额 {user['balance']} 次")

    # 创建任务记录
    task_id = create_task(
        user_id=user["id"],
        prompt=prompt,
        reference_image=ref_image,
        shared_reference_image=shared_image,
        size=req.size,
        n=n,
        total_count=total_count,
        batch=1 if req.batch else 0,
    )

    # 扣费
    deduct_balance(user["id"], cost)
    record_deduction(user["id"], task_id, cost, f"批量生成 {total_count} 张")

    # 自动执行后台线程
    update_task_progress(task_id, 0, "running")
    threading.Thread(
        target=_run_task_background,
        args=(task_id, prompt, ref_image, shared_image, req.size, n,
              user["id"], req.batch),
        daemon=True,
    ).start()

    return {
        "task_id": task_id,
        "total_count": total_count,
        "cost": cost,
        "balance_after": user["balance"] - cost,
        "message": f"任务已创建，将生成 {total_count} 张图，扣除 {cost} 次额度",
    }


@router.get("/api/tasks")
def list_tasks(page: int = 1, page_size: int = 20, user: dict = current_user):
    """获取用户任务列表"""
    tasks = get_user_tasks(user["id"], page, page_size)
    return {"tasks": tasks, "page": page, "page_size": page_size}


@router.get("/api/tasks/{task_id}")
def get_task(task_id: int, user: dict = current_user):
    """获取任务详情"""
    task = get_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问该任务")
    # 全部失败时附加第一个错误信息（用于前端展示）
    if task["status"] == "failed" and task.get("completed_count", 0) == 0:
        results = get_task_results(task_id)
        first_fail = next((r for r in results if r["status"] == "failed"), None)
        if first_fail:
            task["error"] = first_fail.get("error", "")
    return task


@router.get("/api/tasks/{task_id}/results")
def get_task_results_api(task_id: int, user: dict = current_user):
    """获取任务生成结果"""
    task = get_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问该任务")

    results = get_task_results(task_id)
    return {"task": task, "results": results}


@router.post("/api/tasks/{task_id}/execute")
def execute_task(task_id: int, user: dict = current_user):
    """执行任务（异步启动后台线程）"""
    task = get_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权操作该任务")
    if task["status"] != "pending":
        raise HTTPException(status_code=400, detail="任务已执行，请查看结果")

    # 先扣费
    ref_image = task["reference_image"]
    n = task["n"]
    cost = task["total_count"]

    if not deduct_balance(user["id"], cost):
        raise HTTPException(status_code=402, detail="扣费失败，余额不足")

    record_deduction(user["id"], task_id, cost, f"批量生成 {task['total_count']} 张")
    update_task_progress(task_id, 0, "running")

    # 启动后台线程执行
    thread = threading.Thread(
        target=_run_task_background,
        args=(task_id, task["prompt"], ref_image,
              task.get("shared_reference_image") or "",
              task["size"], n, user["id"], task.get("batch", 0)),
        daemon=True,
    )
    thread.start()

    return {"message": "任务已启动执行", "task_id": task_id, "cost": cost}
