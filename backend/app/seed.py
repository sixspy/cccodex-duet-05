import sqlite3
from datetime import datetime, timezone
from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 轨规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 源登记 (
  源键 TEXT PRIMARY KEY,
  轨别 TEXT NOT NULL,
  名称 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 左轨流水 (
  事件编号 TEXT PRIMARY KEY,
  业务键 TEXT NOT NULL,
  金额分 INTEGER NOT NULL,
  源键 TEXT NOT NULL,
  事件时 TEXT NOT NULL,
  处理时 TEXT NOT NULL,
  命运 TEXT NOT NULL DEFAULT '已入左',
  批次编号 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 右轨流水 (
  事件编号 TEXT PRIMARY KEY,
  业务键 TEXT NOT NULL,
  金额分 INTEGER NOT NULL,
  源键 TEXT NOT NULL,
  事件时 TEXT NOT NULL,
  处理时 TEXT NOT NULL,
  命运 TEXT NOT NULL DEFAULT '已入右',
  批次编号 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 对齐差 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  业务键 TEXT NOT NULL,
  左金额分 INTEGER,
  右金额分 INTEGER,
  差型 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT '',
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 悬账单 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  业务键 TEXT NOT NULL,
  悬边 TEXT NOT NULL,
  金额分 INTEGER NOT NULL,
  事件编号 TEXT NOT NULL DEFAULT '',
  原因 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 对拍结论 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  左笔数 INTEGER NOT NULL,
  右笔数 INTEGER NOT NULL,
  对齐笔数 INTEGER NOT NULL,
  悬账笔数 INTEGER NOT NULL,
  是否通过 INTEGER NOT NULL,
  说明 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  轨别 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 补记记录 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  业务键 TEXT NOT NULL,
  事件编号 TEXT NOT NULL,
  金额分 INTEGER NOT NULL,
  形态 TEXT NOT NULL,
  旧悬边 TEXT NOT NULL DEFAULT '',
  旧悬编号 INTEGER,
  归档键 TEXT NOT NULL DEFAULT '',
  补记时刻 TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS 补记_业务键唯一 ON 补记记录(业务键);
CREATE TABLE IF NOT EXISTS 悬账归档 (
  归档键 TEXT PRIMARY KEY,
  业务键 TEXT NOT NULL,
  悬边 TEXT NOT NULL,
  金额分 INTEGER NOT NULL,
  事件编号 TEXT NOT NULL DEFAULT '',
  原因 TEXT NOT NULL,
  原悬编号 INTEGER NOT NULL,
  归档时刻 TEXT NOT NULL,
  补记编号 INTEGER
);
CREATE INDEX IF NOT EXISTS 归档_业务键 ON 悬账归档(业务键);
CREATE TRIGGER IF NOT EXISTS 归档_禁改 BEFORE UPDATE ON 悬账归档
BEGIN SELECT RAISE(ABORT, '悬账归档只读'); END;
CREATE TRIGGER IF NOT EXISTS 归档_禁删 BEFORE DELETE ON 悬账归档
BEGIN SELECT RAISE(ABORT, '悬账归档只读'); END;
"""

# 派生表（对齐差 / 悬账单）每键只应有一行；唯一索引把「重复开差 / 双悬」挡在数据库层。
_唯一索引 = [
    ("CREATE UNIQUE INDEX IF NOT EXISTS 差_业务键唯一 ON 对齐差(业务键)",
     "对齐差", "业务键"),
    ("CREATE UNIQUE INDEX IF NOT EXISTS 悬_业务键悬边唯一 ON 悬账单(业务键, 悬边)",
     "悬账单", "业务键, 悬边"),
]


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _建唯一索引(conn: sqlite3.Connection, 语句: str, 表: str, 列组: str) -> None:
    try:
        conn.execute(语句)
    except sqlite3.OperationalError:
        # 历史库里可能已有重复行；对齐差/悬账单都是可重算派生表，保留最新一行后重建。
        conn.execute(
            f"DELETE FROM {表} WHERE 编号 NOT IN "
            f"(SELECT MAX(编号) FROM {表} GROUP BY {列组})"
        )
        conn.execute(语句)


def 初始化库() -> None:
    conn = 连接()
    conn.executescript(建表)
    for 语句, 表, 列组 in _唯一索引:
        _建唯一索引(conn, 语句, 表, 列组)
    if conn.execute("SELECT COUNT(*) AS c FROM 轨规").fetchone()["c"] == 0:
        for k, v in {
            "站名": "对拍",
            "对齐键": "业务键",
            "金额容差分": "0",
            "骨架": "左轨装载→右轨装载→同键对齐落差→单边悬账→对拍结论",
            "皮": "支付渠道回执 vs 核心记账",
            "去皮": "双源键对齐管道",
            "补记形态": "消悬重对齐",
        }.items():
            conn.execute("INSERT INTO 轨规(键, 值) VALUES (?, ?)", (k, v))
    # 老库（已灌过种子）也补上迟到补记的默认形态
    conn.execute(
        "INSERT OR IGNORE INTO 轨规(键, 值) VALUES ('补记形态', '消悬重对齐')"
    )
    if conn.execute("SELECT COUNT(*) AS c FROM 源登记").fetchone()["c"] == 0:
        conn.execute(
            "INSERT INTO 源登记(源键,轨别,名称,说明) VALUES (?,?,?,?)",
            ("渠甲", "左", "渠道回执甲", "支付渠道回执流"),
        )
        conn.execute(
            "INSERT INTO 源登记(源键,轨别,名称,说明) VALUES (?,?,?,?)",
            ("核乙", "右", "核心记账乙", "核心系统记账流"),
        )
    conn.commit()
    conn.close()
