"""
BOLI AI 批量图片生成 — 核心引擎
===================================
包含 AI API 调用、多供应商故障转移、图片合成、任务解析等核心逻辑。
被 Web 服务（app.api.routes.tasks）使用。
"""

import os
import json
import time
import mimetypes
import threading
import tempfile
from datetime import datetime

import requests
from dotenv import load_dotenv, dotenv_values

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from app.core.config import (
    BACKEND_DIR, API_MEMORY_FILE, provider_env_files,
    _SYSTEM_ENV_KEYS,
)

# ============================================================
# 配置加载（环境变量优先，config.env 兜底）
# ============================================================
load_dotenv(BACKEND_DIR / "config.env", override=False)

API_KEY = os.getenv("API_KEY", "").strip()
# 去掉尾部斜杠和 /v1 后缀，避免拼接出 /v1/v1/images/...
_BASE_URL_RAW = os.getenv("BASE_URL", "https://dilisiko.shop").rstrip("/")
BASE_URL = _BASE_URL_RAW[:-3] if _BASE_URL_RAW.endswith("/v1") else _BASE_URL_RAW
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-image-2")
# size / n 不再设默认值：Web 调用会显式传；CLI 也必须显式传，避免"用了用户没选的参数"
# 请求超时秒数（供应商不再单独配置，全局统一；可用环境变量 TIMEOUT 覆盖）
DEFAULT_TIMEOUT = int(os.getenv("TIMEOUT", "300"))

# 支持的图片扩展名
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

# ============================================================
# 多 API 自动切换（多供应商故障转移 + 记忆优先）
# ============================================================
_MEMORY_FILE = API_MEMORY_FILE
_memory_lock = threading.Lock()


def _normalize_base_url(raw: str) -> str:
    """去掉尾部斜杠和 /v1 后缀，避免拼接出 /v1/v1/images/..."""
    raw = (raw or "").strip().rstrip("/")
    return raw[:-3] if raw.endswith("/v1") else raw


# 系统环境变量 -> provider 字段名映射（生产 systemd EnvironmentFile 注入时覆盖）
_ENV_OVERRIDE_MAP = {
    "API_KEY": "api_key",
    "BASE_URL": "base_url",
    "DEFAULT_MODEL": "model",
    "SUPPLIER_NAME": "name",
}


def _env_field_to_provider(cfg: dict) -> dict:
    """把 config-*.env 的字段名（API_KEY/BASE_URL/...）转成 provider 字段名。"""
    return {
        "name": (cfg.get("SUPPLIER_NAME") or "").strip(),
        "api_key": (cfg.get("API_KEY") or "").strip(),
        "base_url": (cfg.get("BASE_URL") or "").strip(),
        "model": (cfg.get("DEFAULT_MODEL") or "").strip(),
    }


def _apply_env_override(provider: dict) -> dict:
    """只允许"系统环境变量"（生产 systemd 注入）覆盖 provider 字段。

    config.env 加载进 os.environ 的键不在 _SYSTEM_ENV_KEYS 中，不会误覆盖。
    """
    merged = dict(provider)
    for env_key, field in _ENV_OVERRIDE_MAP.items():
        if env_key not in _SYSTEM_ENV_KEYS:
            continue
        env_val = os.getenv(env_key, "").strip()
        if env_val:
            merged[field] = env_val
    return merged


