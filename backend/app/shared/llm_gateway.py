"""LLM 网关:用户自配大模型优先,系统兜底次之;OpenAI 兼容协议。"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..portfolio.models import UserLlmConfig
from ..shared.security import decrypt_secret

import ipaddress
from urllib.parse import urlparse


def _validate_base_url(base_url: str, allow_local: bool = False) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        if not (allow_local and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("LLM 地址必须使用 HTTPS；仅允许明确配置的本机模型使用 HTTP")
    host = parsed.hostname or ""
    try:
        ip = ipaddress.ip_address(host)
        if not (allow_local and ip.is_loopback):
            raise ValueError("禁止访问内网或特殊地址")
    except ValueError as exc:
        if "禁止" in str(exc):
            raise
    if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("LLM 地址无效")
    return base_url.rstrip("/")


log = logging.getLogger("llm_gateway")


def resolve_config(db: Session, user_id: int) -> dict:
    """返回该用户生效的 LLM 配置;未配置返回系统兜底(enabled=False 表示不可用)。"""
    cfg = db.execute(select(UserLlmConfig).where(
        UserLlmConfig.user_id == user_id)).scalar_one_or_none()
    if cfg and cfg.enabled and cfg.base_url and cfg.model:
        try:
            base_url = _validate_base_url(cfg.base_url, allow_local=False)
        except ValueError:
            log.warning("blocked unsafe user LLM URL")
            base_url = ""
        if base_url:
            key = decrypt_secret(cfg.api_key_cipher)
            if key:
                return {"base_url": base_url, "api_key": key, "model": cfg.model,
                        "temperature": cfg.temperature, "source": "user"}
    if settings.default_llm_api_key:
        base_url = _validate_base_url(settings.default_llm_base_url, allow_local=False)
        return {"base_url": base_url, "api_key": settings.default_llm_api_key,
                "model": settings.default_llm_model, "temperature": 0.3, "source": "system"}
    return {"base_url": "", "api_key": "", "model": "", "temperature": 0.3,
            "source": "none", "enabled": False}


def chat(db: Session, user_id: int, system: str, user_content: str,
         json_mode: bool = False) -> tuple[str, str]:
    """调用 LLM,返回 (content, model_name)。未配置或失败抛异常,由上层降级为纯规则输出。"""
    cfg = resolve_config(db, user_id)
    if cfg.get("source") == "none":
        raise RuntimeError("未配置大模型:请在「LLM设置」中填写,或由管理员配置系统默认模型")
    try:
        from openai import OpenAI

        client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], timeout=60)
        resp = client.chat.completions.create(
            model=cfg["model"],
            temperature=cfg.get("temperature", 0.3),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user_content}],
            response_format={"type": "json_object"} if json_mode else None,
        )
        content = resp.choices[0].message.content or ""
        return content, cfg["model"]
    except Exception as exc:  # noqa: BLE001
        log.warning("llm call failed via %s: %s", cfg.get("source"), exc)
        raise


def test_config(base_url: str, api_key: str, model: str,
                timeout: float = 8.0) -> tuple[bool, str]:
    """连通性测试:先做服务端 URL 校验(复用 _validate_base_url,拒绝 SSRF),
    再用短超时探测连通性,避免 /settings/llm/test 长时间挂起。"""
    try:
        base_url = _validate_base_url(base_url, allow_local=False)
        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        resp = client.chat.completions.create(
            model=model, max_tokens=8,
            messages=[{"role": "user", "content": "ping"}])
        return True, resp.choices[0].message.content or "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]
