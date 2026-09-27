"""
BOLI AI 批量图片生成 — 核心引擎
===================================
包含 AI API 调用、多供应商故障转移、图片合成、任务解析等核心逻辑。
被 Web 服务（app.api.routes.tasks）使用。
"""

import os
import io
import json
import time
import mimetypes
import threading
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


def _load_providers(change: int = None) -> list:
    """加载所有可用 API 供应商配置。

    优先从数据库 api_providers 表读取（Web 平台管理）；数据库不可用或为空时
    回退到 config.env / config-*.env 文件（CLI 首次运行等场景）。
    change 不为 None 时只返回与用户线路分组一致的供应商（用户的 change 与
    供应商的 change 相等才匹配）。
    敏感配置仍可被系统环境变量覆盖（生产 systemd EnvironmentFile 注入）。
    """
    # ---- 优先：数据库（Web 平台已 init_db + 种子迁移）----
    try:
        from app.db.database import list_enabled_providers
        rows = list_enabled_providers(change)
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


def _get_provider_order(change: int = None) -> list:
    """返回供应商尝试顺序：记忆的排最前，其次 config.env，其余按文件顺序。"""
    providers = _load_providers(change)
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
    if status_code == 429:
        return "请求过于频繁被限流（429），请降低并发或稍后重试"
    if status_code == 500:
        return "服务端内部错误"
    if status_code == 502:
        return "AI 生成服务暂时不可用（502），请稍后重试"
    if status_code == 503:
        return "服务暂时过载（503），请稍后重试"
    return f"HTTP {status_code}"


def _provider_tag(provider: dict) -> str:
    """错误信息里的供应商标识：用数据库 id（如 [2]），不把供应商名称暴露给终端用户。
    仅 env 文件回退场景（无 id）才退回名称。"""
    pid = provider.get("id")
    if pid is not None:
        return str(pid)
    return str(provider.get("name") or "env")


# 瞬时性 HTTP 错误：值得等一小会儿重试，而不是直接判失败换供应商
# （429=限流、500/502/503=服务端瞬时错误/网关过载，中转站高并发下常见，稍等重试往往能成功）
_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503}

# 网络层异常（代理断连 / 连接中断）的重试等待：最多重试 2 次，等待时间递增。
# 隧道抖动通常持续几秒，等 1 秒往往不够恢复，退避能给足自愈时间。
_NETWORK_RETRY_DELAYS = (1.0, 3.0)




# ============================================================
# API 调用
# ============================================================
# 每线程复用一个 Session（连接池）：走隧道时避免每次调用重复
# TCP+TLS 握手，单次请求省 1~2 秒，并发吞吐明显提升
_session_local = threading.local()


