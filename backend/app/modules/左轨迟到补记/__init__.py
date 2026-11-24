"""左轨迟到补记：右悬在途时左轨同键迟到到达的补齐主链路。

场景：右轨先入、跑对齐出「右悬」（仅右轨有键），随后左轨同业务键的事件
才迟到到达。补记必须择定一种形态把这对键重新对齐，且：

* 装载左事件、清掉该键旧悬、重跑该键对齐必须**同票**（同一写事务）；
* 不得只改悬账单的悬边角标糊弄过去，必须按真实双边重算；
* 补记与右轨再装 / 另一次对齐并发时不得出现双悬或重复开差。

两种形态：

* ``消悬重对齐``：删该键右悬 → 重算该键对齐差。现行悬账单不含该键，无归档。
* ``双写两轨保留悬账快照``：旧悬快照进只读归档（悬账归档），现行悬账改读
  新对齐结果。现行悬账单同样不含该键，但归档里留有旧悬，读引用（归档键）
  与现行悬不同，二者可区分。
"""
from __future__ import annotations

import uuid

from app.db import 连接, 写事务
from app.seed import 此刻
from app.services import 校验, 对齐

NAME = "左轨迟到补记"

消悬形态 = "消悬重对齐"
双写形态 = "双写两轨保留悬账快照"
形态集 = (消悬形态, 双写形态)
默认形态 = 消悬形态

形态说明 = {
    消悬形态: "删该键右悬，重跑对齐；现行悬账单无该键，不保留快照。",
    双写形态: "旧悬进只读归档，现行悬账改读新对齐结果；现行无悬、归档有旧悬。",
}


class 补记驳回(Exception):
    """补记前置条件不满足（可预期，映射为 4xx）。"""

    def __init__(self, 说明: str, 状态码: int = 409):
        super().__init__(说明)
        self.说明 = 说明
        self.状态码 = 状态码


# ---------------------------------------------------------------- 策略

def 读形态(conn) -> str:
    r = conn.execute("SELECT 值 FROM 轨规 WHERE 键='补记形态'").fetchone()
    v = r["值"] if r else 默认形态
    return v if v in 形态集 else 默认形态


