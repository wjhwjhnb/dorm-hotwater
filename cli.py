#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统一命令行入口

    python cli.py open       开阀（会真放水并计费）
    python cli.py close      关阀，并告诉你这次用了多久
    python cli.py status     查状态
    python cli.py login      仅登录（刷新会话，不碰阀门）
    python cli.py cycle 8    开 → 8 秒 → 关（自检）
    python cli.py probe      零成本预检参数（不碰真设备）
    python cli.py device     读设备信息（只读）
    python cli.py whoami     显示识别引擎、账号、当前会话
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TAGS = {"open": "开热水", "close": "关热水", "status": "查状态",
        "login": "登录", "cycle": "自检", "probe": "预检", "device": "设备"}


def _client(tag="热水"):
    from valve import Valve
    return Valve(verbose=True, tag=tag)


def main(argv: list[str]) -> int:
    cmd = (argv[1] if len(argv) > 1 else "help").lower()

    if cmd in ("help", "-h", "--help"):
        print(__doc__)
        return 0

    if cmd == "whoami":
        import captcha as C
        from valve import Valve
        v = Valve(verbose=False)
        print(f"  验证码引擎 : {C.engine()[1] or '（无，将转人工）'}")
        print(f"  账号       : {v.cfg.get('mobile') or '（未配置）'}")
        d = v.dev
        print(f"  设备       : {d.get('name') or ''}  {d.get('machineId')}  ver {d.get('deviceVer')}")
        print(f"  通路       : channelWay={d.get('channelWay')}（4 = 服务器下发，不用蓝牙）")
        print(f"  业务域名   : {v.host}")
        tok = v.token or ""
        h = tok.split("#?")[-1][:8] if "#?" in tok else (tok[:8] or "（无）")
        print(f"  当前会话   : {h}")
        print(f"  已登录     : {'是' if v.token else '否'}")
        return 0

    if cmd == "probe":
        from valve import Valve
        import lxt_login as L
        v = Valve(verbose=True, tag=TAGS["probe"])
        body = {"typeId": v.dev.get("typeId", 1),
                "channelWay": v.dev.get("channelWay", 4),
                "machineId": "0000000000000000",
                "deviceVer": v.dev.get("deviceVer", "01,07"),
                "machineInfoJson": {"lastPosMoney": "0000", "lastPosSerial": "0000"}}
        try:
            v.call("/bath/valve/open", body, "POST")
            print("  ？居然成功了，请人工确认")
            return 1
        except L.LoginError as e:
            msg = e.message or ""
            print(f"  Code={e.code}  {msg}")
            ok = any(k in msg for k in ("未授权", "不存在", "设备[")) and e.code != 0
            print("  ✅ 参数形态正确" if ok else "  ✗ 参数可能有问题")
            return 0 if ok else 1

    if cmd in ("device", "info"):
        v = _client(TAGS["device"])
        print(json.dumps(v.device_info(), ensure_ascii=False, indent=2))
        return 0

    if cmd == "login":
        v = _client(TAGS["login"])
        v.login()
        print("  ✅ 登录成功，会话已刷新")
        return 0

    if cmd == "open":
        v = _client(TAGS["open"])
        t0 = time.time()
        v.open()
        print(f"  （耗时 {time.time()-t0:.2f} 秒）")
        return 0

    if cmd == "close":
        v = _client(TAGS["close"])
        t0 = time.time()
        v.close()
        print(f"  （耗时 {time.time()-t0:.2f} 秒）")
        return 0

    if cmd == "status":
        _client(TAGS["status"]).status()
        return 0

    if cmd == "cycle":
        n = int(argv[2]) if len(argv) > 2 else 8
        v = _client(TAGS["cycle"])
        v.open()
        for i in range(n, 0, -1):
            print(f"    {i} …", flush=True)
            time.sleep(1)
        v.close()
        print("  自检结束")
        return 0

    print(f"未知命令：{cmd}\n")
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:
        print("\n  已取消")
        sys.exit(130)
    except Exception as e:
        print(f"\n  ✗ {type(e).__name__}: {e}")
        sys.exit(1)
