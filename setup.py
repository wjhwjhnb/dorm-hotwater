#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
setup.py —— 一键配置向导

被 一键配置.bat 调用（那个 .bat 只负责找/装 Python）。

流程：
    0. 环境自检 + 自动装依赖
    1. 手机号 + 密码  → 自动登录（图片验证码由本地模型识别，全程无短信）
    2. 查学校 / 服务商
    3. 输宿舍号 → 扫全库房间 → 列出候选让你选（房号会重名，必须让你挑）
    4. 选那间房里的水控器
    5. 保存配置 + 加密保存账号密码
    6. 桌面建「开热水」「关热水」两个图标
    7. 注册保活开机自启（票据过期时自动重登）

全程随时可以 Ctrl+C 退出。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
ROOT = HERE
STATE = ROOT / "state"

sys.path.insert(0, str(SRC))

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

W = 74


# ──────────────────────── 界面小工具 ────────────────────────
def box(title: str) -> None:
    print()
    print("  ┌" + "─" * W + "┐")
    print("  │ " + title.ljust(W - 1) + "│")
    print("  └" + "─" * W + "┘")


def ask(prompt: str, default: str = "") -> str:
    s = f"  {prompt}" + (f" [{default}]" if default else "") + ": "
    v = input(s).strip()
    return v or default


def yes(prompt: str, default_yes: bool = True) -> bool:
    d = "Y/n" if default_yes else "y/N"
    v = input(f"  {prompt} [{d}]: ").strip().lower()
    if not v:
        return default_yes
    return v.startswith("y")


# ──────────────────────── 0. 环境 ────────────────────────
def ensure_deps() -> bool:
    box("第 0 步 / 共 5 步　环境自检")

    print(f"  Python 版本 : {sys.version.split()[0]}  ", end="")
    if sys.version_info < (3, 9):
        print("✗ 需要 3.9 以上")
        return False
    print("✓")

    need = []
    for mod, pkg in (("requests", "requests"), ("ppllocr", "ppllocr")):
        try:
            __import__(mod)
            print(f"  {pkg:<11} : ✓")
        except Exception:
            print(f"  {pkg:<11} : ✗ 需要安装")
            need.append(pkg)

    if need:
        print(f"\n  正在安装：{' '.join(need)}（第一次会慢一点，请等）\n")
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
        # 先试官方源，失败再试清华镜像
        for extra in ([], ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]):
            r = subprocess.run(cmd + need + extra)
            if r.returncode == 0:
                break
        for mod, pkg in (("requests", "requests"), ("ppllocr", "ppllocr")):
            try:
                __import__(mod)
                print(f"  {pkg:<11} : ✓ 已安装")
            except Exception as e:
                print(f"  ✗ {pkg} 安装后仍无法导入：{e}")
                return False

    try:
        import captcha as C
        print(f"  验证码识别 : ✓ {C.engine()[1]}")
    except Exception as e:
        print(f"  验证码识别 : ✗ {type(e).__name__}: {e}")
        print("     （不影响登录，验证码会转人工输入）")
    return True


# ──────────────────────── 1. 登录 ────────────────────────
def step_login():
    box("第 1 步 / 共 5 步　登录（手机号 + 密码，全程无短信）")

    import credentials
    saved_phone, saved_pw = credentials.load()
    if saved_phone and saved_pw:
        print(f"  发现本机已保存的账号：{saved_phone[:3]}****{saved_phone[-4:]}")
        if yes("  直接用它登录吗？"):
            phone, password = saved_phone, saved_pw
        else:
            phone, password = "", ""
    else:
        phone, password = "", ""

    while not phone:
        phone = ask("手机号")
        if len(phone) != 11 or not phone.isdigit():
            print("  ✗ 手机号应该是 11 位数字")
            phone = ""
    if not password:
        import getpass
        while True:
            password = getpass.getpass("  密码（不回显）: ")
            if len(password) >= 6:
                break
            print("  ✗ 密码太短（6-18 位），可能没读到输入")

    import captcha as C
    import lxt_login as L

    print(f"\n  识别引擎：{C.engine()[1]}")
    print("  正在自动登录（验证码自动识别，错了会自动换一张）…\n")
    try:
        d = L.auto_login(phone, password, max_attempts=8, verbose=True)
    except L.LoginError as e:
        print(f"\n  ✗ 登录失败：{e}")
        if "密码" in (e.message or ""):
            print("  ⚠ 服务器限制尝试次数 —— 请确认密码后再试，别反复错。")
        return None

    L.save_session(d)
    try:
        credentials.save(phone, password)
        print(f"\n  🔐 {credentials.describe()}")
    except Exception as e:
        print(f"  ⚠ 凭据保存失败（不影响使用）：{e}")

    d["_phone"] = phone
    return d