def 策略() -> dict:
    conn = 连接()
    try:
        return {
            "现行形态": 读形态(conn),
            "可选形态": [
                {"名": 名, "说明": 形态说明[名]} for 名 in 形态集
            ],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------- 入参归一

def _取事件(体: dict) -> dict:
    ev = 体.get("事件")
    if isinstance(ev, dict):
        return dict(ev)
    out: dict = {}
    for k in ("事件编号", "业务键", "金额分", "源键", "事件时", "处理时"):
        if k in 体:
            out[k] = 体[k]
    return out


# ---------------------------------------------------------------- 补记主流程

def 补记(体: dict) -> dict:
    体 = 体 or {}
    if not isinstance(体, dict):
        raise 补记驳回("体须为对象", 400)
    键 = str(体.get("业务键") or "").strip()
    if not 键:
        raise 补记驳回("缺字段:业务键", 400)
    形态 = 体.get("形态")
    if 形态 in ("", None):
        形态 = None
    if 形态 is not None and 形态 not in 形态集:
        raise 补记驳回(f"未知补记形态:{形态}", 400)

    ev = _取事件(体)
    ev["业务键"] = 键
    ev.setdefault("源键", "渠甲")
    ev.setdefault("处理时", 此刻())
    ev.setdefault("事件时", 此刻())
    ev.setdefault("事件编号", f"补-{uuid.uuid4().hex[:12]}")

    行错 = 校验.校验事件(ev)
    if 行错:
        raise 补记驳回(";".join(行错), 400)

    with 写事务() as conn:
        实形态 = 形态 or 读形态(conn)
        view = _补记同票(conn, 键, ev, 实形态)
    return view


def 补一批(体: dict) -> dict:
    """按夹具批量补记：每条事件各自一票（同键仍串行），逐条报告得失。

    与装载批语义一致：单条驳回不连累整批，但每条自身的「写事件+消悬+重对齐」
    仍是原子票。
    """
    体 = 体 or {}
    形态 = 体.get("形态")
    if 形态 in ("", None):
        形态 = None
    if 形态 is not None and 形态 not in 形态集:
        raise 补记驳回(f"未知补记形态:{形态}", 400)
    事件 = 体.get("事件")
    if not isinstance(事件, list) or not 事件:
        raise 补记驳回("事件列表为空", 400)

    报告 = {"形态": 形态, "补记": 0, "跳过": 0, "驳回": 0, "明细": []}
    for ev in 事件:
        if not isinstance(ev, dict):
            报告["驳回"] += 1
            报告["明细"].append({"命运": "驳回", "说明": "事件不是对象"})
            continue
        请求 = dict(ev)
        请求.setdefault("源键", "渠甲")
        if 形态:
            请求["形态"] = 形态
        try:
            v = 补记(请求)
        except 补记驳回 as e:
            报告["驳回"] += 1
            报告["明细"].append({
                "业务键": ev.get("业务键"), "事件编号": ev.get("事件编号"),
                "命运": "驳回", "说明": e.说明, "状态码": e.状态码,
            })
            continue
        if v.get("幂等"):
            报告["跳过"] += 1
            报告["明细"].append({
                "业务键": v["业务键"], "事件编号": v["事件编号"],
                "命运": "幂等跳过", "valid": v["valid"],
            })
        else:
            报告["补记"] += 1
            报告["明细"].append({
                "业务键": v["业务键"], "事件编号": v["事件编号"],
                "命运": "已补记", "形态": v["形态"],
                "现行悬": v["现行悬"], "归档键": v["归档键"], "valid": v["valid"],
            })
    return 报告


def _补记同票(conn, 键: str, ev: dict, 形态: str) -> dict:
    """在调用方已取的写事务内完成：写左事件 + 消悬/归档 + 单键重对齐 + 记账。"""

    # 幂等：同一业务键只补记一次；重复请求直接回读，不产生第二次写。
    已记 = conn.execute(
        "SELECT 编号 FROM 补记记录 WHERE 业务键=?", (键,)
    ).fetchone()
    if 已记:
        return _读视图(conn, 键) | {"幂等": True, "说明": "该业务键已补记，未重复写入"}

    编号 = str(ev["事件编号"])
    if conn.execute(
        "SELECT 1 FROM 左轨流水 WHERE 事件编号=?", (编号,)
    ).fetchone():
        raise 补记驳回(f"事件编号已存在:{编号}", 409)

    源 = ev.get("源键") or "渠甲"
    if not conn.execute(
        "SELECT 1 FROM 源登记 WHERE 源键=? AND 轨别='左'", (源,)
    ).fetchone():
        raise 补记驳回(f"未知左源:{源}", 400)

    # 前提：该键当前应是「右悬」（右有左无）。没有右悬就不是迟到补记。
    旧悬 = conn.execute(
        "SELECT * FROM 悬账单 WHERE 业务键=? AND 悬边='右' "
        "ORDER BY 编号 DESC LIMIT 1",
        (键,),
    ).fetchone()
    if not 旧悬:
        其他 = conn.execute(
            "SELECT 悬边 FROM 悬账单 WHERE 业务键=?", (键,)
        ).fetchone()
        if 其他:
            raise 补记驳回(f"该键悬边为{其他['悬边']}，迟到补记只处理右悬", 409)
        raise 补记驳回("该键无右悬，不满足迟到补记前提", 409)

    时刻 = 此刻()
    批次 = f"补批-{uuid.uuid4().hex[:8]}"
    conn.execute(
        """INSERT INTO 左轨流水(事件编号,业务键,金额分,源键,事件时,处理时,命运,批次编号)
           VALUES (?,?,?,?,?,?,'已入左',?)""",
        (
            编号, 键, int(ev.get("金额分", 0)), 源,
            ev.get("事件时") or 时刻, ev.get("处理时") or 时刻, 批次,
        ),
    )

    归档键 = f"{键}@{旧悬['编号']}" if 形态 == 双写形态 else ""
    conn.execute(
        """INSERT INTO 补记记录(业务键,事件编号,金额分,形态,旧悬边,旧悬编号,归档键,补记时刻)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            键, 编号, int(ev.get("金额分", 0)), 形态,
            "右", int(旧悬["编号"]), 归档键, 时刻,
        ),
    )
    补记编号 = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]

    if 形态 == 双写形态:
        # 旧悬快照进只读归档；归档表由触发器禁改禁删。
        conn.execute(
            """INSERT INTO 悬账归档
               (归档键,业务键,悬边,金额分,事件编号,原因,原悬编号,归档时刻,补记编号)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                归档键, 键, 旧悬["悬边"], int(旧悬["金额分"]),
                旧悬["事件编号"], 旧悬["原因"], int(旧悬["编号"]), 时刻, 补记编号,
            ),
        )

    # 重跑该键对齐：删该键旧差/旧悬，按真实双边写回。禁止只改悬边角标。
    对齐.重算一键(conn, 键, 对齐.读容差(conn), 时刻)

    return _读视图(conn, 键) | {
        "幂等": False,
        "说明": f"{形态}完成",
        "批次编号": 批次,
    }


# ---------------------------------------------------------------- 只读视图

