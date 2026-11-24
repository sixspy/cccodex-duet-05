import os
import threading
from pathlib import Path

_tmp = Path(__file__).resolve().parent / "_tmp"
_tmp.mkdir(exist_ok=True)
os.environ["数据目录"] = str(_tmp)
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

from app import seed
from app.db import 连接
from app.services import 左装载, 右装载, 对齐, 结论, 目录
from app.modules import 左轨迟到补记 as 补记

def setup_function():
    db = _tmp / "对拍.db"
    if db.exists():
        db.unlink()
    seed.初始化库()

def test_双轨对齐与悬账():
    左装载.装一批({
        "事件": [
            {"事件编号": "a1", "业务键": "K1", "金额分": 100, "源键": "渠甲", "事件时": "2026-11-23T10:00:00Z"},
            {"事件编号": "a2", "业务键": "K2", "金额分": 200, "源键": "渠甲", "事件时": "2026-11-23T10:00:01Z"},
        ]
    })
    右装载.装一批({
        "事件": [
            {"事件编号": "b1", "业务键": "K1", "金额分": 100, "源键": "核乙", "事件时": "2026-11-23T10:00:00Z"},
            {"事件编号": "b2", "业务键": "K3", "金额分": 50, "源键": "核乙", "事件时": "2026-11-23T10:00:02Z"},
        ]
    })
    r = 对齐.跑对齐()
    assert r["对齐"] == 1
    assert r["悬账"] >= 2
    assert len(目录.悬列表()) >= 2
    g = 结论.跑一次()
    assert g["是否通过"] is True

def test_seed():
    assert 目录.概览()["站名"] == "对拍"
    assert len(目录.源列表()) >= 2


# ---------------------------------------------------------------- 左轨迟到补记

def _右先出悬(键="K-LATE", 额=500):
    右装载.装一批({"事件": [
        {"事件编号": f"R-{键}", "业务键": 键, "金额分": 额, "源键": "核乙",
         "事件时": "2026-11-23T10:00:00Z"},
    ]})
    对齐.跑对齐()
    assert [h["悬边"] for h in 目录.悬列表() if h["业务键"] == 键] == ["右"]


def _当前(): return 连接()


def test_消悬形态_现行无悬且无归档():
    _右先出悬()
    v = 补记.补记({"业务键": "K-LATE", "事件编号": "L-LATE", "金额分": 500})
    assert v["形态"] == 补记.消悬形态
    assert v["旧悬边"] == "右"
    assert v["现行悬"] is False
    assert v["归档键"] == ""
    assert v["valid"] is True
    assert v["对齐差型"] == "对齐"
    c = _当前()
    try:
        assert c.execute("SELECT COUNT(*) c FROM 悬账单 WHERE 业务键='K-LATE'").fetchone()["c"] == 0
        assert c.execute("SELECT COUNT(*) c FROM 悬账归档").fetchone()["c"] == 0
    finally:
        c.close()
    assert 结论.跑一次()["是否通过"] is True


def test_双写形态_现行无悬且归档留旧悬():
    _右先出悬("K-DUAL", 700)
    c = _当前(); c.execute("UPDATE 轨规 SET 值='双写两轨保留悬账快照' WHERE 键='补记形态'"); c.commit(); c.close()

    v = 补记.补记({"业务键": "K-DUAL", "事件编号": "L-DUAL", "金额分": 700})
    assert v["形态"] == 补记.双写形态
    assert v["现行悬"] is False
    assert v["归档键"].startswith("K-DUAL@")
    assert v["valid"] is True
    assert v["旧悬快照"]["悬边"] == "右"
    assert v["旧悬快照"]["金额分"] == 700
    c = _当前()
    try:
        assert c.execute("SELECT COUNT(*) c FROM 悬账单 WHERE 业务键='K-DUAL'").fetchone()["c"] == 0
        assert c.execute("SELECT COUNT(*) c FROM 悬账归档 WHERE 业务键='K-DUAL'").fetchone()["c"] == 1
    finally:
        c.close()
    assert 结论.跑一次()["是否通过"] is True


def test_归档只读():
    _右先出悬("K-RO", 10)
    c = _当前(); c.execute("UPDATE 轨规 SET 值='双写两轨保留悬账快照' WHERE 键='补记形态'"); c.commit(); c.close()
    补记.补记({"业务键": "K-RO", "事件编号": "L-RO", "金额分": 10})
    c = _当前()
    try:
        for sql in ("UPDATE 悬账归档 SET 金额分=1", "DELETE FROM 悬账归档"):
            try:
                c.execute(sql); c.commit(); raise AssertionError("归档被改了")
            except Exception:
                c.rollback()
    finally:
        c.close()


def test_金额不符时补记留差而非对齐():
    _右先出悬("K-DIFF", 500)
    v = 补记.补记({"业务键": "K-DIFF", "事件编号": "L-DIFF", "金额分": 480})
    assert v["对齐差型"] == "金额不符"
    assert v["现行悬"] is False
    assert v["valid"] is True


def test_无右悬不许补记():
    _右先出悬("K-NO", 1)
    c = _当前(); c.execute("DELETE FROM 悬账单"); c.commit(); c.close()
    try:
        补记.补记({"业务键": "K-NO", "事件编号": "L-NO", "金额分": 1})
        raise AssertionError("应当驳回")
    except 补记.补记驳回 as e:
        assert e.状态码 == 409


