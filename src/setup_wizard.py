#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
setup_wizard.py —— 一键配置向导

用户下载后只需要跑这个（或双击 安装配置.bat），按提示走完，
就能得到桌面上的「开热水 / 关热水」两个图标。

流程：
    0. 欢迎 + 环境自检（Python / 依赖 / 蓝牙）
    1. 获取登录票据（不碰密码、不碰验证码）
    2. 搜索附近的水控器 → 问服务器"这是哪个房间" → 列出来让用户选
    3. 确认信息
    4. 写入 config.json + 生成桌面快捷方式

设计要点：
    * 每一步都能安全退出（Ctrl+C 不会留下烂摊子）
    * 任何一步失败都给出**人话**的解释和下一步怎么做
    * 绝不打印完整票据（日志脱敏）
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import asdict
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import lxt_api
import token_source
from config import AppConfig, load_token, save_token, token_hash

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SERVICE_HINT = "0000ffe0"     # 水控器主服务
NAME_HINTS = ("BT19S", "BT1", "BT-", "LK", "水控")

LINE = "─" * 74


def hr(title: str = "") -> None:
    print()
    if title:
        print(f"  ┌{'─' * 70}┐")
        print(f"  │ {title:<68} │")
        print(f"  └{'─' * 70}┘")
    else:
        print("  " + LINE)


def ask(prompt: str, default: str = "") -> str:
    try:
        s = input(f"  {prompt}").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消。")
        sys.exit(130)
    return s or default


