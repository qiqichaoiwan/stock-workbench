"""P1 安全回归:独立临时配置,不访问真实网络或生产数据库。"""
import os
import socket
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.fernet import Fernet

_TEST_DIR = tempfile.mkdtemp(prefix="p1_security_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DIR}/security.db"
os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
os.environ["JWT_SECRET"] = "p1-test-only-jwt-secret-0123456789abcdef"

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import settings
from app.portfolio import api
from app.shared import llm_gateway as gateway
from app.shared.security import hash_password, verify_password


@pytest.fixture(autouse=True)
def isolated_network(monkeypatch):
    # 替换网关的 socket 引用,不全局 monkeypatch socket.getaddrinfo。
    resolver = Mock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    monkeypatch.setattr(gateway, "socket", SimpleNamespace(
        getaddrinfo=resolver, SOCK_STREAM=socket.SOCK_STREAM))
    monkeypatch.setattr(settings, "llm_local_base_urls", [])
    monkeypatch.setattr(settings, "default_llm_api_key", "")
    return resolver


def dns(addresses):
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET,
             socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses]


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "10.0.0.1", "169.254.169.254",
                                    "100.64.0.1", "0.0.0.0", "224.0.0.1", "fc00::1",
                                    "fe80::1", "::", "::ffff:127.0.0.1", "2001:db8::1"])
def test_special_addresses_denied(ip, isolated_network):
    isolated_network.return_value = dns([ip])
    with pytest.raises(ValueError):
        gateway._validate_base_url("https://model.example/v1", allow_local=True)


def test_local_exact_whitelist(monkeypatch, isolated_network):
    isolated_network.return_value = dns(["127.0.0.1"])
    with pytest.raises(ValueError):
        gateway._validate_base_url("http://localhost:11434/v1", allow_local=True)
    monkeypatch.setattr(settings, "llm_local_base_urls", ["http://LOCALHOST:11434/v1/"])
    assert gateway._validate_base_url("http://localhost:11434/v1/") == "http://localhost:11434/v1"
    for url in ["http://localhost:11435/v1", "http://localhost:11434/v2",
                "http://127.0.0.1:11434/v1", "http://localhost:11434/v1/../other"]:
        with pytest.raises(ValueError):
            gateway._validate_base_url(url, allow_local=True)
    backend = gateway._ApprovedNetworkBackend("http://localhost:11434/v1")
    backend.backend = Mock()
    backend.connect_tcp("localhost", 11434)
    assert backend.backend.connect_tcp.call_args.args[:2] == ("127.0.0.1", 11434)
    isolated_network.return_value = dns(["10.0.0.1"])
    with pytest.raises(ValueError):
        backend.connect_tcp("localhost", 11434)


def test_dns_rebinding_blocked(isolated_network):
    gateway._validate_base_url("https://model.example/v1")
    backend = gateway._ApprovedNetworkBackend("https://model.example/v1")
    backend.backend = Mock()
    isolated_network.return_value = dns(["8.8.8.8", "127.0.0.1"])
    with pytest.raises(ValueError):
        backend.connect_tcp("model.example", 443)
    backend.backend.connect_tcp.assert_not_called()


def test_pinned_ip_preserves_host_and_tls(isolated_network):
    class Stream:
        def __init__(self):
            self.sni = None
            self.sent = b""

        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            self.sni = server_hostname
            return self

        def get_extra_info(self, info):
            return None

        def write(self, data, timeout=None):
            self.sent += data

        def read(self, max_bytes, timeout=None):
            return b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"

        def close(self):
            pass

    transport = gateway._LlmTransport("https://model.example/v1")
    backend = transport._pool._network_backend
    stream = Stream()
    backend.backend = Mock()
    backend.backend.connect_tcp.return_value = stream
    with httpx.Client(transport=transport, trust_env=False) as client:
        assert client.get("https://model.example/v1/test").text == "ok"
    assert backend.backend.connect_tcp.call_args.args[:2] == ("8.8.8.8", 443)
    assert stream.sni == "model.example"
    assert b"Host: model.example\r\n" in stream.sent