# ──────────────────────── 2. 学校 / 服务商 ────────────────────────
def step_org(v) -> bool:
    box("第 2 步 / 共 5 步　读取学校信息")
    try:
        sl = v.call("user/binding/getSchoolInfo", {}, "GET") or {}
        schools = sl.get("schoolList") or []
        if not schools:
            print("  ✗ 拿不到学校列表")
            return False
        sid = schools[0]
        v.state["schoolId"] = sid
        print(f"  学校 ID : {sid}")

        inv = v.call("operation/getInvestorInfoByShoolidV2",
                     {"schoolId": sid}, "GET") or []
        iid = None
        if isinstance(inv, list) and inv:
            iid = (inv[0].get("investorInfo") or {}).get("investorId")
        if not iid:
            print("  ✗ 拿不到服务商 ID")
            return False
        v.state["investorId"] = iid
        print(f"  服务商 ID: {iid}")
        v._save_state()
        return True
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e}")
        return False


# ──────────────────────── 3. 选宿舍 ────────────────────────
def step_room(v) -> dict | None:
    box("第 3 步 / 共 5 步　找你的宿舍")

    import discover as D
    school_id = v.state.get("schoolId")

    while True:
        q = ask("宿舍号（如 402 / A402 / 15栋402；回车重输）")
        if not q:
            continue

        print(f"\n  正在搜索「{q}」…（全库扫一遍大约几秒）")
        t0 = time.time()
        try:
            cands = D.find_rooms(
                v, school_id, q,
                progress=lambda i, n: print(f"\r    扫描楼层 {i}/{n} …", end="", flush=True))
        except Exception as e:
            print(f"\n  ✗ 搜索失败：{type(e).__name__}: {e}")
            continue
        print(f"\r    扫描完成（{time.time()-t0:.1f} 秒）" + " " * 20)

        if not cands:
            print(f"  ✗ 没找到含「{q}」的宿舍。试试输完整一点，比如 15栋402")
            continue

        exact = [c for c in cands if c["_exact"]]
        loose = [c for c in cands if not c["_exact"]]
        shown = (exact[:12] + loose[:8])[:20]

        print(f"\n  找到 {len(cands)} 个相关房间：")
        n = 0
        if exact:
            for c in exact[:12]:
                n += 1
                print(f"    {n:>2}. {c['addr']}")
        if loose:
            if exact:
                print("    ── 以下只是名字里含「%s」，是别的房间 ──" % q)
            for c in loose[:8]:
                n += 1
                print(f"    {n:>2}. {c['addr']}   （房号是 {c['name']}）")
        print(f"     0. 重新输入")

        sel = ask("选哪个", "1")
        if sel == "0":
            continue
        try:
            idx = int(sel)
        except ValueError:
            print("  ✗ 请输入数字")
            continue
        if not (1 <= idx <= len(shown)):
            print("  ✗ 序号超出范围")
            continue
        return shown[idx - 1]


# ──────────────────────── 4. 选设备 ────────────────────────
def step_machine(v, room: dict) -> dict | None:
    box("第 4 步 / 共 5 步　选水控器")

    import discover as D
    try:
        m = D.machines_at_site(v, v.state.get("schoolId"), v.state.get("investorId"),
                               room["siteId"], room.get("siteFlag"), 1)
    except Exception as e:
        print(f"  ✗ 查询设备失败：{type(e).__name__}: {e}")
        return None

    lst = m.get("list") or []
    print(f"  {room['addr']}　空闲 {m.get('idleNum')} 台 / 使用中 {m.get('usedNum')} 台\n")
    if not lst:
        print("  ✗ 这个房间里没查到水控器")
        return None

    for i, d in enumerate(lst, 1):
        st = d.get("curStatusMsg") or ""
        print(f"    {i:>2}. 设备 {d.get('machineId')}　{d.get('machineName') or ''}　[{st}]")

    while True:
        sel = ask("选哪台", "1")
        try:
            idx = int(sel)
        except ValueError:
            print("  ✗ 请输入数字")
            continue
        if 1 <= idx <= len(lst):
            return lst[idx - 1]
        print("  ✗ 序号超出范围")


# ──────────────────────── 5. 保存 + 建快捷方式 ────────────────────────
def find_device_ver(v, machine: dict) -> str:
    """查 deviceVer（决定是 4G 机还是蓝牙机）"""
    mac = (machine.get("mac") or "").replace(":", "").upper()
    if not mac and machine.get("machineId"):
        mac = str(machine["machineId"]).replace(":", "").upper()
    mac_fmt = ":".join(mac[i:i + 2] for i in range(0, len(mac), 2))
    try:
        info = v.call("/mgapp/machine/getMachineByMac",
                      {"mac": mac_fmt, "typeId": 1}, "GET") or {}
        return info.get("deviceVer") or ""
    except Exception:
        return ""


