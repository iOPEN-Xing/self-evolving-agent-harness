"""课程只读工具边界：无通用路径、无命令执行、无评分目录入口。"""
import json
from pathlib import Path
TABLES=frozenset({'request','orders','ledger','channel'})
def read_table(workspace,table):
    if table not in TABLES: raise PermissionError('只允许读取预先定义的输入表')
    root=(Path(workspace)/'input').resolve(); path=(root/(table+'.json')).resolve()
    if path.parent!=root or path.is_symlink(): raise PermissionError('输入路径越界')
    return json.loads(path.read_text())
