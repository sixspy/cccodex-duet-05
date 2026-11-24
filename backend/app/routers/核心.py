from fastapi import APIRouter, HTTPException
from app.services import 目录, 左装载, 右装载, 对齐, 结论, 夹具, 统计, 说明
from app.modules import 左轨迟到补记 as 迟到补记

路由 = APIRouter()

@路由.get("/api/概览")
def api_概览():
    return 目录.概览()

@路由.get("/api/轨规")
def api_轨规():
    return 目录.轨规()

@路由.post("/api/轨规")
def api_改轨规(体: dict):
    return 目录.改轨规(体)

@路由.get("/api/源登记")
def api_源():
    return 目录.源列表()

@路由.get("/api/左轨")
def api_左():
    return 目录.左列表()

@路由.get("/api/右轨")
def api_右():
    return 目录.右列表()

@路由.get("/api/对齐差")
def api_差():
    return 目录.差列表()

@路由.get("/api/悬账单")
def api_悬():
    return 目录.悬列表()

@路由.get("/api/对拍结论")
def api_结论列表():
    return 目录.结论列表()

@路由.get("/api/夹具")
def api_夹具():
    return 夹具.列表()

@路由.post("/api/左轨/装载")
def api_左装(体: dict):
    return 左装载.装一批(体)

@路由.post("/api/右轨/装载")
def api_右装(体: dict):
    return 右装载.装一批(体)

@路由.post("/api/左轨/夹具/{name}")
def api_左夹(name: str):
    名 = name
    try:
        return 左装载.装一批(夹具.读取(名))
    except FileNotFoundError:
        raise HTTPException(404, 名)

@路由.post("/api/右轨/夹具/{name}")
def api_右夹(name: str):
    名 = name
    try:
        return 右装载.装一批(夹具.读取(名))
    except FileNotFoundError:
        raise HTTPException(404, 名)

@路由.post("/api/对齐/跑")
def api_对齐():
    return 对齐.跑对齐()

@路由.post("/api/对拍结论/跑")
def api_结论():
    return 结论.跑一次()


@路由.get("/api/统计/左命运")
def api_左命运():
    return 统计.左命运()

@路由.get("/api/统计/右命运")
def api_右命运():
    return 统计.右命运()

@路由.get("/api/统计/差型")
def api_差型():
    return 统计.差型分布()

@路由.get("/api/统计/悬边")
def api_悬边():
    return 统计.悬边分布()


@路由.get("/api/仓情")
def api_仓情():
    return 说明.仓情()


# ---------------------------------------------------------------- 左轨迟到补记
# 只读视图恒含：业务键 / 旧悬边 / 现行悬 / 归档键 / valid

@路由.get("/api/迟到补记/策略")
def api_补记策略():
    return 迟到补记.策略()

@路由.get("/api/迟到补记")
def api_补记列表():
    return 迟到补记.列表()

@路由.get("/api/迟到补记/归档")
def api_补记归档():
    return 迟到补记.归档列表()

# Starlette 路径参数名只认 ASCII，故用 {key}；业务键可含中文/斜杠，故用 :path 并放在
# 固定子路径（策略 / 归档 / 夹具）之后注册，避免被吞。
@路由.get("/api/迟到补记/{key:path}")
def api_补记详情(key: str):
    try:
        return 迟到补记.详情(key)
    except 迟到补记.补记驳回 as e:
        raise HTTPException(e.状态码, e.说明)

@路由.post("/api/迟到补记")
def api_补记(体: dict):
    try:
        return 迟到补记.补记(体)
    except 迟到补记.补记驳回 as e:
        raise HTTPException(e.状态码, e.说明)

@路由.post("/api/迟到补记/夹具/{name}")
def api_补记夹具(name: str, 体: dict | None = None):
    名 = name
    try:
        夹 = 夹具.读取(名)
    except FileNotFoundError:
        raise HTTPException(404, 名)
    请求 = dict(夹)
    if 体:
        请求.update(体)
    try:
        return 迟到补记.补一批(请求)
    except 迟到补记.补记驳回 as e:
        raise HTTPException(e.状态码, e.说明)


@路由.get("/api/空桩")
def api_空桩():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "modules"
    out = []
    done = []
    if root.is_dir():
        for p in sorted(root.iterdir()):
            if not (p.is_dir() and p.name != "__pycache__" and (p / "__init__.py").is_file()):
                continue
            # 已落主链路的模块不再算空桩：README/题面按「空桩」口径枚举时必须一致。
            src = (p / "__init__.py").read_text(encoding="utf-8")
            (out if "空桩" in src else done).append(p.name)
    return {"空桩": out, "条数": len(out), "已实现": done}