def make_launchers() -> None:
    """
    生成启动器：run.bat（共用）+ 开热水.bat / 关热水.bat（薄包装）

    ★ 铁律：.bat 的【内容】必须是纯 ASCII，中文只能出现在【文件名】里。
      cmd 用系统 OEM 码页（中文系统是 GBK）解析 .bat，内容里任何非 ASCII
      字节都会被拆成乱码指令，整个脚本崩掉 —— 我前后踩了两次：
        第一次：在 if 块的 echo 里写了裸括号 "(" → 块被截断
        第二次：在 run.bat 里写了 call "%~dp0一键配置.bat" → 中文被拆坏
      所以：带逻辑的批处理叫 SETUP.bat（ASCII 名），
            中文名的 一键配置.bat / 开热水.bat 只是两行薄包装。

    另外：.bat 里也【不要】写裸括号在 if 块内的 echo 行上（要用 ^( ^) 转义）。
    """
    ascii_only = lambda s: s.encode("ascii", "replace")

    # ---- 共用启动器：找 Python，找不到就转去跑配置 ----
    run_bat = (
        "@echo off\r\n"
        "rem Shared launcher. ASCII-only content.\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n"
        "chcp 65001 >nul\r\n"
        "set \"PY=\"\r\n"
        "for %%C in (\r\n"
        "  \"%~dp0.venv\\Scripts\\python.exe\"\r\n"
        "  \"%~dp0venv\\Scripts\\python.exe\"\r\n"
        ") do (\r\n"
        "  if not defined PY if exist \"%%~C\" set \"PY=%%~C\"\r\n"
        ")\r\n"
        "if not defined PY (\r\n"
        "  py -3 -c \"import sys\" >nul 2>nul && set \"PY=py -3\"\r\n"
        ")\r\n"
        "if not defined PY (\r\n"
        "  for /f \"delims=\" %%P in ('where python 2^>nul') do (\r\n"
        "    if not defined PY (\r\n"
        "      echo %%P | find /i \"WindowsApps\" >nul || set \"PY=%%P\"\r\n"
        "    )\r\n"
        "  )\r\n"
        ")\r\n"
        "if not defined PY (\r\n"
        "  for %%D in (\r\n"
        "    \"%LOCALAPPDATA%\\Programs\\Python\\Python314\\python.exe\"\r\n"
        "    \"%LOCALAPPDATA%\\Programs\\Python\\Python313\\python.exe\"\r\n"
        "    \"%LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe\"\r\n"
        "  ) do (\r\n"
        "    if not defined PY if exist \"%%~D\" set \"PY=%%~D\"\r\n"
        "  )\r\n"
        ")\r\n"
        "if not defined PY (\r\n"
        "  echo.\r\n"
        "  echo   Python not found - starting first-time setup ...\r\n"
        "  echo.\r\n"
        "  call \"%~dp0SETUP.bat\"\r\n"
        "  exit /b %ERRORLEVEL%\r\n"
        ")\r\n"
        "set PYTHONIOENCODING=utf-8\r\n"
        "set PYTHONUTF8=1\r\n"
        "set NO_PROXY=127.0.0.1,localhost\r\n"
        "set no_proxy=127.0.0.1,localhost\r\n"
        "%PY% \"%~dp0cli.py\" %*\r\n"
        "echo.\r\n"
        "pause\r\n"
    )
    (ROOT / "run.bat").write_bytes(ascii_only(run_bat))

    # ---- 中文名薄包装（内容两行，纯 ASCII）----
    outer = ("@echo off\r\n"
             "rem ASCII-only content; call the shared launcher.\r\n"
             "call \"%~dp0run.bat\" {action}\r\n")
    for action, name in (("open", "开热水.bat"), ("close", "关热水.bat")):
        (ROOT / name).write_bytes(ascii_only(outer.format(action=action)))

    # ---- 自检：内容里绝不能有非 ASCII ----
    for name in ("run.bat", "开热水.bat", "关热水.bat"):
        p = ROOT / name
        if p.exists():
            bad = sum(1 for byte in p.read_bytes() if byte > 127)
            if bad:
                print(f"    ⚠ {name} 含 {bad} 个非 ASCII 字节（可能被 cmd 拆坏）")