def _get_session() -> "requests.Session":
    session = getattr(_session_local, "session", None)
    if session is None:
        session = requests.Session()
        _session_local.session = session
    return session


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
    resp = _get_session().post(url, headers=headers, json=payload, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json(), response_format


# ============================================================
# 参考图上传压缩（第一优先：AI 能看清参考图细节，如衣服上的文字）
# ============================================================
# 只减体积不减清晰度：最长边 1536 + JPEG 质量 90 + 缩放后轻微锐化，
# PNG 图无损保留。参数可用环境变量调整（如文字看不清可提到 2048/95）
REF_IMAGE_MAX_EDGE = int(os.getenv("REF_IMAGE_MAX_EDGE", "1536"))
REF_IMAGE_JPEG_QUALITY = int(os.getenv("REF_IMAGE_JPEG_QUALITY", "90"))
_PNG_KEEP_MAX_BYTES = 1536 * 1024  # PNG 缩放后不超过该体积则保留 PNG（无损，文字最锐）


def prepare_reference_image(img_path: str) -> tuple:
    """把参考图压成适合上传的 (bytes, mime, filename)。

    规则：
    - 只缩不放：原图最长边 <= REF_IMAGE_MAX_EDGE 时保持原尺寸
    - 缩放后做轻微锐化（UnsharpMask），弥补缩图导致的文字边缘发软
    - 原图是 PNG 且压缩后体积不大（<=1.5MB）时保留 PNG（无损）
    - 其余转 JPEG 质量 REF_IMAGE_JPEG_QUALITY（透明通道铺白底）
    - 压缩结果比原图还大时直接用原图字节（保证只减不增）
    PIL 不可用或解析失败时原样返回文件原始字节。
    """
    fname = os.path.basename(img_path)
    with open(img_path, "rb") as f:
        raw = f.read()

    if not HAS_PIL:
        mime = mimetypes.guess_type(img_path)[0] or "application/octet-stream"
        return raw, mime, fname

    from PIL import Image, ImageOps, ImageFilter
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        mime = mimetypes.guess_type(img_path)[0] or "application/octet-stream"
        return raw, mime, fname

    # 按 EXIF 方向摆正（手机拍摄的照片常见旋转标记）
    img = ImageOps.exif_transpose(img)
    is_png = (img.format == "PNG") or fname.lower().endswith(".png")

    # 只缩不放
    w, h = img.size
    if max(w, h) > REF_IMAGE_MAX_EDGE:
        scale = REF_IMAGE_MAX_EDGE / max(w, h)
        img = img.resize(
            (max(1, round(w * scale)), max(1, round(h * scale))),
            Image.LANCZOS,
        )
        # 轻微锐化，把缩图后发软的文字边缘"提"回来
        try:
            img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=2))
        except Exception:
            pass

    stem = os.path.splitext(fname)[0] or "image"

    # PNG 优先无损保留（文字/线条最锐）
    if is_png:
        try:
            buf = io.BytesIO()
            img.save(buf, "PNG", optimize=True)
            data = buf.getvalue()
            if len(data) <= _PNG_KEEP_MAX_BYTES:
                return data, "image/png", stem + ".png"
        except Exception:
            pass

    # 转 JPEG（透明通道铺白底，避免黑底）
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=REF_IMAGE_JPEG_QUALITY, optimize=True)
    data = buf.getvalue()
    if len(data) >= len(raw):
        # 压缩反而更大（本身就很小的图）：直接用原图
        mime = mimetypes.guess_type(img_path)[0] or "image/jpeg"
        return raw, mime, fname
    return data, "image/jpeg", stem + ".jpg"


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
            raise FileNotFoundError(f"参考图已失效，请重新上传：{img_path}")

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
        # 上传前压缩参考图（保留文字细节的前提下削减体积，降低带宽/隧道压力）
        img_bytes, mime, fname = prepare_reference_image(img_path)
        file_fields.append(
            ("image", (fname, img_bytes, mime))
        )

    headers = {"Authorization": f"Bearer {provider['api_key']}"}
    resp = _get_session().post(
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
                        response_format: str = "url",
                        change: int = 0) -> dict:
    """
    处理单个生成任务。

    参数:
        prompt: 描述词
        reference_images: 参考图路径列表（空 list=文生图，非空=图生图），最多 6 张
        size: 图片尺寸（必传，由 Web/CLI 调用方决定）
        n: 生成数量（必传，由 Web/CLI 调用方决定）
        response_format: 返回格式
        change: 用户线路分组（与供应商的 change 相等才匹配使用）

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

    # 多 API 自动切换：依次尝试所有匹配线路的供应商，哪个成功就用哪个（并记住它）
    errors = []
    for provider in _get_provider_order(change):
        # 网络层异常（代理断连等）按 _NETWORK_RETRY_DELAYS 退避重试：
        # 隧道抖动持续几秒时，多给几次机会往往能拿到真实业务响应
        for attempt in range(len(_NETWORK_RETRY_DELAYS) + 1):
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
                if attempt < len(_NETWORK_RETRY_DELAYS):
                    time.sleep(_NETWORK_RETRY_DELAYS[attempt])  # 隧道波动，退避后重试
                    continue
                errors.append(f"[{_provider_tag(provider)}] 代理连接异常，请检查 frp 代理链路或稍后重试")
                _forget_provider_if_matched(provider["name"])
                break
            except requests.exceptions.ConnectionError:
                if attempt < len(_NETWORK_RETRY_DELAYS):
                    time.sleep(_NETWORK_RETRY_DELAYS[attempt])
                    continue
                errors.append(f"[{_provider_tag(provider)}] 网络连接中断，请稍后重试")
                _forget_provider_if_matched(provider["name"])
                break
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if hasattr(e, "response") and e.response is not None else 0
                if status_code in _RETRYABLE_HTTP_STATUS and attempt == 0:
                    # 瞬时性错误（限流/网关过载）：优先按 Retry-After 等待，缺省等 2 秒后重试一次
                    retry_after = 2.0
                    try:
                        ra = e.response.headers.get("Retry-After")
                        if ra and ra.isdigit():
                            retry_after = min(float(ra), 10.0)
                    except Exception:
                        pass
                    time.sleep(retry_after)
                    continue
                errors.append(f"[{_provider_tag(provider)}] {_http_error_desc(status_code)}")
                _forget_provider_if_matched(provider["name"])
                break
            except requests.exceptions.Timeout:
                errors.append(f"[{_provider_tag(provider)}] 请求超时")
                _forget_provider_if_matched(provider["name"])
                break
            except requests.exceptions.RequestException as e:
                errors.append(f"[{_provider_tag(provider)}] 网络错误: {str(e)}")
                _forget_provider_if_matched(provider["name"])
                break
            except FileNotFoundError as e:
                # 本地参考图缺失（非供应商故障）：所有供应商都会同样失败，直接返回，
                # 不套供应商前缀也不切换线路，避免错误信息重复、误导排查方向
                result["status"] = "failed"
                result["error"] = str(e)
                return result
            except Exception as e:
                errors.append(f"[{_provider_tag(provider)}] {str(e)}")
                _forget_provider_if_matched(provider["name"])
                break

    result["status"] = "failed"
    result["error"] = "；".join(errors) if errors else "未知错误"
    return result
