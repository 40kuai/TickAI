"""api 测试共享环境配置。

db.py 的 engine 在首次导入时即绑定 DB_PATH(settings 导入时解析一次)。
test_security_fixes 与 test_selfheal_routes 各自 setdefault OPS_DB_PATH,
pytest 按文件名字典序收集时谁先导入 db 谁生效,存在 DB 路径竞态。
此 conftest 在收集任何 api 测试模块之前强制指定同一个隔离测试 DB,
消除竞态并保证整包一致(与 tests/selfheal/conftest.py 同理)。
"""
import os
from pathlib import Path

os.environ["OPS_DB_PATH"] = "/tmp/opsticket_test/api.db"
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)