def make_desktop_shortcuts() -> None:
    """在桌面创建两个快捷方式（用 VBS 调 cscript，最稳）"""
    desktop = Path(os.path.expanduser("~")) / "Desktop"
    if not desktop.exists():
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
            desktop = Path(winreg.QueryValueEx(k, "Desktop")[0])
        except Exception:
            return
    names = {"开热水": "开热水.bat", "关热水": "关热水.bat"}
    for label, target in names.items():
        tp = ROOT / target
        if not tp.exists():
            continue
        lnk = desktop / f"{label}.lnk"
        vbs = (f'Set s = CreateObject("WScript.Shell")\r\n'
               f'Set l = s.CreateShortcut("{lnk}")\r\n'
               f'l.TargetPath = "{tp}"\r\n'
               f'l.WorkingDirectory = "{ROOT}"\r\n'
               f'l.IconLocation = "%SystemRoot%\\System32\\SHELL32.dll,27"\r\n'
               f'l.Save\r\n')
        tmp = STATE / "_mklnk.vbs"
        STATE.mkdir(parents=True, exist_ok=True)
        tmp.write_text(vbs, encoding="gbk", errors="replace")
        try:
            subprocess.run(["cscript", "//nologo", str(tmp)],
                           capture_output=True, timeout=25)
        except Exception:
            pass
        try:
            tmp.unlink()
        except Exception:
            pass


def install_keepalive() -> None:
    """注册保活开机自启（票据过期会自动重登）"""
    try:
        subprocess.run([sys.executable, str(SRC / "keepalive.py"), "--install"],
                       timeout=60)
    except Exception as e:
        print(f"  ⚠ 保活注册失败：{e}")


def step_save(v, d: dict, room: dict, machine: dict) -> bool:
    box("第 5 步 / 共 5 步　保存配置")

    device_ver = find_device_ver(v, machine) or "01,07"
    mac = (machine.get("mac") or "").upper()
    if not mac:
        mac = str(machine.get("machineId") or "").upper()

    cfg = {
        "mobile": d.get("_phone", ""),
        "captcha_length": 5,
        "base_url": d.get("url") or "https://v3-app.lxt6.cn",
        "direct_connection": True,
        "device": {
            "machineId": machine.get("machineId") or "",
            "deviceVer": device_ver,
            "mac": mac,
            "name": f"{machine.get('machineName') or room.get('name')}　{room['addr']}",
            "typeId": 1,
            "channelWay": 4,
        },
        "schoolId": v.state.get("schoolId"),
        "investorId": v.state.get("investorId"),
    }

    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    # 兼容早期的 config.json 位置
    (ROOT / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  ✓ 配置已保存")
    print(f"      设备号   {cfg['device']['machineId']}")
    print(f"      机型固件 {device_ver}")
    print(f"      位置     {room['addr']}")

    is4g = device_ver.split(",")[-1].strip() in {
        "01", "02", "03", "04", "05", "06", "07", "08", "09", "10",
        "11", "12", "13", "14", "15", "16", "17", "18", "19", "20",
    }
    print(f"      通路     {'服务器下发（4G，不需要蓝牙）' if is4g else '蓝牙直连'}")

    print("\n  正在生成启动器…")
    make_launchers()
    print("    ✓ 开热水.bat / 关热水.bat")

    print("  正在创建桌面快捷方式…")
    make_desktop_shortcuts()
    print("    ✓ 桌面上的「开热水」「关热水」")

    print("  正在注册保活（票据过期自动重登）…")
    install_keepalive()

    return True


# ──────────────────────── 主流程 ────────────────────────
BANNER = r"""
  ╔══════════════════════════════════════════════════════════════════════╗
  ║                    宿舍热水 · 一键配置                                  ║
  ║        服务器下发开阀 · 不用蓝牙 · 不用掏手机 · 没有广告                    ║
  ╚══════════════════════════════════════════════════════════════════════╝
"""


def main() -> int:
    print(BANNER)
    print("  本工具只操作你自己宿舍的设备、用你自己的账号，用水照常计费。")
    print("  密码只用于登录本机加密保存（见 src/credentials.py），不上传任何地方。")
    print("  按 Ctrl+C 可以随时退出。\n")
    if not yes("开始配置吗？"):
        return 0

    if not ensure_deps():
        print("\n  环境检查没通过，请把上面的报错发我。")
        return 1

    d = step_login()
    if not d:
        return 1

    from valve import Valve
    v = Valve(cfg={"mobile": d.get("_phone", ""), "device": {}}, verbose=True)
    v.token = d.get("token", "")
    v.host = (d.get("url") or v.host).rstrip("/")
    v._save_state(studentId=d.get("studentId", ""))

    if not step_org(v):
        print("\n  ✗ 读取学校信息失败，无法继续。")
        return 1

    room = step_room(v)
    if not room:
        return 1

    machine = step_machine(v, room)
    if not machine:
        return 1

    if not step_save(v, d, room, machine):
        return 1

    print()
    print("  " + "═" * 70)
    print("   ✅ 配置完成！")
    print("  " + "═" * 70)
    print("""
   以后怎么用：

     双击桌面「开热水」 → 水就来了
     双击桌面「关热水」 → 关掉，并告诉你这次用了多久

   验证码全自动识别，登录过期自动重登，你什么都不用管。
""")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n  已取消。")
        sys.exit(130)