def test_sdk_does_not_follow_redirects(monkeypatch):
    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "http://127.0.0.1/private"})

    monkeypatch.setattr(gateway, "_LlmTransport", lambda url: httpx.MockTransport(redirect))
    client = gateway._http_client("https://model.example/v1", 1)
    assert client.follow_redirects is False
    assert client._trust_env is False
    client.close()
    assert not gateway.test_config("https://model.example/v1", "test-key", "test-model")[0]
    assert len(requests) == 1
    assert requests[0].url.host == "model.example"
    monkeypatch.setattr(gateway, "resolve_config", lambda db, uid: {
        "source": "user", "base_url": "https://model.example/v1", "api_key": "test-key", "model": "test"})
    with pytest.raises(Exception):
        gateway.chat(None, 1, "system", "user")
    assert len(requests) == 2


def test_save_test_resolve_same_local_policy(monkeypatch, isolated_network):
    isolated_network.return_value = dns(["127.0.0.1"])
    db = Mock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    body = api.LlmIn(base_url="http://localhost:11434/v1", api_key="test", model="test")
    with pytest.raises(api.HTTPException) as exc:
        api.save_llm(body, 1, db)
    assert exc.value.status_code == 400
    assert api.test_llm(body, 1, db)["ok"] is False
    monkeypatch.setattr(settings, "default_llm_base_url", body.base_url)
    monkeypatch.setattr(settings, "default_llm_api_key", "test")
    assert gateway.resolve_config(db, 1)["source"] == "none"
    monkeypatch.setattr(settings, "llm_local_base_urls", [body.base_url])
    api.save_llm(body, 1, db)
    monkeypatch.setattr(gateway, "_LlmTransport", lambda url: httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 0, "model": "test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}]})))
    assert api.test_llm(body, 1, db)["ok"] is True
    assert gateway.resolve_config(db, 1)["source"] == "system"
    db.execute.return_value.scalar_one_or_none.return_value = SimpleNamespace(
        enabled=True, base_url=body.base_url, model="test", temperature=.3,
        api_key_cipher=api.encrypt_secret("test"))
    assert gateway.resolve_config(db, 1)["source"] == "user"


@pytest.mark.parametrize("schema,field,extra", [
    (api.RegisterIn, "password", {"username": "test"}),
    (api.UserCreateIn, "password", {"username": "test"}),
    (api.ChangePasswordIn, "new_password", {"old_password": "old-password"}),
    (api.ResetPasswordIn, "new_password", {}),
])
def test_password_schemas_utf8_limit(schema, field, extra):
    with pytest.raises(ValidationError):
        schema(**extra, **{field: "密" * 25})
    assert getattr(schema(**extra, **{field: "密" * 24}), field) == "密" * 24
    assert getattr(schema(**extra, **{field: "a" * 72}), field) == "a" * 72
    app = FastAPI()

    def endpoint(body):
        return {"ok": True}

    endpoint.__annotations__["body"] = schema
    app.router.route_class = api._PasswordSafeRoute
    app.post("/password")(endpoint)
    with TestClient(app) as client:
        response = client.post("/password", json={**extra, field: "密" * 25})
        assert response.status_code == 422
        assert "input" not in response.json()["detail"][0]
        assert "密" * 25 not in response.text


def test_password_hash_boundary_and_old_tail():
    password = "密" * 24
    hashed = hash_password(password)
    assert verify_password(password, hashed)
    for overlong in [password + "a", password + "b", "a" * 73]:
        with pytest.raises(ValueError, match="72 字节"):
            hash_password(overlong)
        assert verify_password(overlong, hashed) is False
    prefix_hash = hash_password("a" * 72)
    assert not verify_password("a" * 72 + "first-tail", prefix_hash)
    assert not verify_password("a" * 72 + "other-tail", prefix_hash)
