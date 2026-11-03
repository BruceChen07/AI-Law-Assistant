from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_app_embedder
from app.core import mineru_models
from app.core.config import get_config


def _mineru_health(deep: bool = False) -> dict:
    """汇总 MinerU 本地模型状态，供健康检查与告警使用。

    只做文件存在性/大小检查；``deep=True`` 时额外按 manifest 校验 sha256。
    """
    cfg = get_config()
    mineru_cfg = cfg.get("mineru") or {}
    source = str(mineru_cfg.get("model_source") or "").strip().lower()
    download = mineru_models.get_status()
    payload = {
        "model_source": source,
        "local_mode": source == "local",
        "deep_checked": bool(deep),
        "download": {
            "running": bool(download.get("running")),
            "stage": download.get("stage", "idle"),
            "ok": bool(download.get("ok")),
            "error": download.get("error", ""),
            "started_at": download.get("started_at", ""),
            "finished_at": download.get("finished_at", ""),
        },
    }
    if source != "local":
        payload["local_ready"] = False
        return payload
    try:
        status = mineru_models.inspect_local_models(
            cfg, deep=deep, verify_sha256=deep)
    except Exception as exc:  # pragma: no cover - defensive, health must not 500
        payload["local_ready"] = False
        payload["error"] = str(exc)
        return payload
    payload.update({
        "local_ready": bool(status.get("ok")),
        "root": status.get("root", ""),
        "config_path": status.get("config_path", ""),
        "manifest_path": status.get("manifest_path", ""),
        "required_kinds": status.get("required_kinds", []),
        "missing": status.get("missing", []),
        "optional_missing": status.get("optional_missing", []),
        "message": status.get("message", ""),
    })
    return payload


def build_router():
    router = APIRouter()

    @router.get("/health")
    def health(embedder=Depends(get_app_embedder)):
        status = embedder.get_registry_status()
        return {
            "status": "ok",
            "embedding_ready": status["ready"],
            "embedding_default_language": status["default_language"],
            "embedding_languages": status["languages"],
            "mineru": _mineru_health(),
        }

    @router.get("/health/mineru")
    def mineru_health(deep: bool = Query(
            False, description="按 manifest 逐文件校验 sha256（较慢）")):
        payload = _mineru_health(deep=deep)
        if payload.get("local_ready"):
            payload["status"] = "ok"
        elif payload.get("local_mode") and payload.get("download", {}).get("running"):
            payload["status"] = "degraded"
        else:
            payload["status"] = "unavailable"
        return payload

    return router
