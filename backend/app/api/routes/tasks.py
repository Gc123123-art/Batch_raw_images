"""
任务路由 — 创建 / 列表 / 详情 / 结果 / 执行
============================================
后台任务在独立线程中执行（daemon 线程），进度与结果持久化到数据库。
服务启动时自动恢复未完成任务；看门狗线程兜底处理停滞的 running 任务。

任务定位：account + created_at（对外 id = created_at）。
"""

import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException

from app.schemas.schemas import TaskCreateRequest
from app.core.config import COST_PER_IMAGE, TASK_STALL_TIMEOUT, AI_MAX_CONCURRENCY
from app.db.database import (
    create_task, get_user_tasks, get_task_by_key, get_task_results,
    get_user_by_account,
    deduct_balance, add_balance, record_deduction,
    update_task_status, update_task_status_if_running, claim_pending_task,
    set_task_image_url, set_task_error,
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


def _user_id_by_account(account: str):
    """按账号取 user_id（余额扣退仍以 users.id 计），用户已删除时返回 None"""
    u = get_user_by_account(account)
    return u["id"] if u else None


def _user_change(account: str) -> int:
    """按账号取用户分组数字（change），与供应商 change 相等才匹配使用"""
    u = get_user_by_account(account)
    return int(u.get("change") or 0) if u else 0


# ============================================================
# 后台任务执行
# ============================================================
# 全局 AI 调用并发闸门：所有任务的所有子任务共享这 AI_CONC 个额度，
# 防止大批量任务 / 多任务同时执行瞬间打满供应商限流和 frp 代理
AI_CONC = max(1, AI_MAX_CONCURRENCY)
_gen_semaphore = threading.Semaphore(AI_CONC)


def _refund_ungenerated(account: str, created_at: str) -> int:
    """退还"已扣费但未生成"的额度：净扣费(amount) - 已成功张数 * 单价。

    以任务表 amount（净扣费）为准，退还后 amount 同步减少，
    工作线程与看门狗任意时点调用都不会重复退款。返回实际退还数额。
    """
    task = get_task_by_key(account, created_at)
    if not task:
        return 0
    amount = task.get("amount") or 0
    generated = len(get_task_results(account, created_at))
    refund = amount - generated * COST_PER_IMAGE
    if refund <= 0:
        return 0
    uid = _user_id_by_account(account)
    if uid is None:
        return 0
    add_balance(uid, refund)
    record_deduction(account, created_at, -refund, "未生成部分退还")
    return refund


def _run_task_background(account: str, created_at: str, prompt: str,
                         ref_image: str, shared_image: str, size: str,
                         n: int, batch: int = 0):
    """后台任务执行（在独立线程中运行）

    batch=1（批量模式）：每张批量图独立生成 1 张，子任务数 = 批量图数量；
      shared_image（共用参考图）会附加到每张批量图后面一起送模型，
      例如批量图是"老人+护工"、共用参考图是两件衣服，提示词描述换装，
      每张批量图都会与这两件衣服一起编辑生成。
    否则（默认）：所有参考图合成后生成 n 张（n 仅在本次执行期间使用，不入库）。
    """
    seq_count = 0
    try:
        # 用户线路分组：任务执行期间固定，与供应商 change 匹配
        user_change = _user_change(account)

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
        done_seqs = {r["seq"] for r in get_task_results(account, created_at)}

        completed = 0
        progress_lock = threading.Lock()
        pending_seqs = [seq for seq in range(1, seq_count + 1) if seq not in done_seqs]
        completed += (seq_count - len(pending_seqs))

        def _generate_one(seq: int):
            nonlocal completed
            # 批量模式：每个子任务用对应的 1 张批量图 + 全部共用参考图；
            # 普通模式：所有参考图一起送模型
            refs_for_seq = ([ref_list[seq - 1]] if (batch and ref_list) else list(ref_list)) + shared_list
            # 全局并发闸门：等待拿到额度后才发起 AI 调用
            with _gen_semaphore:
                result = process_single_task(
                    prompt=gen_prompt,
                    reference_images=refs_for_seq,
                    size=platform_size,
                    n=1,
                    change=user_change,
                )

            if result["status"] == "success":
                image_url = result["image_urls"][0] if result["image_urls"] else ""
                if image_url:
                    set_task_image_url(account, created_at, seq, image_url)
            else:
                set_task_error(account, created_at, result["error"])

            with progress_lock:
                if result["status"] == "success":
                    completed += 1

        # 任务内并行：单任务最多 AI_CONC 个线程，跨任务总量由 _gen_semaphore 兜底
        with ThreadPoolExecutor(
                max_workers=min(max(1, len(pending_seqs)), AI_CONC)) as pool:
            futures = [pool.submit(_generate_one, seq) for seq in pending_seqs]
            for f in futures:
                f.result()

        if completed == 0:
            # 全部失败：退还全部净扣费并标记为 failed（不进历史任务列表）
            _refund_ungenerated(account, created_at)
            update_task_status(account, created_at, "failed")
        else:
            # 先退还未生成部分的费用，再定最终状态
            _refund_ungenerated(account, created_at)
            status = "completed" if completed == seq_count else "partial"
            update_task_status(account, created_at, status)

    except Exception:
        # 异常也按失败处理：退还未生成部分
        try:
            _refund_ungenerated(account, created_at)
            update_task_status(account, created_at, "failed")
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

    批量模式张数 = 参考图数量；普通模式张数 = 已完成张数 + 1
    （n 不入库，重启后按已有结果估算续跑，保证不丢已扣费用对应的结果）。
    """
    from app.db.database import _parse_image_url_map
    for task in list_incomplete_tasks():
        account = task["account"]
        created_at = task["created_at"]
        ref_list = [x for x in (task["reference_image"] or "").split(",") if x.strip()]
        batch = task.get("batch", 0)
        if batch and ref_list:
            n = 1
            seq_count = len(ref_list)
        else:
            batch = 0
            done = len(_parse_image_url_map(task))
            n = done + 1
            seq_count = n

        cost = seq_count * COST_PER_IMAGE
        uid = _user_id_by_account(account)
        if uid is None:
            update_task_status(account, created_at, "failed")
            continue

        if task["status"] == "pending":
            if not deduct_balance(uid, cost):
                update_task_status(account, created_at, "failed")
                record_deduction(account, created_at, 0, "恢复执行失败：余额不足")
                continue
            record_deduction(account, created_at, cost, f"批量生成 {cost} 张（恢复执行）")

        update_task_status(account, created_at, "running")
        threading.Thread(
            target=_run_task_background,
            args=(account, created_at, task["prompt"], task["reference_image"],
                  task.get("shared_reference_image") or "",
                  task["size"], n, batch),
            daemon=True,
        ).start()


def _stall_watchdog_loop():
    """看门狗线程：周期性检查停滞的 running 任务。

    单次 AI 调用最长 DEFAULT_TIMEOUT(300s)，每张图成功/失败都会刷新
    updated_at（心跳），因此超过 TASK_STALL_TIMEOUT（默认 15 分钟）无任何
    进度的 running 任务基本已卡死（线程被杀等）。此时按"已扣费但未生成"
    的张数退还额度（已生成的不退），一张没生成标记 failed，有部分生成
    标记 partial。
    """
    last_cleanup = 0.0
    while True:
        time.sleep(60)
        try:
            for task in list_stalled_running_tasks(TASK_STALL_TIMEOUT):
                account = task["account"]
                created_at = task["created_at"]
                has_image = len(get_task_results(account, created_at)) > 0
                # 条件接管：仅当任务仍是 running 时改状态，避免覆盖刚完成的任务
                if not update_task_status_if_running(
                        account, created_at, "partial" if has_image else "failed"):
                    continue
                _refund_ungenerated(account, created_at)
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
    created_at = create_task(
        account=user["account"],
        prompt=prompt,
        reference_image=ref_image,
        shared_reference_image=shared_image,
        size=req.size,
        batch=1 if req.batch else 0,
    )

    # 扣费
    deduct_balance(user["id"], cost)
    record_deduction(user["account"], created_at, cost, f"批量生成 {total_count} 张")

    # 自动执行后台线程
    update_task_status(user["account"], created_at, "running")
    threading.Thread(
        target=_run_task_background,
        args=(user["account"], created_at, prompt, ref_image, shared_image,
              req.size, n, req.batch),
        daemon=True,
    ).start()

    return {
        "task_id": created_at,
        "total_count": total_count,
        "cost": cost,
        "balance_after": user["balance"] - cost,
        "message": f"任务已创建，将生成 {total_count} 张图，扣除 {cost} 次额度",
    }


@router.get("/api/tasks")
def list_tasks(page: int = 1, page_size: int = 20, user: dict = current_user):
    """获取用户任务列表"""
    tasks = get_user_tasks(user["account"], page, page_size)
    return {"tasks": tasks, "page": page, "page_size": page_size}


@router.get("/api/tasks/{created_at}")
def get_task(created_at: str, user: dict = current_user):
    """获取任务详情"""
    task = get_task_by_key(user["account"], created_at)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/api/tasks/{created_at}/results")
def get_task_results_api(created_at: str, user: dict = current_user):
    """获取任务生成结果"""
    task = get_task_by_key(user["account"], created_at)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    results = get_task_results(user["account"], created_at)
    return {"task": task, "results": results}


@router.post("/api/tasks/{created_at}/execute")
def execute_task(created_at: str, user: dict = current_user):
    """执行任务（异步启动后台线程）"""
    task = get_task_by_key(user["account"], created_at)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 先原子认领（pending → running）：双击/并发重入时第二个请求认领失败，
    # 从源头防止重复扣费重复执行
    if not claim_pending_task(user["account"], created_at):
        raise HTTPException(status_code=400, detail="任务已执行，请查看结果")

    # 再扣费（批量模式张数 = 参考图数量；普通模式 = 1 张）；失败则回滚认领
    ref_list = [x for x in (task["reference_image"] or "").split(",") if x.strip()]
    total_count = len(ref_list) if task.get("batch") and ref_list else 1
    cost = total_count * COST_PER_IMAGE

    if not deduct_balance(user["id"], cost):
        update_task_status(user["account"], created_at, "pending")
        raise HTTPException(status_code=402, detail="扣费失败，余额不足")

    record_deduction(user["account"], created_at, cost, f"批量生成 {total_count} 张")

    # 启动后台线程执行
    n = 1 if task.get("batch") else total_count
    thread = threading.Thread(
        target=_run_task_background,
        args=(user["account"], created_at, task["prompt"], task["reference_image"],
              task.get("shared_reference_image") or "",
              task["size"], n, task.get("batch", 0)),
        daemon=True,
    )
    thread.start()

    return {"message": "任务已启动执行", "task_id": created_at, "cost": cost}
