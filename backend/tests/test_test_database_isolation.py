"""Test collection must not touch a database supplied by the environment."""
import os
from pathlib import Path
import subprocess
import sys


def test_collection_ignores_external_database_url(tmp_path):
    sentinel = tmp_path / 'must-not-exist.db'
    env = os.environ.copy()
    env['DATABASE_URL'] = f'sqlite:///{sentinel}'
    env['JWT_SECRET'] = 'test-secret-for-auth-0123456789abcdef'
    backend = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '--collect-only', '-q',
         'tests/test_audit_round2.py', 'tests/test_auth_permissions.py'],
        cwd=backend, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not sentinel.exists(), 'pytest collection wrote to DATABASE_URL from the parent environment'
