"""pytest 全局隔离:固定临时 SQLite 库与测试 JWT 密钥。

pytest 会先加载 conftest 再导入测试模块,这里设置的 DATABASE_URL 对
app.db(导入时即绑定引擎)生效,避免任何测试误连生产 data/selectmoney.db。
"""
import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="smtest_")

os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DIR}/tests.db")
os.environ.setdefault("JWT_SECRET", "test-secret-for-auth-0123456789abcdef")
