"""P1 B02: 离线验证等待超时、资源上限、嵌套调用和重试。"""
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["JWT_SECRET"] = "p1-timeout-tests-only-not-a-production-secret"
os.environ["FERNET_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.shared import data_gateway as dg


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("timeout tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(dg.settings, "fetch_retry_times", 3)
    monkeypatch.setattr(dg.settings, "fetch_retry_wait_seconds", 0)
    monkeypatch.setattr(dg.settings, "fetch_timeout_seconds", 0.03)
    monkeypatch.setattr(dg, "_rate_limit", lambda: None)


@pytest.fixture
def slow_call():
    release = threading.Event()
    workers = []

    def slow():
        workers.append(threading.current_thread())
        release.wait(5)
        return "late result"

    yield slow, release, workers
    release.set()
    for worker in workers:
        worker.join(2)
        assert not worker.is_alive()


def test_success_args_and_daemon_worker():
    caller = threading.current_thread()

    def source(value, *, suffix):
        worker = threading.current_thread()
        assert worker is not caller
        assert worker.daemon
        return value + suffix

    assert dg._run_with_timeout(source, 1, "ok", suffix="!") == "ok!"


@pytest.mark.parametrize("error", [ValueError("source failure"), TimeoutError("source timeout")])
def test_exception_identity_and_capacity_release(monkeypatch, error):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(dg, "_timeout_slots", slots)
    workers = []

    def source():
        workers.append(threading.current_thread())
        raise error

    with pytest.raises(type(error)) as caught:
        dg._run_with_timeout(source, 1)
    assert caught.value is error
    workers[0].join(1)
    assert dg._run_with_timeout(lambda: "recovered", 1) == "recovered"


@pytest.mark.parametrize("timeout", [None, 0, -1])
def test_timeout_disabled_runs_inline_even_when_full(monkeypatch, timeout):
    monkeypatch.setattr(dg, "_timeout_slots", threading.BoundedSemaphore(0))
    caller = threading.current_thread()
    assert dg._run_with_timeout(threading.current_thread, timeout) is caller


def test_slow_call_returns_before_underlying_completion(slow_call):
    slow, release, workers = slow_call
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="underlying call continues"):
        dg._run_with_timeout(slow, 0.03)
    assert time.monotonic() - started < 0.5
    assert not release.is_set()
    assert len(workers) == 1 and workers[0].is_alive()


def test_repeated_timeouts_are_bounded_and_capacity_recovers(slow_call):
    slow, release, workers = slow_call
    for _ in range(dg._TIMEOUT_MAX_WORKERS):
        with pytest.raises(TimeoutError):
            dg._run_with_timeout(slow, 0.03)
    assert len(workers) == dg._TIMEOUT_MAX_WORKERS
    assert all(worker.daemon and worker.is_alive() for worker in workers)
    started = time.monotonic()
    for _ in range(40):
        with pytest.raises(RuntimeError, match="capacity exhausted"):
            dg._run_with_timeout(slow, 10)
    assert time.monotonic() - started < 0.5
    assert len(workers) == dg._TIMEOUT_MAX_WORKERS
    release.set()
    for worker in workers:
        worker.join(1)
    assert dg._run_with_timeout(lambda: "recovered", 1) == "recovered"


def test_thread_start_failure_releases_capacity(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(dg, "_timeout_slots", slots)

    def failed_start(self):
        raise RuntimeError("cannot start thread")

    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", failed_start)
        with pytest.raises(RuntimeError, match="cannot start thread"):
            dg._run_with_timeout(lambda: None, 1)
    assert dg._run_with_timeout(lambda: "ok", 1) == "ok"


def test_nested_decorators_share_one_slot(monkeypatch):
    monkeypatch.setattr(dg, "_timeout_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(dg.settings, "fetch_timeout_seconds", 1)

    @dg.resilient("inner")
    def inner():
        return threading.current_thread()

    @dg.resilient("outer")
    def outer():
        assert inner() is threading.current_thread()
        return "nested ok"

    assert outer() == "nested ok"


def test_nested_slow_call_still_obeys_outer_wait(monkeypatch, slow_call):
    slow, release, workers = slow_call
    monkeypatch.setattr(dg, "_timeout_slots", threading.BoundedSemaphore(1))
    inner = dg.resilient("inner")(slow)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        dg._run_with_timeout(inner, 0.03)
    assert time.monotonic() - started < 0.5
    assert len(workers) == 1 and workers[0].is_alive()


def test_resilient_retries_errors_and_preserves_backoff(monkeypatch):
    attempts = []
    waits = []
    monkeypatch.setattr(dg.settings, "fetch_retry_wait_seconds", 2)
    monkeypatch.setattr(dg.time, "sleep", waits.append)

    @dg.resilient("flaky")
    def source():
        attempts.append(1)
        if len(attempts) < 3:
            raise ValueError("retry me")
        return "ok"

    assert source() == "ok"
    assert len(attempts) == 3
    assert waits == [2, 4]


def test_resilient_retries_timeout_and_can_succeed(slow_call):
    slow, release, workers = slow_call
    attempts = []

    @dg.resilient("timeout_then_success")
    def source():
        attempts.append(1)
        return slow() if len(attempts) == 1 else "ok"

    assert source() == "ok"
    assert len(attempts) == 2
    assert workers[0].is_alive()


def test_resilient_timeout_exhaustion_is_bounded(slow_call):
    slow, release, workers = slow_call
    source = dg.resilient("always_slow")(slow)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="always_slow") as caught:
        source()
    assert isinstance(caught.value.__cause__, TimeoutError)
    assert len(workers) == 3
    assert time.monotonic() - started < 0.6


def test_daemon_timeout_does_not_block_process_exit():
    code = """
import socket
import threading
from app.shared.data_gateway import _run_with_timeout
socket.socket.connect = lambda *args: (_ for _ in ()).throw(AssertionError('network forbidden'))
try:
    _run_with_timeout(threading.Event().wait, 0.01)
except TimeoutError:
    print('timed out', flush=True)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=Path(__file__).resolve().parent.parent,
        env=os.environ.copy(), capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "timed out" in result.stdout
