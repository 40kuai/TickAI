"""selfheal 测试共享环境配置。

db.py 的 engine 在首次导入时即绑定 DB_PATH(settings 导入时解析一次)。
pytest 按文件名排序收集,test_actions 等模块会先于 test_models/test_orchestrator
导入 db,导致后者的 setdefault("OPS_DB_PATH") 失效而误连默认项目库。
此 conftest 在收集任何测试模块之前强制指定隔离的测试 DB,
避免测试写穿/删穿真实项目数据,并保证整个套件一致。
"""
import os

os.environ["OPS_DB_PATH"] = "/tmp/opsticket_test/selfheal.db"
