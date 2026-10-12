#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
lxt_open.py —— 完整开阀链路（BLE + 服务器令牌）

完整链路（源自 util/nbBle.js 的 M / C 函数）：

    ┌─ BLE ─────────────────────────────────────────────┐
    │ ① cmd F0  取设备信息 → SN                          │
    │ ② cmd 08  取随机数   → machineRandom               │
    │ ③ cmd 02  授权(type=01)                            │
    │      → machinedata / possystemtime / temperature   │
    │      → openClientType / machineDataFull            │
    └────────────────────────────────────────────────────┘
    ┌─ HTTP ────────────────────────────────────────────┐
    │ ④ POST bath/handleData  把设备的"挑战"送服务器      │
    │      → 服务器签发 systemData / systemTime /         │
    │        agreementNoHex                               │
    └────────────────────────────────────────────────────┘
    ┌─ BLE ─────────────────────────────────────────────┐
    │ ⑤ cmd 06  把服务器签发的令牌发回设备 → 开阀         │
    └────────────────────────────────────────────────────┘

设备只认服务器签发的令牌，所以第 ④ 步不可省略。

用法：
    python lxt_open.py --dry-run    只跑 ①②③④，不出水（安全）
    python lxt_open.py              完整开阀
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import lxt_api
import lxt_nb as nb
from config import AppConfig
from lxt_ble import LxtDevice, MY_ID

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_CFG = AppConfig.load()
TYPE_ID = _CFG.type_id          # 1 = 洗澡（见 config.py）


def load_token() -> str:
    """读登录票据（config 统一管理；没有则退回抓包时那份，仅供调试）"""
    from config import load_token as _load
    return _load() or lxt_api.TOKEN_CAPTURED


def log(m: str) -> None:
    print(f"  [{time.strftime('%H:%M:%S')}] {m}", flush=True)


