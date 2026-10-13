#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
valve.py —— 开阀 / 关阀 / 状态（走服务器，**完全不需要蓝牙**）

★★★ 这是一个重要更正 ★★★
    之前用蓝牙（BLE）直连水控器实现开阀，需要扫描设备、保持连接、
    还要走 cmd F0/08/02/06 一整套握手。

    但 deviceVer 第二段是 "07" 的设备是 **4G 机**（小程序 util/version.js 的
    FourthGCanUse 表里含 "07"，getCache 里也有 ali4G + IMEI），
    开阀和关阀**都由服务器下发指令**，根本不用蓝牙。

    实测可用的接口（老家族，走 v3-app.lxt6.cn）：

      开阀  POST /bath/valve/open
            {typeId, channelWay, machineId, deviceVer,
             machineInfoJson:{lastPosMoney:"0000", lastPosSerial:"0000"}}
            -> Data.communicationVal   ← 关阀/查状态必须回传这个值

      关阀  POST /bath/valve/close
            {typeId, channelWay, machineId, deviceVer,
             machineInfoJson:{machineId, communicationVal}}

      状态  GET  /bath/valve/getStatus?typeId&machineId&modeValue&communicationVal

    好处：不用蓝牙、不用扫设备、不用保持连接，双击就出水。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests  # noqa: E402

import lxt_login as L  # noqa: E402

SESSION_ERR = -44
CAPTCHA_ERR = {-44, -45, -50, -52, -521}


# ──────────────────────── 配置 ────────────────────────
def load_config() -> dict:
    """
    配置优先从 state/config.json 读；也兼容项目早期的 config.json 结构
    （早期用 machine.sn，现在用 device.machineId）。
    """
    cands = [_SRC.parent / "state" / "config.json", _SRC.parent / "config.json"]
    for p in cands:
        if not p.exists():
            continue
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        dev = c.get("device") or {}
        mach = c.get("machine") or {}
        dev.setdefault("machineId", mach.get("sn") or mach.get("machineId") or "")
        dev.setdefault("deviceVer", mach.get("device_ver") or mach.get("deviceVer") or "")
        dev.setdefault("mac", mach.get("mac") or "")
        dev.setdefault("name", mach.get("name") or mach.get("roominfo") or "")
        dev.setdefault("typeId", (c.get("options") or {}).get("type_id", 1))
        dev.setdefault("channelWay", 4)
        c["device"] = dev
        c.setdefault("mobile", mach.get("mobile") or "")
        c.setdefault("base_url", "https://v3-app.lxt6.cn")
        return c
    return {}


def session_path() -> Path:
    return _SRC.parent / "state" / "session.json"


class ValveError(RuntimeError):
    pass