def test_重复补记幂等():
    _右先出悬("K-IDEM", 42)
    v1 = 补记.补记({"业务键": "K-IDEM", "事件编号": "L-IDEM", "金额分": 42})
    v2 = 补记.补记({"业务键": "K-IDEM", "事件编号": "L-IDEM-2", "金额分": 42})
    assert v1["valid"] and v2["valid"]
    assert v2["幂等"] is True
    c = _当前()
    try:
        assert c.execute("SELECT COUNT(*) c FROM 左轨流水 WHERE 业务键='K-IDEM'").fetchone()["c"] == 1
        assert c.execute("SELECT COUNT(*) c FROM 补记记录 WHERE 业务键='K-IDEM'").fetchone()["c"] == 1
    finally:
        c.close()


def test_补记与右装并发不双悬():
    _右先出悬("K-RACE", 100)
    结果 = []
    def 补():
        try: 结果.append(("补", 补记.补记({"业务键": "K-RACE", "事件编号": "L-RACE", "金额分": 100})))
        except Exception as e: 结果.append(("补错", repr(e)))
    def 右():
        try: 结果.append(("右", 右装载.装一批({"事件": [
            {"事件编号": "R-RACE-2", "业务键": "K-RACE", "金额分": 100, "源键": "核乙",
             "事件时": "2026-11-23T10:01:00Z"}]})))
        except Exception as e: 结果.append(("右错", repr(e)))
    def 对():
        try: 结果.append(("对齐", 对齐.跑对齐()))
        except Exception as e: 结果.append(("对齐错", repr(e)))
    th = [threading.Thread(target=f) for f in (补, 右, 对, 补)]
    [t.start() for t in th]; [t.join() for t in th]
    c = _当前()
    try:
        assert c.execute("SELECT COUNT(*) c FROM 悬账单 WHERE 业务键='K-RACE'").fetchone()["c"] == 0
        assert c.execute("SELECT COUNT(*) c FROM 对齐差 WHERE 业务键='K-RACE'").fetchone()["c"] == 1
    finally:
        c.close()


def test_免装左直接读视图接口含必填五要素():
    右装载.装一批({"事件": [
        {"事件编号": "R-VIEW", "业务键": "K-VIEW", "金额分": 9, "源键": "核乙",
         "事件时": "2026-11-23T10:00:00Z"}]})
    对齐.跑对齐()
    补记.补记({"业务键": "K-VIEW", "事件编号": "L-VIEW", "金额分": 9})
    v = 补记.详情("K-VIEW")
    for k in ("业务键", "旧悬边", "现行悬", "归档键", "valid"):
        assert k in v
    assert v["业务键"] == "K-VIEW" and v["旧悬边"] == "右" and v["现行悬"] is False
    assert isinstance(v["valid"], bool)


# ---------------------------------------------------------------- HTTP 路由层
# 回归：Starlette 路径参数名只认 ASCII，{业务键} 会被当字面量，夹具路由与详情路由
# 都曾因此 404/405。用 TestClient 走真实路由，业务键含中文与斜杠。

def _客户端():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_夹具路由可用():
    with _客户端() as c:
        assert c.post("/api/左轨/夹具/左轨样例").json()["入流"] == 3
        assert c.post("/api/右轨/夹具/右轨样例").json()["入流"] == 3
        assert c.post("/api/对齐/跑").json()["悬账"] == 2


def test_详情路由_中文与斜杠业务键():
    with _客户端() as c:
        c.post("/api/右轨/装载", json={"事件": [
            {"事件编号": "R-HTTP", "业务键": "键/中文", "金额分": 7, "源键": "核乙",
             "事件时": "2026-11-23T10:00:00Z"}]})
        c.post("/api/对齐/跑")
        r = c.post("/api/迟到补记", json={"业务键": "键/中文", "金额分": 7})
        assert r.status_code == 200 and r.json()["valid"] is True
        d = c.get("/api/迟到补记/键/中文")
        assert d.status_code == 200
        for k in ("业务键", "旧悬边", "现行悬", "归档键", "valid"):
            assert k in d.json()
        # 固定子路径不被 {key:path} 吞掉
        assert c.get("/api/迟到补记/策略").status_code == 200
        assert c.get("/api/迟到补记/归档").status_code == 200
        assert c.get("/api/迟到补记").status_code == 200


def test_补记驳回映射到状态码():
    with _客户端() as c:
        r = c.post("/api/迟到补记", json={"业务键": "无此悬键", "金额分": 1})
        assert r.status_code == 409
        d = c.get("/api/迟到补记/无此悬键")
        assert d.status_code == 404
        bad = c.post("/api/迟到补记", json={"业务键": "X", "形态": "瞎写", "金额分": 1})
        assert bad.status_code == 400


def test_空桩枚举不再含已实现模块():
    with _客户端() as c:
        d = c.get("/api/空桩").json()
        assert "左轨迟到补记" not in d["空桩"]
        assert "左轨迟到补记" in d["已实现"]
        assert d["条数"] == len(d["空桩"])


def test_双写形态重对齐后归档与valid保持():
    seed.初始化库()
    c = _当前(); c.execute("UPDATE 轨规 SET 值='双写两轨保留悬账快照' WHERE 键='补记形态'"); c.commit(); c.close()
    右装载.装一批({"事件": [
        {"事件编号": "R-RE", "业务键": "K-RE", "金额分": 5, "源键": "核乙",
         "事件时": "2026-11-23T10:00:00Z"}]})
    对齐.跑对齐()
    补记.补记({"业务键": "K-RE", "金额分": 5})
    assert 补记.详情("K-RE")["valid"] is True
    对齐.跑对齐()
    v = 补记.详情("K-RE")
    assert v["valid"] is True and v["旧悬快照"] is not None and v["现行悬"] is False
