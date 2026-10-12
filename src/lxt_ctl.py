#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
一键开/关宿舍热水 —— 电脑蓝牙直连水控器（不经过微信、无广告）

═══════════════════════════════════════════════════════════════════
后端链路（全部由逆向乐校通小程序得到，并已实测跑通）
═══════════════════════════════════════════════════════════════════
开阀（约 4 秒）：
    ① BLE  cmd F0   取设备信息 → SN / MAC
    ② HTTP getMachineByMac  按 MAC 查 deviceVer（如 01,07）
    ③ BLE  cmd 08   取随机数
    ④ BLE  cmd 02   授权(type=01) → machinedata / possystemtime
    ⑤ HTTP bath/handleData  把挑战送服务器 → 换回签名令牌
                            systemData(16B) + systemTime(6B)
    ⑥ BLE  cmd 06   把令牌发回设备(type=01) → 开阀

关阀：
    BLE  cmd 14     data = "00" + ID

═══════════════════════════════════════════════════════════════════
用法
═══════════════════════════════════════════════════════════════════
    python lxt_ctl.py open              开热水（弹确认框）
    python lxt_ctl.py close             关热水
    python lxt_ctl.py status            读设备信息（安全）
    python lxt_ctl.py open --yes        跳过确认（供桌面图标调用）
    python lxt_ctl.py --make-shortcut   生成桌面「开热水 / 关热水」

═══════════════════════════════════════════════════════════════════
注意
═══════════════════════════════════════════════════════════════════
* 只操作自己宿舍这台设备（BT19S-T / AA:BB:CC:DD:EE:FF / 某区某栋402）
* 开阀前默认弹确认框，防止误触；关阀不需要确认
* 登录票据过期时，只要在微信里打开一次乐校通即可刷新
  （票据缓存在 state/token_full.txt，会自动更新）
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import subprocess
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import lxt_api
import lxt_nb as nb
import lxt_open
from config import AppConfig
from lxt_ble import LxtDevice, MY_ID

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = _SRC.parent
LOG = ROOT / "logs" / "lxt_ctl.log"
CFG = AppConfig.load()

MB_OK, MB_ICONERROR, MB_ICONINFORMATION = 0x0, 0x10, 0x40
MB_ICONQUESTION, MB_YESNO, MB_TOPMOST, MB_SETFOREGROUND = 0x20, 0x4, 0x40000, 0x10000
IDYES = 6


# ──────────────────────────── 工具 ────────────────────────────
def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:
        pass
    try:
        if sys.stdout is not None:
            print(msg, flush=True)
    except Exception:
        pass


def msgbox(text: str, title: str = "宿舍热水", kind: str = "info") -> int:
    icon = {"info": MB_ICONINFORMATION, "error": MB_ICONERROR,
            "ask": MB_ICONQUESTION}.get(kind, MB_ICONINFORMATION)
    try:
        return ctypes.windll.user32.MessageBoxW(0, text, title, MB_OK | icon | MB_TOPMOST | MB_SETFOREGROUND)
    except Exception:
        return 0


def confirm(text: str, title: str = "宿舍热水") -> bool:
    try:
        return ctypes.windll.user32.MessageBoxW(
            0, text, title, MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND) == IDYES
    except Exception:
        return True


# ──────────────────────────── 业务 ────────────────────────────
async def do_status(verbose: bool) -> int:
    dev = LxtDevice(verbose=verbose)
    try:
        await dev.connect()
        r = await dev.cmd_f0()
        if r and "info" in r:
            i = r["info"]
            msg = f"设备信息\n\nSN：{i.sn}\nMAC：{i.mac}\nIMEI：{i.imei}"
            log(msg.replace("\n", " | "))
            if not verbose:
                msgbox(msg)
            return 0
        raise RuntimeError("取设备信息失败")
    except Exception as exc:
        log(f"[X] {type(exc).__name__}: {exc}")
        if not verbose:
            msgbox(f"连接失败：{exc}\n\n请确认：\n1. 蓝牙已打开\n2. 在水控器附近\n3. 手机没占用它", kind="error")
        return 2
    finally:
        await dev.disconnect()


