#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
discover.py —— 按宿舍号找到水控器

链路（全部来自实测，详见 docs/协议说明.md）：

    baseDict/site/getLowerAreas?areaId=<schoolId>
        -> 区域 / 楼栋 / 楼层 三级树（叶子是楼层）

    baseDict/site/getDormitoryOrPublicRoom?areaId=<楼层id>&typeId=1
        -> [{"id":"660000000000000001","name":"402",
             "demo":"某校区15栋4层402","siteFlag":0}]
        ★ 这里的 id 就是后面要用的 siteId

    bath/machine/status/getList?schoolId&investorId&siteId&siteFlag&typeId
        -> {"idleNum":1,"usedNum":0,
            "machineStatusList":[{"machineId":"...","machineName":"402",
                                  "addressMsg":"某校区15栋4层402",
                                  "curStatus":0,"curStatusMsg":"空闲", ...}]}

为什么要"选"：**房号会重名**。实测某校 1343 个房间里 "402" 有 4 个
（13/14/15/16 栋各一个），全库 139 个重名房号。而且还有 A402/B402/C402
这种真实存在的房号 —— 它们只是名字里含 "402"，并不是 402。

处理原则：**精确匹配和"含有"都列出来**，靠排序和分组区分，
绝不能把"含有"的直接过滤掉（否则住 A402 的人永远找不到自己的宿舍）。
"""

from __future__ import annotations

import re
from typing import Callable


def floors(client, school_id) -> list[tuple[str, str]]:
    """返回 [(完整位置路径, 楼层id)]，如 ("某校区/15栋/4层", "650000000000003280")"""
    tree = client.call("baseDict/site/getLowerAreas",
                       {"areaId": str(school_id)}, "GET") or []
    out: list[tuple[str, str]] = []

    def walk(nodes, path: tuple[str, ...]):
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            p = path + (n.get("name") or "?",)
            kids = n.get("childList") or []
            if kids:
                walk(kids, p)
            else:
                out.append(("/".join(p), n.get("id")))

    walk(tree, ())
    return out


def rooms(client, floor_id: str, type_id: int = 1) -> list[dict]:
    """某个楼层下的房间：[{name, addr, siteId, siteFlag}]"""
    data = client.call("baseDict/site/getDormitoryOrPublicRoom",
                       {"areaId": floor_id, "typeId": type_id}, "GET") or []
    out = []
    for r in data or []:
        if not isinstance(r, dict):
            continue
        out.append({"name": r.get("name"),
                    "addr": r.get("demo") or r.get("name"),
                    "siteId": r.get("id"),
                    "siteFlag": r.get("siteFlag")})
    return out


def all_rooms(client, school_id, type_id: int = 1,
              progress: Callable[[int, int], None] | None = None) -> list[dict]:
    """扫全库房间（实测某校 41 层 / 1343 间，约 5 秒）"""
    fl = floors(client, school_id)
    out = []
    for i, (path, fid) in enumerate(fl, 1):
        if progress:
            progress(i, len(fl))
        for r in rooms(client, fid, type_id):
            r["sitePath"] = path
            out.append(r)
    return out


def machines_at_site(client, school_id, investor_id, site_id, site_flag,
                     type_id: int = 1) -> dict:
    """某房间下的设备。返回 {"idleNum":.., "usedNum":.., "list":[...]}"""
    d = client.call("bath/machine/status/getList",
                    {"schoolId": school_id, "investorId": investor_id,
                     "siteId": site_id, "siteFlag": site_flag,
                     "typeId": type_id}, "GET") or {}
    return {"idleNum": d.get("idleNum"), "usedNum": d.get("usedNum"),
            "list": d.get("machineStatusList") or []}


def _score(room: dict, q: str) -> int:
    """匹配度打分，越大越像。支持 "402" / "A402" / "15栋402" / "某校区15栋4层402"

       100  房号完全相同
        95  地址完全相同
        90  地址结尾相同
        70  数字片段全中 且是子串
        55  数字片段全中
        40  地址里含有该串   ★ 可能不是同一个房间（如 A402）
        -1  不匹配
    """
    name = str(room.get("name") or "").strip()
    addr = str(room.get("addr") or "").strip()
    q = q.strip().replace(" ", "")
    if not q:
        return -1
    if name == q:
        return 100
    if addr == q:
        return 95
    if q.isdigit():
        # 纯数字：房号要么完全相等（上面已判），要么只是"含有" → 低分排后面
        return 40 if q in addr else -1
    if addr.endswith(q):
        return 90
    nums = re.findall(r"\d+", q)
    if nums and all(n in addr for n in nums):
        return 70 if q in addr else 55
    if q in addr:
        return 40
    return -1


def find_rooms(client, school_id, query: str, type_id: int = 1,
               progress: Callable[[int, int], None] | None = None,
               min_score: int = 0) -> list[dict]:
    """按用户输入找候选房间，按匹配度从高到低排序。
    返回项带 `_score`；`_exact` 表示"确实是这个房号"（>=90 分）。"""
    cands = []
    for r in all_rooms(client, school_id, type_id, progress):
        s = _score(r, query)
        if s >= min_score:
            r = dict(r)
            r["_score"] = s
            r["_exact"] = s >= 90
            cands.append(r)
    cands.sort(key=lambda x: (-x["_score"], str(x.get("addr"))))
    return cands