def _load_providers() -> list:
    """加载所有可用 API 供应商配置。

    优先从数据库 api_providers 表读取（Web 平台管理）；数据库不可用或为空时
    回退到 config.env / config-*.env 文件（CLI 首次运行等场景）。
    敏感配置仍可被系统环境变量覆盖（生产 systemd EnvironmentFile 注入）。
    """
    # ---- 优先：数据库（Web 平台已 init_db + 种子迁移）----
    try:
        from app.db.database import list_enabled_providers
        rows = list_enabled_providers()
        if rows:
            providers = []
            for r in rows:
                provider = {
                    "id": r["id"],
                    "name": r["name"],
                    "api_key": r["api_key"],
                    "base_url": _normalize_base_url(r["base_url"]),
                    "model": (r["model"] or "").strip() or DEFAULT_MODEL,
                }
                provider = _apply_env_override(provider)
                providers.append(provider)
            return providers
    except Exception:
        pass  # 数据库未初始化（如 CLI 直接运行），回退到 env 文件

    # ---- 回退：env 文件（原有逻辑，按 API_KEY 归并）----
    grouped = {}   # api_key -> {"name": 显示名, "cfg": 合并后的配置}
    order = []

    def _collect(name: str, cfg: dict):
        cfg = _apply_env_override(_env_field_to_provider(cfg))
        key = (cfg.get("api_key") or "").strip()
        if not key.startswith("sk-"):
            return
        if key not in grouped:
            grouped[key] = {"name": name, "cfg": {}}
            order.append(key)
        # 合并：只补齐缺失项，不覆盖已配置的值（config.env 显式配置优先）
        existing = grouped[key]["cfg"]
        for k, v in cfg.items():
            if v and k not in existing:
                existing[k] = v

    _collect("config.env", dotenv_values(BACKEND_DIR / "config.env"))
    for env_file in provider_env_files():
        name = env_file.stem.replace("config-", "", 1)
        _collect(name, dotenv_values(env_file))

    providers = []
    for key in order:
        entry = grouped[key]
        cfg = entry["cfg"]
        providers.append({
            "name": cfg.get("name") or entry["name"],
            "api_key": key,
            "base_url": _normalize_base_url(cfg.get("base_url", "")),
            "model": (cfg.get("model") or "").strip() or DEFAULT_MODEL,
        })

    # 兜底：没有任何有效配置时用全局变量（至少保证调用不报错）
    if not providers:
        providers.append({
            "name": "config.env",
            "api_key": API_KEY,
            "base_url": BASE_URL,
            "model": DEFAULT_MODEL,
        })
    return providers


def _get_remembered_provider() -> str:
    """读取最近一次调用成功的供应商名（跨重启持久化）"""
    try:
        if _MEMORY_FILE.exists():
            data = json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
            return str(data.get("provider") or "")
    except Exception:
        pass
    return ""