async def do_open(auto_yes: bool, verbose: bool) -> int:
    if not auto_yes and CFG.confirm_before_open:
        if not confirm("即将开启热水。\n\n确认吗？", "开启热水"):
            log("用户取消开阀")
            return 0

    t0 = time.time()
    rc = await lxt_open.run(dry_run=False, device_ver="auto", type_id=CFG.type_id,
                            domain=CFG.domain, verbose=verbose)
    cost = time.time() - t0

    if rc == 0:
        room = CFG.machine.roominfo or "你的宿舍"
        log(f"✅ 热水已开启（{room}，耗时 {cost:.1f} 秒）")
        if not verbose:
            msgbox(f"✅ 热水已开启\n\n{room}\n耗时 {cost:.1f} 秒")
        return 0

    tip = {
        2: "蓝牙没连上。请确认蓝牙已开、人在水控器旁边、手机没占用它。",
        3: "服务器拒绝。通常是登录票据过期——在微信里打开一次乐校通即可刷新。",
        4: "设备没有回应开阀命令。",
        5: "设备拒绝开阀（授权失败）。",
        6: "设备正被他人使用中，请稍后再试。",
    }.get(rc, "未知错误。")
    log(f"❌ 开阀失败 rc={rc}：{tip}")
    if not verbose:
        msgbox(f"❌ 开阀失败\n\n{tip}\n\n详见 logs\\lxt_ctl.log", kind="error")
    return rc or 2


async def do_close(verbose: bool) -> int:
    dev = LxtDevice(verbose=verbose)
    try:
        await dev.connect()
        r = await dev.cmd_14_close(MY_ID)
        if r is None:
            raise RuntimeError("关阀命令无响应")
        st = r["data"][0:2]
        # 源码：cmd14 返回 "00"=关阀成功, "01"=设备已关闭, 其他=设备正忙
        if st == "00":
            msg = "热水已关闭"
        elif st == "01":
            msg = "设备本来就是关闭状态"
        else:
            msg = f"关阀返回异常（status={st}）"
        log(msg)
        if not verbose:
            msgbox(f"✅ {msg}")
        return 0 if st in ("00", "01") else 2
    except Exception as exc:
        log(f"[X] 关阀失败：{type(exc).__name__}: {exc}")
        if not verbose:
            msgbox(f"❌ 关阀失败\n\n{exc}", kind="error")
        return 2
    finally:
        await dev.disconnect()


# ──────────────────────────── 桌面快捷方式 ────────────────────────────
def _make_lnk(lnk: Path, script_args: str, icon: str) -> bool:
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pyw = Path(sys.executable)
    vbs = ROOT / "state" / "_mk.vbs"
    vbs.parent.mkdir(parents=True, exist_ok=True)
    body = (
        'Set ws = CreateObject("WScript.Shell")\r\n'
        f'Set l = ws.CreateShortcut("{lnk}")\r\n'
        f'l.TargetPath = "{pyw}"\r\n'
        f'l.Arguments = """{_SRC / "lxt_ctl.py"}"" {script_args}"\r\n'
        f'l.WorkingDirectory = "{ROOT}"\r\n'
        f'l.IconLocation = "{icon}"\r\n'
        f'l.Description = "宿舍热水"\r\n'
        'l.Save\r\n'
    )
    vbs.write_bytes(b"\xff\xfe" + body.encode("utf-16-le"))
    try:
        subprocess.run(["cscript", "//nologo", str(vbs)], capture_output=True, timeout=60, check=False)
        return lnk.exists()
    except Exception as exc:
        log(f"生成快捷方式异常：{exc}")
        return False
    finally:
        try:
            vbs.unlink()
        except Exception:
            pass


def make_shortcuts() -> int:
    import os
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        desktop = Path(os.environ.get("USERPROFILE", str(ROOT))) / "Desktop"
    made: list[str] = []
    if _make_lnk(desktop / "开热水.lnk", "open --yes", r"%SystemRoot%\System32\imageres.dll,103"):
        made.append("开热水.lnk")
    if _make_lnk(desktop / "关热水.lnk", "close", r"%SystemRoot%\System32\imageres.dll,100"):
        made.append("关热水.lnk")
    pyw = Path(sys.executable).with_name("pythonw.exe")
    for name, a in (("开热水", "open --yes"), ("关热水", "close")):
        (ROOT / f"{name}.bat").write_bytes(
            f'@echo off\r\nchcp 65001 >nul\r\n"{pyw}" "{_SRC / "lxt_ctl.py"}" {a}\r\n'.encode("utf-8"))
        made.append(f"{name}.bat")
    log("已生成：" + ", ".join(made))
    print("已生成：")
    for m in made:
        print("  " + m)
    print(f"\n桌面路径：{desktop}")
    print("双击「开热水」即出水，约 4 秒。")
    return 0


# ──────────────────────────── 入口 ────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="一键开/关宿舍热水（蓝牙直连水控器）")
    ap.add_argument("action", nargs="?", choices=["open", "close", "status"], default="open")
    ap.add_argument("--yes", action="store_true", help="跳过确认弹窗")
    ap.add_argument("--make-shortcut", action="store_true", help="生成桌面图标")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印协议细节")
    args = ap.parse_args()

    if args.make_shortcut:
        return make_shortcuts()

    if args.action == "open":
        return asyncio.run(do_open(args.yes, args.verbose))
    if args.action == "close":
        return asyncio.run(do_close(args.verbose))
    return asyncio.run(do_status(args.verbose))


if __name__ == "__main__":
    sys.exit(main())