# ──────────────────────── 阀门 ────────────────────────
class Valve:
    def __init__(self, cfg: dict | None = None, verbose: bool = True,
                 tag: str = "阀门"):
        self.cfg = cfg or load_config()
        self.dev = self.cfg.get("device") or {}
        self.verbose = verbose
        self.tag = tag
        self.state = L.load_session()
        self.s = L.new_session()
        self.token = self.state.get("token", "")
        self.host = (self.state.get("host") or
                     self.cfg.get("base_url") or "https://v3-app.lxt6.cn").rstrip("/")

    # ---------- 日志 ----------
    def _log(self, *a):
        if self.verbose:
            print(f"[{self.tag}]", *a, flush=True)

    # ---------- 会话 ----------
    def _save_state(self, **kw):
        self.state.update(kw)
        self.state["token"] = self.token
        self.state["host"] = self.host
        self.state["updatedAt"] = time.time()
        p = session_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.state, ensure_ascii=False, indent=2),
                     encoding="utf-8")

    def login(self, max_attempts: int = 8) -> bool:
        mobile = self.cfg.get("mobile") or ""
        pwd = self.cfg.get("password") or ""
        if not mobile or not pwd:
            try:
                import credentials
                mobile, pwd = credentials.load()
            except Exception:
                pass
        if not (mobile and pwd):
            raise ValveError("配置里没有手机号/密码。请先跑一次「一键配置」。")

        self._log(f"正在登录（{mobile}）…")
        d = L.auto_login(mobile, pwd, max_attempts=max_attempts,
                         verbose=self.verbose, allow_human=True)
        self.token = d.get("token", "")
        self.host = (d.get("url") or self.host).rstrip("/")
        self._save_state(studentId=d.get("studentId", ""))
        return True

    def ensure_login(self):
        if not self.token:
            self.login()

    # ---------- 通用调用（带自动重登） ----------
    def call(self, path: str, data=None, method: str = "POST") -> dict:
        self.ensure_login()
        try:
            return self._call(path, data, method)
        except L.LoginError as e:
            if e.code != SESSION_ERR:
                raise
            self._log("会话已过期，自动重新登录 …")
            self.token = ""
            self.login()
            return self._call(path, data, method)

    def _call(self, path: str, data=None, method: str = "POST") -> dict:
        p = path[1:] if path.startswith("/") else path
        url = f"{self.host}/{p}"
        if method.upper() == "GET":
            params = L.query_params(data or {})
            signed = L.serialize(data or {})
            r = self.s.get(url, params=params,
                           headers=L.headers(signed, self.token),
                           timeout=L.TIMEOUT, verify=False)
        else:
            body = json.dumps(data or {}, separators=(",", ":"),
                              ensure_ascii=False)
            r = self.s.post(url, data=body.encode(),
                            headers=L.headers(body, self.token),
                            timeout=L.TIMEOUT, verify=False)
        try:
            j = r.json()
        except ValueError:
            raise L.LoginError(None, f"HTTP {r.status_code} 非 JSON: {r.text[:200]}")
        code = j.get("Code")
        if code != 0:
            raise L.LoginError(code, j.get("Message"))
        # 有些接口 Data 在顶层
        return j.get("Data") if "Data" in j else j

    # ---------- 业务 ----------
    def _dev_name(self) -> str:
        return self.dev.get("name") or self.dev.get("machineId") or "?"

    def _body_base(self) -> dict:
        return {
            "typeId": self.dev.get("typeId", 1),
            "channelWay": self.dev.get("channelWay", 4),
            "machineId": self.dev.get("machineId", ""),
            "deviceVer": self.dev.get("deviceVer", "01,07"),
        }

    def open(self) -> dict:
        if not self.dev.get("machineId"):
            raise ValveError("配置里没有设备号（machineId）。请先跑一次「一键配置」。")
        body = dict(self._body_base())
        body["machineInfoJson"] = {"lastPosMoney": "0000", "lastPosSerial": "0000"}
        self._log(f"正在开阀 —— {self._dev_name()}")
        data = self.call("/bath/valve/open", body, "POST") or {}
        cv = data.get("communicationVal") if isinstance(data, dict) else None
        self._save_state(communicationVal=cv or "", modeValue=1,
                         openedAt=time.time(), machineId=self.dev.get("machineId"),
                         lastOpen=data)
        self._log("✅ 开阀成功，水可以用了")
        return data if isinstance(data, dict) else {}

    def close(self) -> dict:
        cv = self.state.get("communicationVal", "")
        body = dict(self._body_base())
        body["machineInfoJson"] = {"machineId": self.dev.get("machineId", ""),
                                   "communicationVal": cv}
        self._log(f"正在关阀 —— {self._dev_name()}")
        if not cv:
            self._log("  提示：本机没有开阀记录（communicationVal 只在开阀返回里下发），仍照常尝试关闭")
        data = self.call("/bath/valve/close", body, "POST") or {}
        self._save_state(communicationVal=(data or {}).get("communicationVal") or cv,
                         modeValue=2, closedAt=time.time(), lastClose=data)
        self._log("✅ 关阀成功")
        opened = self.state.get("openedAt")
        if opened:
            self._log(f"  本次用时 {_duration(time.time() - opened)}")
        return data if isinstance(data, dict) else {}

    def status(self) -> dict:
        cv = self.state.get("communicationVal", "")
        if not cv:
            self._log("状态：暂时查不了（本机没有开阀记录）")
            self._log("  原因：服务端要求带 communicationVal，它只在「开阀」返回里下发。")
            self._log("  拿不到 ⇒ 说明阀门当前是关闭/空闲状态。这是接口设计，不是故障。")
            return {}
        d = dict(self._body_base())
        d.pop("channelWay", None)
        d["modeValue"] = self.state.get("modeValue", 0)
        d["communicationVal"] = cv
        data = self.call("/bath/valve/getStatus", d, "GET") or {}
        if isinstance(data, dict):
            self._log(f"状态：{data.get('statusMsg')} (status={data.get('status')})")
        return data if isinstance(data, dict) else {}

    # ---------- 设备信息（只读） ----------
    def device_info(self) -> dict:
        """按 MAC 查设备（MAC 必须带冒号，否则返回空）"""
        mac = (self.dev.get("mac") or self.dev.get("machineId") or "").replace(":", "").upper()
        mac_fmt = ":".join(mac[i:i + 2] for i in range(0, len(mac), 2))
        return self.call("/mgapp/machine/getMachineByMac",
                         {"mac": mac_fmt, "typeId": self.dev.get("typeId", 1)},
                         "GET") or {}


def _duration(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s} 秒"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} 分 {s} 秒" if s else f"{m} 分"
    h, m = divmod(m, 60)
    return f"{h} 小时 {m} 分" if m else f"{h} 小时"


# ──────────────────────── 命令行 ────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="热水阀 开/关/状态（服务器下发，无需蓝牙）")
    ap.add_argument("action", choices=["open", "close", "status", "info"])
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args()

    v = Valve(verbose=not a.quiet)
    try:
        if a.action == "open":
            v.open()
        elif a.action == "close":
            v.close()
        elif a.action == "status":
            v.status()
        else:
            v.ensure_login()
            print(json.dumps(v.device_info(), ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"✗ {type(e).__name__}: {e}")
        sys.exit(1)
