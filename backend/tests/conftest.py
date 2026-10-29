"""pytest 全局隔离:固定临时 SQLite 库与测试 JWT 密钥。

pytest 会先加载 conftest 再导入测试模块,这里设置的 DATABASE_URL 对
app.db(导入时即绑定引擎)生效,避免任何测试误连生产 data/selectmoney.db。
"""
import os
import tempfile

from cryptography.fernet import Fernet

_TMP_DIR = tempfile.mkdtemp(prefix="smtest_")

os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DIR}/tests.db")
os.environ.setdefault("JWT_SECRET", "test-secret-for-auth-0123456789abcdef")
# 双核实时适配器默认关闭:单元测试不得联网,也不得依赖外部行情源的可用性。
# 需要验证适配器的测试直接调用 dual_core_feed 并注入 provider(或 monkeypatch 本配置)。
os.environ["DUAL_CORE_FEED_ENABLED"] = "false"
# 与上面两项同理:在 app.config 首次导入前固定测试用 Fernet 密钥,
# 避免依赖测试模块导入顺序(test_cockpit 曾因 app.config 先被导入而拿到空 key)。
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
