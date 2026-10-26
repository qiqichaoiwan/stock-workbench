"""LLM 网关:用户配置优先,管理员系统配置兜底。"""
import ipaddress
import logging
import socket
import ssl
from urllib.parse import urlsplit

import httpcore
import httpx
from httpcore._backends.sync import SyncBackend
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..portfolio.models import UserLlmConfig
from ..shared.security import decrypt_secret

log = logging.getLogger("llm_gateway")


def _normalize_base_url(base_url: str) -> str:
    value = base_url.strip()
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or "?" in value or "#" in value or "\\" in value
                or any(ord(c) <= 32 or ord(c) == 127 for c in value)
                or "%" in parsed.netloc or "%" in parsed.path
                or any(p in (".", "..") for p in parsed.path.split("/"))):
            raise ValueError
        parsed.port  # 拒绝非法端口。
        return str(httpx.URL(value)).rstrip("/")
    except (ValueError, httpx.InvalidURL):
        raise ValueError("LLM 地址无效:仅支持无认证、查询和片段的 HTTP(S) Base URL") from None


def _local_approved(base_url: str) -> bool:
    return base_url in {_normalize_base_url(url) for url in settings.llm_local_base_urls}


def _check_ip(address: str, local: bool) -> None:
    ip = ipaddress.ip_address(address)
    # IPv4 映射、隧道、作用域地址不作为本地例外,避免不同协议栈解释差异。
    if ("%" in address or (isinstance(ip, ipaddress.IPv6Address)
            and (ip.ipv4_mapped or ip.sixtofour or ip.teredo))):
        raise ValueError("禁止访问内网或特殊地址")
    if local and ip.is_loopback:
        return
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise ValueError("禁止访问内网或特殊地址;本机模型须由管理员批准完整 Base URL(含端口和路径)")


def _resolve_addresses(host: str, port: int, local: bool) -> list[str]:
    try:
        addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM)))
    except OSError:
        raise ValueError("LLM 域名解析失败") from None
    if not addresses:
        raise ValueError("LLM 域名解析无结果")
    for address in addresses:
        _check_ip(address, local)
    return addresses


def _validate_base_url(base_url: str, allow_local: bool = False) -> str:
    # 兼容旧调用参数,但 allow_local 永远不能扩张管理员白名单。
    normalized = _normalize_base_url(base_url)
    local = _local_approved(normalized)
    url = httpx.URL(normalized)
    if url.scheme != "https" and not local:
        raise ValueError("LLM 地址必须使用 HTTPS;本机 HTTP 仅允许管理员白名单内的完整 Base URL(含端口和路径)")
    _resolve_addresses(url.host, url.port or (443 if url.scheme == "https" else 80), local)
    return normalized


class _ApprovedNetworkBackend(httpcore.NetworkBackend):
    """在实际 TCP 建连时检查全部 DNS 结果,只向批准的数值 IP 建连。

    URL/Host 不变,httpcore 因而仍使用原域名进行 TLS SNI 与证书校验。
    """
    def __init__(self, base_url: str):
        self.url = httpx.URL(base_url)
        self.local = _local_approved(base_url)
        self.backend = SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host != self.url.host or port != (self.url.port or (443 if self.url.scheme == "https" else 80)):
            raise ValueError("禁止连接非配置的 LLM 目标")
        addresses = _resolve_addresses(host, port, self.local)
        for index, address in enumerate(addresses):
            try:
                return self.backend.connect_tcp(address, port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout):
                if index == len(addresses) - 1:
                    raise

    def connect_unix_socket(self, *args, **kwargs):
        raise ValueError("不允许 LLM Unix socket")


class _LlmTransport(httpx.HTTPTransport):
    def __init__(self, base_url: str):
        super().__init__(trust_env=False)
        self._pool.close()
        # httpx 0.28.1 不暴露 network_backend,仅替换本实例 pool,不修改全局解析器。
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=_ApprovedNetworkBackend(base_url), retries=0)


def _http_client(base_url: str, timeout: float) -> httpx.Client:
    return httpx.Client(transport=_LlmTransport(base_url), follow_redirects=False,
                        trust_env=False, timeout=timeout)


def resolve_config(db: Session, user_id: int) -> dict:
    cfg = db.execute(select(UserLlmConfig).where(
        UserLlmConfig.user_id == user_id)).scalar_one_or_none()
    if cfg and cfg.enabled and cfg.base_url and cfg.model:
        try:
            base_url = _validate_base_url(cfg.base_url)
        except ValueError as exc:
            log.warning("blocked unsafe user LLM URL: %s", exc)
        else:
            key = decrypt_secret(cfg.api_key_cipher)
            if key:
                return {"base_url": base_url, "api_key": key, "model": cfg.model,
                        "temperature": cfg.temperature, "source": "user"}
    if settings.default_llm_api_key and settings.default_llm_base_url and settings.default_llm_model:
        try:
            base_url = _validate_base_url(settings.default_llm_base_url)
        except ValueError as exc:
            log.warning("blocked unsafe system default LLM URL: %s", exc)
        else:
            return {"base_url": base_url, "api_key": settings.default_llm_api_key,
                    "model": settings.default_llm_model, "temperature": 0.3, "source": "system"}
    return {"base_url": "", "api_key": "", "model": "", "temperature": 0.3,
            "source": "none", "enabled": False}


def chat(db: Session, user_id: int, system: str, user_content: str,
         json_mode: bool = False) -> tuple[str, str]:
    cfg = resolve_config(db, user_id)
    if cfg.get("source") == "none":
        raise RuntimeError("未配置可用大模型:请配置 HTTPS 端点,本机端点须由管理员批准完整 Base URL")
    from openai import OpenAI

    with OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], max_retries=0,
                http_client=_http_client(cfg["base_url"], 60)) as client:
        resp = client.chat.completions.create(
            model=cfg["model"], temperature=cfg.get("temperature", 0.3),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user_content}],
            response_format={"type": "json_object"} if json_mode else None)
        return resp.choices[0].message.content or "", cfg["model"]


def test_config(base_url: str, api_key: str, model: str,
                timeout: float = 8.0, allow_local: bool = False) -> tuple[bool, str]:
    try:
        base_url = _validate_base_url(base_url)
        from openai import OpenAI

        with OpenAI(base_url=base_url, api_key=api_key, max_retries=0,
                    http_client=_http_client(base_url, timeout)) as client:
            resp = client.chat.completions.create(
                model=model, max_tokens=8,
                messages=[{"role": "user", "content": "ping"}])
            return True, resp.choices[0].message.content or "ok"
    except ValueError as exc:
        return False, str(exc)[:200]
    except Exception:
        # SDK 异常可能包含上游响应/认证信息,不向用户或日志泄露。
        return False, "LLM 连接或响应失败(不跟随重定向);请检查管理员端点白名单、网络及模型配置"
