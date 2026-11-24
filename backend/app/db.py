import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

数据目录 = Path(os.environ.get("数据目录", Path(__file__).resolve().parent.parent / "data"))
夹具目录 = Path(os.environ.get("夹具目录", Path(__file__).resolve().parent.parent.parent / "fixtures"))
库路径 = 数据目录 / "对拍.db"


def 连接() -> sqlite3.Connection:
    数据目录.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(库路径, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    # 写锁争用由 SQLite 排队等待，避免并发装载/补记直接双成功
    c.execute("PRAGMA busy_timeout = 30000")
    return c


@contextmanager
def 写事务():
    """独占写事务：装载、对齐重算、迟到补记同一票提交。

    BEGIN IMMEDIATE 在事务开头先取写锁，把同一业务键上的并发装载、
    补记、重对齐串行化；任一环节失败整票回滚，不落半截悬账/半截差行。
    """
    c = 连接()
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()
