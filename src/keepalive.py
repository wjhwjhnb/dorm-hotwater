#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
keepalive.py —— 会话保活

背景（实测结论，见 docs/开源计划.md 的 E3 实验）：
    乐校通服务器对登录会话是【滑动过期】。
      * 全程闲置  → 约 30 分钟失效
      * 每 5 分钟访问一次 → 38 分钟仍然有效（实测 8/8 成功）

    所以只要定期发一个很轻的请求，会话就不会过期。
    用户只需手动登录一次，之后可以长期免登录。

用法：
    python keepalive.py               前台运行（每 5 分钟一次，Ctrl+C 停）
    python keepalive.py --once        只探一次，检查票据状态
    python keepalive.py --install     注册开机自启（Windows 计划任务/启动项）
    python keepalive.py --uninstall   取消开机自启

隐私说明：
    保活请求只发往乐校通官方服务器（v3-app.lxt6.cn），内容为"查自己的用户信息"，
    不包含任何额外数据、不上传本地信息。不想常驻联网的用户可以不启用。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import lxt_api
from config import load_token, token_hash, ROOT

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_INTERVAL = 300          # 秒
LOG = ROOT / "logs" / "keepalive.log"
STARTUP_NAME = "DormHotWaterKeepalive"


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def ping() -> tuple[int | None, str]:
    """调一次轻量接口；返回 (Code, Message)"""
    tk = load_token()
    if not tk:
        return None, "本地没有票据"
    try:
        api = lxt_api.LxtApi(token=tk, domain="https://v3-app.lxt6.cn", verbose=False)
        r = api.get("user/basic/getUserInfo", timeout=15)
        return r.get("Code"), str(r.get("Message") or "")
    except Exception as exc:
        return None, f"网络异常 {type(exc).__name__}: {exc}"


# ──────────────────────── 开机自启 ────────────────────────
def _startup_dir() -> Path:
    import os
    return (Path(os.environ.get("APPDATA", "")) /
            "Microsoft/Windows/Start Menu/Programs/Startup")


def install_autostart() -> int:
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pyw = Path(sys.executable)
    vbs = _startup_dir() / f"{STARTUP_NAME}.vbs"
    vbs.parent.mkdir(parents=True, exist_ok=True)
    body = (
        'Set ws = CreateObject("WScript.Shell")\r\n'
        f'ws.Run """{pyw}"" ""{_SRC / "keepalive.py"}""", 0, False\r\n'
    )
    vbs.write_bytes(body.encode("utf-8"))
    print(f"✅ 已注册开机自启：{vbs}")
    print("   （放在「启动」文件夹里，每次开机自动后台保活）")
    print("   取消：python src/keepalive.py --uninstall")
    return 0


def uninstall_autostart() -> int:
    vbs = _startup_dir() / f"{STARTUP_NAME}.vbs"
    if vbs.exists():
        vbs.unlink()
        print(f"✅ 已取消开机自启：{vbs}")
    else:
        print("（本来就没注册）")
    return 0


# ──────────────────────── 主循环 ────────────────────────
def notify(title: str, text: str) -> None:
    """弹一个 Windows 提示框（保活是后台跑的，必须主动告诉用户）"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40 | 0x40000 | 0x10000)
    except Exception:
        pass


def prompt_relogin() -> bool:
    """
    票据失效 → 拉起一个【可见的】登录窗口，让用户输一次短信码。
    返回是否已拉起（不代表登录成功）。
    """
    import subprocess
    try:
        import credentials
        phone, pw = credentials.load()
    except Exception:
        phone, pw = "", ""

    if not (phone and pw):
        notify("宿舍热水：需要重新登录",
               "登录已过期，且本机没有保存账号密码。\n\n"
               "请运行 安装配置.bat（或 登录乐校通.bat）完成一次登录。")
        return False

    notify("宿舍热水：需要重新登录",
           f"登录已过期（账号 {phone[:3]}****{phone[-4:]}）。\n\n"
           "马上会弹出一个窗口，密码已自动填好，\n"
           "你只需要输入【图片验证码】和【手机短信码】。\n\n"
           "完成后这个窗口会自动关闭。")
    try:
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        subprocess.Popen([sys.executable, str(_SRC / "lxt_login.py"),
                          "--phone", phone], creationflags=flags)
        return True
    except Exception as exc:
        log(f"拉起登录窗口失败：{type(exc).__name__}: {exc}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="登录会话保活")
    ap.add_argument("--once", action="store_true", help="只探一次")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                    help=f"探测间隔秒数（默认 {DEFAULT_INTERVAL}）")
    ap.add_argument("--install", action="store_true", help="注册开机自启")
    ap.add_argument("--uninstall", action="store_true", help="取消开机自启")
    ap.add_argument("--no-relogin", action="store_true", help="失效时不自动弹登录窗口")
    a = ap.parse_args()

    if a.install:
        return install_autostart()
    if a.uninstall:
        return uninstall_autostart()

    tk = load_token()
    if not tk:
        log("✗ 本地没有票据，请先跑 安装配置.bat")
        return 1
    log(f"保活启动  会话 {token_hash(tk)[:8]}…  间隔 {a.interval}s")

    fails = 0
    relaunched = False
    while True:
        code, msg = ping()
        h = token_hash(load_token())
        if code == 0:
            fails = 0
            relaunched = False
            log(f"Code=0  {msg}  (会话 {h[:8]}…)")
        elif code == -44:
            fails += 1
            log(f"Code=-44 会话已失效（连续失败 {fails}）")
            if not a.no_relogin and not relaunched:
                if prompt_relogin():
                    log("已拉起登录窗口，等待你完成后继续保活…")
                    relaunched = True
                    # 给用户几分钟时间完成登录
                    for _ in range(60):
                        time.sleep(10)
                        c2, _m2 = ping()
                        if c2 == 0:
                            log("✅ 重新登录成功，保活继续")
                            fails = 0
                            relaunched = False
                            break
                    else:
                        log("等待重新登录超时")
            if fails >= 3:
                log("多次失效，停止保活。请运行 安装配置.bat 重新登录。")
                return 2
        else:
            fails += 1
            log(f"Code={code}  {msg}")

        if a.once:
            return 0 if code == 0 else 1
        try:
            time.sleep(a.interval)
        except KeyboardInterrupt:
            log("收到 Ctrl+C，保活退出")
            return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已退出")
        sys.exit(0)
