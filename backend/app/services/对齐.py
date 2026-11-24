"""同业务键对齐：双边金额比对，落对齐差 + 悬账单。

对齐结果（对齐差 / 悬账单）是派生表：每业务键恒有且仅有一行对齐差，
悬账按「业务键+悬边」唯一。全局跑对齐与迟到补记里的单键重算走同一套
单键逻辑，避免两条写路径口径漂移。
"""
from __future__ import annotations

from app.db import 写事务
from app.seed import 此刻


def 读容差(conn) -> int:
    r = conn.execute("SELECT 值 FROM 轨规 WHERE 键='金额容差分'").fetchone()
    try:
        return int(r["值"]) if r else 0
    except (TypeError, ValueError):
        return 0


def _单键态(conn, 业务键: str):
    左 = conn.execute(
        "SELECT COALESCE(SUM(金额分),0) AS 额, COUNT(*) AS 笔 FROM 左轨流水 "
        "WHERE 业务键=? AND 命运='已入左'",
        (业务键,),
    ).fetchone()
    右 = conn.execute(
        "SELECT COALESCE(SUM(金额分),0) AS 额, COUNT(*) AS 笔 FROM 右轨流水 "
        "WHERE 业务键=? AND 命运='已入右'",
        (业务键,),
    ).fetchone()
    左代 = conn.execute(
        "SELECT 事件编号 FROM 左轨流水 WHERE 业务键=? AND 命运='已入左' "
        "ORDER BY 事件编号 LIMIT 1",
        (业务键,),
    ).fetchone()
    右代 = conn.execute(
        "SELECT 事件编号 FROM 右轨流水 WHERE 业务键=? AND 命运='已入右' "
        "ORDER BY 事件编号 LIMIT 1",
        (业务键,),
    ).fetchone()
    return {
        "有左": 左["笔"] > 0,
        "左额": int(左["额"]),
        "左代": 左代["事件编号"] if 左代 else "",
        "有右": 右["笔"] > 0,
        "右额": int(右["额"]),
        "右代": 右代["事件编号"] if 右代 else "",
    }


def 重算一键(conn, 业务键: str, 容差: int, 时刻: str) -> dict:
    """只重算一个业务键的对齐差与悬账，不触碰其他键。

    调用方必须已在写事务里（迟到补记要求「装载+消悬+重对齐同票」）。
    """
    conn.execute("DELETE FROM 对齐差 WHERE 业务键=?", (业务键,))
    conn.execute("DELETE FROM 悬账单 WHERE 业务键=?", (业务键,))

    s = _单键态(conn, 业务键)
    if s["有左"] and s["有右"]:
        if abs(s["左额"] - s["右额"]) <= 容差:
            差型, 说明 = "对齐", "双边金额在容差内"
        else:
            差型, 说明 = "金额不符", f"左{s['左额']}-右{s['右额']}"
        conn.execute(
            "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻) "
            "VALUES (?,?,?,?,?,?)",
            (业务键, s["左额"], s["右额"], 差型, 说明, 时刻),
        )
        return {"业务键": 业务键, "差型": 差型, "悬边": None}

    if s["有左"]:
        conn.execute(
            "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻) "
            "VALUES (?,?,?,?,?,?)",
            (业务键, s["左额"], None, "右缺", "仅左轨有键", 时刻),
        )
        conn.execute(
            "INSERT INTO 悬账单(业务键,悬边,金额分,事件编号,原因,时刻) "
            "VALUES (?,?,?,?,?,?)",
            (业务键, "左", s["左额"], s["左代"], "右轨缺键", 时刻),
        )
        return {"业务键": 业务键, "差型": "右缺", "悬边": "左"}

    if s["有右"]:
        conn.execute(
            "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻) "
            "VALUES (?,?,?,?,?,?)",
            (业务键, None, s["右额"], "左缺", "仅右轨有键", 时刻),
        )
        conn.execute(
            "INSERT INTO 悬账单(业务键,悬边,金额分,事件编号,原因,时刻) "
            "VALUES (?,?,?,?,?,?)",
            (业务键, "右", s["右额"], s["右代"], "左轨缺键", 时刻),
        )
        return {"业务键": 业务键, "差型": "左缺", "悬边": "右"}

    # 双轨皆无该键：两个派生表都不留行
    return {"业务键": 业务键, "差型": "", "悬边": None}


def _所有键(conn) -> set[str]:
    键 = set()
    for 表, 命运 in (("左轨流水", "已入左"), ("右轨流水", "已入右")):
        for r in conn.execute(
            f"SELECT DISTINCT 业务键 FROM {表} WHERE 命运=?", (命运,)
        ):
            键.add(r["业务键"])
    return 键


def 跑对齐() -> dict:
    with 写事务() as conn:
        容差 = 读容差(conn)
        # 基线默认：每次重算派生表
        conn.execute("DELETE FROM 对齐差")
        conn.execute("DELETE FROM 悬账单")

        键集 = _所有键(conn)
        对齐 = 差 = 悬 = 0
        时刻 = 此刻()
        for k in sorted(键集):
            态 = 重算一键(conn, k, 容差, 时刻)
            if 态["差型"] == "对齐":
                对齐 += 1
            elif 态["差型"]:
                差 += 1
            if 态["悬边"]:
                悬 += 1

        return {
            "键总数": len(键集),
            "对齐": 对齐,
            "差与悬": 差,
            "悬账": 悬,
            "容差分": 容差,
            "时刻": 时刻,
        }