def _remember_provider(name: str):
    """记录最近一次成功的供应商，下次优先使用；直到它真正不行了才切换到下一个"""
    with _memory_lock:
        try:
            _MEMORY_FILE.write_text(
                json.dumps({"provider": name,
                            "updated_at": datetime.now().isoformat()},
                           ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass


def _forget_provider_if_matched(name: str):
    """当前供应商调用失败时，若它是记忆中的供应商，则清除记忆（下次从 config.env 优先）"""
    if name != _get_remembered_provider():
        return
    with _memory_lock:
        try:
            if _MEMORY_FILE.exists():
                _MEMORY_FILE.unlink()
        except Exception:
            pass


def _get_provider_order() -> list:
    """返回供应商尝试顺序：记忆的排最前，其次 config.env，其余按文件顺序。"""
    providers = _load_providers()
    remembered = _get_remembered_provider()
    if remembered:
        for i, p in enumerate(providers):
            if p["name"] == remembered:
                providers.insert(0, providers.pop(i))
                break
    return providers


def _http_error_desc(status_code: int) -> str:
    """HTTP 错误码 -> 友好描述"""
    if status_code == 400:
        return "参数错误或提示词内容违规"
    if status_code == 401:
        return "API Key 无效"
    if status_code == 402:
        return "余额不足"
    if status_code == 500:
        return "服务端内部错误"
    if status_code == 502:
        return "AI 生成服务暂时不可用（502），请稍后重试"
    return f"HTTP {status_code}"


# ============================================================
# 工具函数
# ============================================================
def _try_load_font(size: int):
    """尝试加载一个支持中文的系统字体；失败则降级到 PIL 默认字体。"""
    if not HAS_PIL:
        return None
    from PIL import ImageFont
    # 常见字体路径
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for fp in candidates:
        if os.path.isfile(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


def composite_images(image_paths: list, output_path: str = None,
                      draw_labels: bool = True) -> str:
    """
    将多张参考图合成一张组合图（最多 6 张）。

    布局规则：
      - 1 张：原图返回
      - 2 张：左右并排（2 列 × 1 行）
      - 3~4 张：2×2 网格（3 张时最后一行居中）
      - 5~6 张：3×2 网格（5 张时最后一行居中）

    每张图统一 resize 为 512×512，2px 间隔，白色背景填充。
    draw_labels=True 时绘制"图1/图2..."红底白字角标。
    """
    if not HAS_PIL:
        raise RuntimeError("需要安装 Pillow: pip install Pillow")
    from PIL import ImageDraw

    paths = [p for p in image_paths if p and os.path.isfile(p)]
    if not paths:
        raise FileNotFoundError("没有有效的参考图")
    if len(paths) == 1:
        return paths[0]

    CELL = 512
    GAP = 2
    BG_COLOR = (255, 255, 255)

    label_font = _try_load_font(28) if draw_labels else None

    def _paste_with_label(canvas, img, box, idx):
        """贴图 + 画图位角标"""
        canvas.paste(img, box)
        if draw_labels:
            try:
                draw = ImageDraw.Draw(canvas)
                label = f"图{idx + 1}"
                pad_x, pad_y = 12, 8
                try:
                    tb = draw.textbbox((0, 0), label, font=label_font)
                    tw, th = tb[2] - tb[0], tb[3] - tb[1]
                except AttributeError:
                    tw, th = draw.textsize(label, font=label_font)
                rx0, ry0 = box[0] + 6, box[1] + 6
                rx1, ry1 = rx0 + tw + pad_x * 2, ry0 + th + pad_y * 2
                draw.rectangle([rx0, ry0, rx1, ry1], fill=(220, 38, 38))
                draw.text((rx0 + pad_x, ry0 + pad_y - 2), label,
                          fill=(255, 255, 255), font=label_font)
            except Exception:
                pass

    count = len(paths)
    if count == 2:
        cols, rows = 2, 1
    elif count <= 4:
        cols, rows = 2, 2
    else:
        cols, rows = 3, 2

    w = CELL * cols + GAP * (cols - 1)
    h = CELL * rows + GAP * (rows - 1)
    canvas = Image.new("RGB", (w, h), BG_COLOR)

    # 最后一行不满时水平居中，避免右侧留空
    last_row_start = (count - 1) // cols * cols
    last_row_count = count - last_row_start
    row_w = last_row_count * CELL + GAP * (last_row_count - 1)
    row_start_x = (w - row_w) // 2 if last_row_count < cols else 0

    for i, p in enumerate(paths):
        img = Image.open(p).convert("RGB").resize((CELL, CELL), Image.LANCZOS)
        row, col = divmod(i, cols)
        if i < last_row_start:
            x = col * (CELL + GAP)
        else:
            x = row_start_x + (i - last_row_start) * (CELL + GAP)
        y = row * (CELL + GAP)
        _paste_with_label(canvas, img, (x, y), i)

    output_path = output_path or tempfile.mktemp(suffix=".png")
    canvas.save(output_path, "PNG")
    return output_path


# ============================================================
# API 调用
# ============================================================
def call_text_to_image(provider: dict, prompt: str, size: str, n: int,
                        response_format: str = "url") -> tuple:
    """
    文生图接口 POST /v1/images/generations
    返回 (response_json, response_format)
    size / n 由调用方显式传入（来自 Web 表单或 CLI 参数）
    """
    payload = {
        "model": provider["model"],
        "prompt": prompt,
        "n": n,
        "size": size,
        "response_format": response_format,
    }

    url = f"{provider['base_url']}/v1/images/generations"
    headers = {"Authorization": f"Bearer {provider['api_key']}"}
    resp = requests.post(url, headers=headers, json=payload, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json(), response_format


def call_image_edit(provider: dict, prompt: str, reference_images: list, n: int,
                     size: str = "", response_format: str = "url") -> tuple:
    """
    图生图接口 POST /v1/images/edits（multipart/form-data）

    多张参考图直接以多个 image 字段上传（不做后端合成），
    中转站按上传顺序识别为 图1、图2...，模型输出一张编辑后的完整图片。
    n / size 由调用方显式传入（来自 Web 表单或 CLI 参数）；
    size 非空时作为独立参数发送，与提示词比例描述形成双重保险
    """
    for img_path in reference_images:
        if not os.path.isfile(img_path):
            raise FileNotFoundError(f"参考图文件不存在: {img_path}")

    url = f"{provider['base_url']}/v1/images/edits"

    # 多张参考图 → 全部作为多个 image 字段直接上传（上传顺序即 图1/图2...）
    file_fields = [
        ("prompt", (None, prompt)),
        ("model", (None, provider["model"])),
        ("n", (None, str(n))),
        ("response_format", (None, response_format)),
    ]
    if size:
        file_fields.append(("size", (None, size)))
    for img_path in reference_images:
        with open(img_path, "rb") as f:
            img_data = f.read()
        mime = mimetypes.guess_type(img_path)[0] or "image/png"
        file_fields.append(
            ("image", (os.path.basename(img_path), img_data, mime))
        )

    headers = {"Authorization": f"Bearer {provider['api_key']}"}
    resp = requests.post(
        url,
        headers=headers,
        files=file_fields,
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json(), response_format


# ============================================================
# 任务处理（核心）
# ============================================================
def process_single_task(prompt: str, reference_images: list,
                        size: str, n: int,
                        response_format: str = "url") -> dict:
    """
    处理单个生成任务。

    参数:
        prompt: 描述词
        reference_images: 参考图路径列表（空 list=文生图，非空=图生图），最多 6 张
        size: 图片尺寸（必传，由 Web/CLI 调用方决定）
        n: 生成数量（必传，由 Web/CLI 调用方决定）
        response_format: 返回格式

    返回:
        {
            "status": "success" | "failed",
            "error": "错误信息",
            "image_urls": ["https://...", ...]
        }
    """
    if not size:
        raise ValueError("size 必传（由调用方提供）")
    if not n or n < 1:
        raise ValueError("n 必传且 >= 1")
    result = {
        "status": "",
        "error": "",
        "generated_files": [],
        "image_urls": [],
    }

    refs = reference_images or []
    is_edit = len(refs) > 0

    # 多 API 自动切换：依次尝试所有供应商，哪个成功就用哪个（并记住它）
    errors = []
    for provider in _get_provider_order():
        # 网络层异常（代理断连等）自动重试 1 次：中转站波动时第二次往往能拿到真实业务响应
        for attempt in range(2):
            try:
                if is_edit:
                    resp_data, _ = call_image_edit(provider, prompt, refs, n, size, response_format)
                else:
                    resp_data, _ = call_text_to_image(provider, prompt, size, n, response_format)

                data_items = resp_data.get("data", [])
                if not data_items:
                    raise RuntimeError("接口返回空数据")

                # 纯 URL 模式：直接存 AI 平台返回的图片链接，不落地到本地磁盘
                image_urls = []
                for item in data_items:
                    url = item.get("url", "")
                    if not url:
                        raise RuntimeError("接口未返回图片 URL（仅返回 base64，已禁用本地落地）")
                    image_urls.append(url)

                # 成功：记住该供应商，下次优先使用
                _remember_provider(provider["name"])
                result["status"] = "success"
                result["image_urls"] = image_urls
                result["generated_files"] = []
                return result

            except requests.exceptions.ProxyError:
                if attempt == 0:
                    time.sleep(1)  # 隧道/中转站波动，稍等后重试一次
                    continue
                errors.append(f"[{provider['name']}] 代理连接异常，请检查 frp 代理链路或稍后重试")
                _forget_provider_if_matched(provider["name"])
                break
            except requests.exceptions.ConnectionError:
                if attempt == 0:
                    time.sleep(1)
                    continue
                errors.append(f"[{provider['name']}] 网络连接中断，请稍后重试")
                _forget_provider_if_matched(provider["name"])
                break
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if hasattr(e, "response") and e.response is not None else 0
                errors.append(f"[{provider['name']}] {_http_error_desc(status_code)}")
                _forget_provider_if_matched(provider["name"])
                break
            except requests.exceptions.Timeout:
                errors.append(f"[{provider['name']}] 请求超时")
                _forget_provider_if_matched(provider["name"])
                break
            except requests.exceptions.RequestException as e:
                errors.append(f"[{provider['name']}] 网络错误: {str(e)}")
                _forget_provider_if_matched(provider["name"])
                break
            except Exception as e:
                errors.append(f"[{provider['name']}] {str(e)}")
                _forget_provider_if_matched(provider["name"])
                break

    result["status"] = "failed"
    result["error"] = "；".join(errors) if errors else "未知错误"
    return result