async def run(dry_run: bool, device_ver: str, type_id: int,
              domain: str, verbose: bool = True, type_override: str = "") -> int:
    dev = LxtDevice(verbose=verbose)
    try:
        # ───────── ①②③ BLE 取挑战 ─────────
        await dev.connect()

        log("① cmd F0 取设备信息")
        r0 = await dev.cmd_f0()
        if not r0 or "info" not in r0:
            raise RuntimeError("取设备信息失败")
        sn = r0["info"].sn
        mac = r0["info"].mac
        log(f"   SN = {sn}   MAC = {mac}")

        api = lxt_api.LxtApi(token=load_token(), domain=domain, verbose=verbose)

        # deviceVer 不是猜的，要按 MAC 问服务器
        if device_ver in ("auto", "", None):
            mac_fmt = ":".join(mac[i:i + 2] for i in range(0, 12, 2))
            log(f"   查询设备版本 getMachineByMac({mac_fmt})")
            m = api.get("mgapp/machine/getMachineByMac", {"mac": mac_fmt, "typeId": type_id})
            if m.get("Code") != 0 or not (m.get("Data") or {}).get("deviceVer"):
                raise RuntimeError(f"查不到 deviceVer: {m}")
            d0 = m["Data"]
            device_ver = d0["deviceVer"]
            log(f"   deviceVer = {device_ver}   房间 = {d0.get('roominfo')}   机号 = {d0.get('machineName')}")

        log("② cmd 08 取随机数")
        r08 = await dev.cmd_08()
        if r08 is None:
            raise RuntimeError("取随机数失败")
        machine_random = r08["data"]
        log(f"   machineRandom = {machine_random}")

        log("③ cmd 02 授权 (type=01)")
        r02 = await dev.cmd_02_auth("01", MY_ID)
        if r02 is None:
            raise RuntimeError("授权失败")
        d = r02["data"]
        # 设备被占用时会返回简短状态码，而不是完整的挑战数据。
        # 状态码含义来自 util/nb.js 的 cmd 02 解析器：
        code02 = d[0:2] if len(d) >= 2 else ""
        busy = {
            "03": "设备正忙，无法使用",
            "06": "该设备正被他人使用中，请稍后再试",
            "02": "设备连接异常，请重试",
        }.get(code02)
        if busy:
            raise RuntimeError(busy)
        if len(d) < 50:
            raise RuntimeError(f"授权回复异常（{len(d)//2} 字节，状态码 {code02 or '空'}）")
        machinedata   = d[20:36]          # 8 字节
        possystemtime = d[36:48]          # 6 字节
        open_client   = int(d[2:4], 16)
        handletype    = d[48:50]
        temperature   = int(d[48:50], 16)
        machine_full  = r02["raw"]        # 完整原始帧
        log(f"   machinedata   = {machinedata}")
        log(f"   possystemtime = {possystemtime}")
        log(f"   openClientType= {open_client}   temperature = {temperature}")

        # ───────── ④ HTTP 换令牌 ─────────
        log("④ POST bath/handleData 换开阀令牌")
        resp = lxt_api.handle_data(
            api,
            type_id=type_id,
            machine_id=sn,
            device_ver=device_ver,
            open_client_type=open_client,
            machine_data=machinedata,
            machine_random=machine_random,
            pos_system_time=possystemtime,
            temperature=temperature,
            machine_data_full=machine_full,
            random_key=machine_random,
        )
        print()
        print("=" * 88)
        print("服务器返回:")
        print(json.dumps(resp, ensure_ascii=False, indent=2)[:1800])
        print("=" * 88)

        if resp.get("Code") != 0:
            log(f"❌ 服务器拒绝: Code={resp.get('Code')} {resp.get('Message')}")
            log("   （deviceVer 可能不对：格式为 'machinetypeid,machinever'，如 1,93 / 2,78）")
            return 3

        data = resp.get("Data") or {}
        sdata = data.get("systemData") or data.get("machineData") or ""
        stime = data.get("systemTime") or data.get("posSystemTime") or ""
        agree = data.get("agreementNoHex") or data.get("signatureFile") or ""
        stype = str(data.get("systemType") or data.get("type") or "") or type_override or "01"
        log(f"✅ 拿到令牌!")
        log(f"   systemData  = {sdata}")
        log(f"   systemTime  = {stime}")
        log(f"   agreement   = {agree}")
        log(f"   systemType  = {stype}")

        if dry_run:
            log("--dry-run：到此为止，不发开阀命令（未出水）")
            return 0

        # ───────── ⑤ BLE 开阀 ─────────
        # 源码：machinetypeid==01 且 machinever==93 才用 46，否则用 06
        mt = device_ver.split(",")[0].lstrip("0") or "0"
        mv = device_ver.split(",")[1] if "," in device_ver else ""
        cmd = "46" if (mt == "1" and mv == "93") else "06"
        log(f"⑤ cmd {cmd} 发送令牌 → 开阀")
        r06 = await dev.cmd_06_open(stype or "02", sdata, stime, agree, cmd=cmd)
        if r06 is None:
            log("❌ 开阀无响应")
            return 4
        st = r06["data"][0:2]
        log(f"   设备回复 cmd={r06['cmd']} status={st} data={r06['data'][:40]}")
        if st in ("00", "AA"):
            log("🎉 开阀成功！")
            return 0
        log(f"❌ 开阀失败 status={st}")
        return 5

    except Exception as exc:
        msg = str(exc)
        log(f"[X] {type(exc).__name__}: {msg}")
        if "使用中" in msg or "正忙" in msg:
            return 6          # 设备被别人占用
        return 2
    finally:
        await dev.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser(description="乐校通完整开阀链路")
    ap.add_argument("--dry-run", action="store_true", help="只跑到服务器换令牌，不出水")
    ap.add_argument("--device-ver", default="auto", help="machinetypeid,machinever；默认 auto 自动查询")
    ap.add_argument("--type-id", type=int, default=TYPE_ID)
    ap.add_argument("--domain", default=_CFG.domain)
    ap.add_argument("--type", dest="ctype", default="", help="cmd 06 的 type，默认 01")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args()
    return asyncio.run(run(a.dry_run, a.device_ver, a.type_id, a.domain, not a.quiet, a.ctype))


if __name__ == "__main__":
    sys.exit(main())