def yes(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    s = ask(f"{prompt} [{d}] ").lower()
    if not s:
        return default
    return s.startswith("y")


# ──────────────────────── 0. 环境自检 ────────────────────────
def check_env() -> bool:
    hr("第 0 步 / 共 4 步　环境自检")
    ok = True

    v = sys.version_info
    print(f"  Python 版本 : {v.major}.{v.minor}.{v.micro}", end="  ")
    if v < (3, 9):
        print("✗ 太旧，需要 3.9 以上")
        ok = False
    else:
        print("✓")

    try:
        import bleak  # noqa
        print(f"  bleak 库    : ✓ ({getattr(bleak, '__version__', '已安装')})")
    except ImportError:
        print("  bleak 库    : ✗ 未安装")
        print("      请运行： pip install bleak")
        ok = False

    try:
        import requests  # noqa
        print("  requests 库 : ✓")
    except ImportError:
        print("  requests 库 : ✗ 未安装")
        print("      请运行： pip install requests")
        ok = False

    print("  蓝牙适配器  : 检查中…", end=" ", flush=True)
    try:
        from bleak import BleakScanner

        async def _probe():
            return await BleakScanner.discover(timeout=3.0)

        devs = asyncio.run(_probe())
        print(f"✓（本次扫到 {len(devs)} 个设备）")
    except Exception as exc:
        print(f"✗ {type(exc).__name__}: {exc}")
        print("      请确认：① 电脑蓝牙已打开  ② 没被其他程序独占")
        ok = False

    if not ok:
        print("\n  ⚠ 环境不满足，请先解决上面的问题再跑一次。")
    return ok


# ──────────────────────── 1. 票据 ────────────────────────
def step_token(cfg: AppConfig) -> bool:
    hr("第 1 步 / 共 4 步　获取登录票据")
    print("""
  本工具需要一次"登录"，之后靠保活长期有效。

  两种做法，任选其一：
    · 直接登录   —— 输入手机号+密码+验证码（只需一次，密码不保存）
    · 复用会话   —— 先在微信里登录一次乐校通，再回到这里自动读取

  ⚠ 不接收、不保存你的密码；不破解验证码。
""")
    tk = load_token()
    if tk:
        ok, msg, info = token_source.verify(tk)
        if ok:
            print(f"  ✅ 本地票据仍然有效（{info.get('studentName','?')}）")
            cfg.machine.student_hex = info.get("studentHex") or cfg.machine.student_hex
            if yes("  要重新登录吗？", False):
                pass
            else:
                return True
        else:
            print(f"  本地票据已失效：{msg}")

    while True:
        print("  请选择获取方式：")
        print("    [1] 直接登录（推荐，只需一次）")
        print("    [2] 复用微信里已登录的会话（自动读取本地票据）")
        print("    [3] 手工粘贴票据（会用抓包工具的人）")
        print("    [0] 退出")
        c = ask("输入序号 [1]: ", "1")

        if c == "0":
            return False

        if c == "1":
            try:
                import lxt_login
                print()
                ok, lmsg = lxt_login.interactive_login()
            except Exception as exc:
                ok, lmsg = False, f"登录模块异常：{type(exc).__name__}: {exc}"
            if ok:
                t = load_token()
                okv, m2, info = token_source.verify(t)
                if okv:
                    cfg.machine.student_hex = info.get("studentHex") or ""
                    print(f"  ✅ 票据有效：{info.get('studentName','?')} / {info.get('studentHex','?')}")
                    return True
                print(f"  ✗ 登录后校验失败：{m2}")
            else:
                print(f"  ✗ {lmsg}")
        else:
            strategy = {"2": "auto", "3": "manual"}.get(c, "auto")
            t, msg = token_source.acquire(strategy)
            if t:
                okv, m2, info = token_source.verify(t)
                if okv:
                    cfg.machine.student_hex = info.get("studentHex") or ""
                    print(f"  ✅ 票据有效：{info.get('studentName','?')} / {info.get('studentHex','?')}")
                    return True
                print(f"  ✗ 校验失败：{m2}")
            else:
                print(f"  ✗ {msg}")

        print()
        if not yes("  再试一次吗？", True):
            return False


# ──────────────────────── 2. 选设备 ────────────────────────
async def _scan() -> list:
    from bleak import BleakScanner
    print("  正在扫描附近的蓝牙设备（12 秒）…", flush=True)
    found = await BleakScanner.discover(timeout=12.0, return_adv=True)
    rows = []
    for dev, adv in found.values():
        name = (adv.local_name or dev.name or "").strip()
        uuids = " ".join(adv.service_uuids or []).lower()
        score = 0
        if SERVICE_HINT in uuids:
            score += 10
        if any(h in name.upper() for h in NAME_HINTS):
            score += 5
        rows.append({"address": dev.address, "name": name,
                     "rssi": adv.rssi, "score": score, "uuids": uuids})
    rows.sort(key=lambda r: (-r["score"], -r["rssi"]))
    return rows


def step_device(cfg: AppConfig, token: str) -> bool:
    hr("第 2 步 / 共 4 步　选择你的水控器")

    try:
        rows = asyncio.run(_scan())
    except Exception as exc:
        print(f"  ✗ 扫描失败：{type(exc).__name__}: {exc}")
        return False

    cands = [r for r in rows if r["score"] > 0]
    if not cands:
        print(f"  ✗ 附近没有发现水控器（扫到 {len(rows)} 个蓝牙设备，但都不像）")
        print("      请确认你在水控器旁边（几米内），然后重试。")
        if not yes("  重扫一次？", True):
            return False
        return step_device(cfg, token)

    api = lxt_api.LxtApi(token=token, domain=cfg.domain, verbose=False)

    # 逐个问服务器：这是哪个房间
    print(f"  发现 {len(cands)} 个候选，正在查询房间信息…\n")
    listed = []
    for r in cands:
        mac = ":".join(r["address"].replace("-", ":").upper().split(":")[:6])
        room, mname, dver, sn = "（查询失败）", "", "", ""
        try:
            resp = api.get("mgapp/machine/getMachineByMac", {"mac": mac, "typeId": cfg.type_id})
            if resp.get("Code") == 0 and resp.get("Data"):
                d = resp["Data"]
                room = d.get("roominfo") or "（无房间信息）"
                mname = d.get("machineName") or ""
                dver = d.get("deviceVer") or ""
                sn = d.get("machineId") or ""
            else:
                room = f"（{resp.get('Message') or '未授权设备'}）"
        except Exception as exc:
            room = f"（异常 {type(exc).__name__}）"
        listed.append({**r, "mac": mac, "room": room, "mname": mname,
                       "dver": dver, "sn": sn})

    # 已查到房间的排前面
    listed.sort(key=lambda x: (0 if x["sn"] else 1, -x["rssi"]))

    while True:
        print("  请选择你的水控器：\n")
        for i, x in enumerate(listed, 1):
            print(f"    [{i}] {x['room']:<26} 信号 {x['rssi']:>4} dBm   "
                  f"{x['name'] or '未命名':<14} {x['address']}")
        print()
        c = ask("输入序号（0 = 重新扫描）: ", "")
        if c == "0":
            return step_device(cfg, token)
        try:
            k = int(c)
            pick = listed[k - 1]
        except (ValueError, IndexError):
            print("  ✗ 序号不对，重输\n")
            continue

        if not pick["sn"]:
            print(f"\n  ⚠ 这台设备服务器查不到（{pick['room']}）。")
            print("    可能是别的楼栋/别的学校的设备，或者不属于你。")
            if not yes("  仍要用它吗？", False):
                continue
            # 手工补 SN
            pick["sn"] = ask("  请手工输入设备 SN（设备标签上有）: ")

        cfg.machine.address = pick["address"]
        cfg.machine.name = pick["name"]
        cfg.machine.mac = pick["mac"].replace(":", "")
        cfg.machine.sn = pick["sn"]
        cfg.machine.device_ver = pick["dver"]
        cfg.machine.roominfo = pick["room"]
        cfg.machine.machine_name = pick["mname"]
        return True


# ──────────────────────── 3. 确认 ────────────────────────
def step_confirm(cfg: AppConfig) -> bool:
    hr("第 3 步 / 共 4 步　确认信息")
    m = cfg.machine
    print(f"""
    房间      : {m.roominfo or '（未知）'}
    机号      : {m.machine_name or '（未知）'}
    蓝牙地址  : {m.address}
    设备 SN   : {m.sn or '（未知）'}
    版本      : {m.device_ver or '（未知）'}
    你的 ID   : {m.student_hex or '（未知）'}
""")
    return yes("  以上信息正确吗？", True)


# ──────────────────────── 4. 试跑 + 落地 ────────────────────────
def step_finish(cfg: AppConfig) -> bool:
    hr("第 4 步 / 共 4 步　试跑并生成快捷方式")
    p = cfg.save()
    print(f"  ✅ 配置已保存：{p}")

    print("\n  试跑：连接水控器读一下设备信息（不会出水）…")
    try:
        import lxt_ble
        dev = lxt_ble.LxtDevice(address=cfg.machine.address, verbose=False)

        async def _run():
            await dev.connect()
            r = await dev.cmd_f0()
            await dev.disconnect()
            return r

        r = asyncio.run(_run())
        if r and "info" in r:
            print(f"  ✅ 连接成功！SN={r['info'].sn}  MAC={r['info'].mac}")
        else:
            print("  ⚠ 连上了但没读到设备信息，仍可继续（稍后可用 status 命令复查）")
    except Exception as exc:
        print(f"  ⚠ 试跑失败：{type(exc).__name__}: {exc}")
        print("    不影响配置；请确认蓝牙已开、人在设备旁。")

    # 桌面快捷方式
    try:
        import lxt_ctl
        print()
        lxt_ctl.make_shortcuts()
    except Exception as exc:
        print(f"  ⚠ 生成快捷方式失败：{exc}")

    hr()
    print("""
  🎉 配置完成！

  现在可以：
    * 双击桌面「开热水」 → 约 4 秒出水
    * 双击桌面「关热水」 → 关水

  ⚠ 如果提示「服务器拒绝 / 身份失效」：
     说明登录票据过期了，在微信里重新登录一次乐校通，
     然后重新运行本配置向导的第 1 步即可。
""")
    return True


# ──────────────────────── 主流程 ────────────────────────
def main() -> int:
    print()
    print("  ╔" + "═" * 70 + "╗")
    print("  ║" + "宿舍热水 一键配置向导".center(62) + " " * 8 + "║")
    print("  ║" + "蓝牙直连水控器 · 不经过微信 · 无广告".center(58) + " " * 12 + "║")
    print("  ╚" + "═" * 70 + "╝")
    print("""
  本工具只操作你自己宿舍的设备，使用你自己的账号，用水照常计费。
  不接收密码、不破解验证码。按 Ctrl+C 可以随时退出。
""")
    if not yes("  开始配置吗？", True):
        return 0

    cfg = AppConfig.load()

    if not check_env():
        return 1
    if not step_token(cfg):
        print("\n  ✗ 没拿到有效票据，配置中止。")
        return 1
    if not step_device(cfg, load_token()):
        print("\n  ✗ 没选到设备，配置中止。")
        return 1
    if not step_confirm(cfg):
        print("\n  已取消。")
        return 1
    step_finish(cfg)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n  已取消（Ctrl+C）")
        sys.exit(130)