def _快照(conn, 记) -> dict | None:
    if not 记["归档键"]:
        return None
    r = conn.execute(
        "SELECT * FROM 悬账归档 WHERE 归档键=?", (记["归档键"],)
    ).fetchone()
    return dict(r) if r else None


def _读视图(conn, 键: str) -> dict:
    记 = conn.execute(
        "SELECT * FROM 补记记录 WHERE 业务键=?", (键,)
    ).fetchone()
    if not 记:
        raise 补记驳回(f"无该业务键的补记记录:{键}", 404)

    现行悬 = conn.execute(
        "SELECT * FROM 悬账单 WHERE 业务键=? ORDER BY 编号 DESC LIMIT 1", (键,)
    ).fetchone()
    差 = conn.execute(
        "SELECT * FROM 对齐差 WHERE 业务键=? ORDER BY 编号 DESC LIMIT 1", (键,)
    ).fetchone()
    左 = conn.execute(
        "SELECT * FROM 左轨流水 WHERE 事件编号=?", (记["事件编号"],)
    ).fetchone()
    右 = conn.execute(
        "SELECT * FROM 右轨流水 WHERE 业务键=? AND 命运='已入右' LIMIT 1", (键,)
    ).fetchone()
    快照 = _快照(conn, 记)

    问题: list[str] = []
    if not 左 or 左["命运"] != "已入左":
        问题.append("左轨无补记事件")
    if not 右:
        问题.append("右轨无该键，补记前提不成立")
    if not 差:
        问题.append("该键无对齐差")
    elif 差["差型"] not in ("对齐", "金额不符"):
        问题.append(f"补记后差型仍为{差['差型']}")
    if 现行悬 is not None:
        问题.append(f"现行悬账单仍含该键（悬边{现行悬['悬边']}）")

    if 记["形态"] == 双写形态:
        if not 记["归档键"]:
            问题.append("双写形态缺归档键")
        elif not 快照:
            问题.append("归档键指向的快照不存在")
        elif 快照["业务键"] != 键 or 快照["悬边"] != 记["旧悬边"]:
            问题.append("归档快照与补记记录不符")
    elif 记["形态"] == 消悬形态:
        if 记["归档键"]:
            问题.append("消悬形态不应有归档键")
        if conn.execute(
            "SELECT 1 FROM 悬账归档 WHERE 业务键=?", (键,)
        ).fetchone():
            问题.append("消悬形态不应归档旧悬")

    valid = not 问题
    return {
        "业务键": 键,
        "形态": 记["形态"],
        "旧悬边": 记["旧悬边"],
        "旧悬编号": 记["旧悬编号"],
        "归档键": 记["归档键"],
        "现行悬": 现行悬 is not None,
        "现行悬边": 现行悬["悬边"] if 现行悬 else None,
        "现行悬文案": f"仍含{现行悬['悬边']}悬" if 现行悬 else "现行无悬",
        "valid": valid,
        "校验问题": 问题,
        "事件编号": 记["事件编号"],
        "金额分": 记["金额分"],
        "补记时刻": 记["补记时刻"],
        "对齐差型": 差["差型"] if 差 else None,
        "对齐差左": 差["左金额分"] if 差 else None,
        "对齐差右": 差["右金额分"] if 差 else None,
        "旧悬快照": 快照,
    }


def 详情(键: str) -> dict:
    键 = str(键 or "").strip()
    if not 键:
        raise 补记驳回("缺字段:业务键", 400)
    conn = 连接()
    try:
        return _读视图(conn, 键)
    finally:
        conn.close()


def 列表() -> dict:
    conn = 连接()
    try:
        记集 = conn.execute(
            "SELECT 业务键 FROM 补记记录 ORDER BY 编号 DESC"
        ).fetchall()
        项 = [_读视图(conn, r["业务键"]) for r in 记集]
        汇总 = {
            "补记数": len(项),
            "消悬数": sum(1 for x in 项 if x["形态"] == 消悬形态),
            "双写数": sum(1 for x in 项 if x["形态"] == 双写形态),
            "归档数": sum(1 for x in 项 if x["归档键"]),
            "现行仍含数": sum(1 for x in 项 if x["现行悬"]),
            "有效数": sum(1 for x in 项 if x["valid"]),
            "无效数": sum(1 for x in 项 if not x["valid"]),
        }
        return {"列表": 项, "汇总": 汇总}
    finally:
        conn.close()


def 归档列表() -> dict:
    conn = 连接()
    try:
        rows = conn.execute(
            "SELECT * FROM 悬账归档 ORDER BY 归档时刻 DESC, 归档键"
        ).fetchall()
        return {"归档": [dict(r) for r in rows], "条数": len(rows)}
    finally:
        conn.close()
