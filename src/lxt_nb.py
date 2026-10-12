#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
lxt_nb.py —— 乐校通「NB」蓝牙水控协议编解码（从解密后的小程序源码逆向得到）

来源：__APP__.wxapkg → util/nb.js（帧构建/校验/转义）、util/nbOperation.js（命令集）、
      util/nbBle.js（开阀流程）、util/nb.js resolveData（收帧解析）

═══════════════════════════════════════════════════════════════════════
一、发送帧格式（sendData）
═══════════════════════════════════════════════════════════════════════
    A5 <len> <d> <g> <c1> <c2> <c3> <data...> <sum>

    d, g   : 各 1 字节随机数
    c1 = u(rns1, g, d)
    c2 = u(rns0, g, d)
    c3 = u(cmd,  g, d)
    len    : 5 + len(data)        （即 <len> + 3 == 总字节数）
    sum    : (d + g + c1 + c2 + c3 + data...) & 0xFF
    最后整体转义：A5 → 1BE7，1BE7 → 1B00E7

    u(x, t, r) = (0xAA + (x ^ t ^ r)) & 0xFF     ← 生成校验（getResCode）

二、校验/解码
    i(x, t, r) = ((x - 0xAA) & 0xFF) ^ t ^ r     ← 反解（getCode）

三、接收帧格式（resolveData）
    A5 <len> <rns3> <rns2> <rns1> <rns0> <cmd> <type> <ver> <way> <status> <data...> <sum>
    其中 rns3/rns2 为明文，其余 7 个用 i(x, rns2, rns3) 解码。

四、命令码
    F0  取设备信息（回复 data: SN(8) + MAC(6) + IMEI(8) ...）
    08  取随机数
    02  授权      data = type(1) + ID(8)
    32  授权（部分机型）
    06  开阀/用水 data = type(1) + systemData(8) + systemTime(6) [+ agreementNo]
    46  开阀（机型 01/93 专用）
    14  关阀      data = "00" + ID(8)
    01  appKey 授权  data = "00" + xorData + 时间
    07  更新
"""

from __future__ import annotations

import os
import random
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ──────────────────────────── 基础工具 ────────────────────────────
def _hex2(v: int) -> str:
    """int → 2 位大写十六进制"""
    return f"{v & 0xFF:02X}"


def rnd_hex() -> str:
    """o()：随机 1 字节的 2 位十六进制"""
    return _hex2(random.randrange(256))


def u(x: int, t: int, r: int) -> int:
    """getResCode：生成校验字节 = (0xAA + (x^t^r)) & 0xFF"""
    return (0xAA + (x ^ t ^ r)) & 0xFF


def i_(x: int, t: int, r: int) -> int:
    """getCode：反解 = ((x - 0xAA) & 0xFF) ^ t ^ r"""
    return ((x - 0xAA) & 0xFF) ^ t ^ r


# ──────────────────────────── 转义 ────────────────────────────
def escape(frame_hex: str) -> str:
    """changeChar(restore=False)：A5 → 1BE7，1BE7 → 1B00E7（跳过开头的 A5）"""
    body = frame_hex[2:]
    out: list[str] = []
    p = 0
    n = len(body)
    while p < n:
        b = body[p:p + 2]
        if b == "A5":
            out.append("1BE7")
        elif body[p:p + 4] == "1BE7":
            out.append("1B00E7")
            p += 2
        else:
            out.append(b)
        p += 2
    return "A5" + "".join(out)


def unescape(frame_hex: str) -> str:
    """changeChar(restore=True)：1BE7 → A5，1B00E7 → 1BE7"""
    body = frame_hex[2:]
    out: list[str] = []
    p = 0
    n = len(body)
    while p < n:
        g = body[p:p + 2]
        if g == "1B":
            if body[p + 2:p + 4] == "E7":
                out.append("A5")
                p += 2
            elif body[p + 2:p + 6] == "00E7":
                out.append("1BE7")
                p += 4
            else:
                out.append(g)
        else:
            out.append(g)
        p += 2
    return "A5" + "".join(out)


# ──────────────────────────── 发送帧构建 ────────────────────────────
def build_frame(cmd: str, data: str = "",
                rns0: str | None = None, rns1: str | None = None,
                d: str | None = None, g: str | None = None) -> str:
    """
    构建发送帧，返回【已转义】的十六进制字符串。

    cmd  : 2 位十六进制命令码，如 "08" "02" "06" "14"
    data : 十六进制数据（偶数长度）
    rns0/rns1/d/g : 可显式指定（用于复现抓包）；默认随机
    """
    if not cmd:
        raise ValueError("cmd 不能为空")
    if len(data) % 2:
        raise ValueError("data 必须是偶数个十六进制字符")

    d = d or rnd_hex()
    g = g or rnd_hex()
    rns0 = rns0 or rnd_hex()
    rns1 = rns1 or rnd_hex()

    di, gi = int(d, 16), int(g, 16)
    p = (d + g
         + _hex2(u(int(rns1, 16), gi, di))
         + _hex2(u(int(rns0, 16), gi, di))
         + _hex2(u(int(cmd, 16), gi, di)))
    body = p + data
    nbytes = len(body) // 2
    if nbytes > 255:
        raise ValueError("帧过长")
    s = sum(int(body[k:k + 2], 16) for k in range(0, len(body), 2)) & 0xFF
    raw = "A5" + _hex2(nbytes) + body + _hex2(s)
    return escape(raw)


def build_raw(cmd: str, data: str = "",
              rns0: str | None = None, rns1: str | None = None,
              d: str | None = None, g: str | None = None) -> str:
    """同 build_frame，但返回【未转义】的原始十六进制（便于调试/对比）"""
    d = d or rnd_hex()
    g = g or rnd_hex()
    rns0 = rns0 or rnd_hex()
    rns1 = rns1 or rnd_hex()
    di, gi = int(d, 16), int(g, 16)
    p = (d + g
         + _hex2(u(int(rns1, 16), gi, di))
         + _hex2(u(int(rns0, 16), gi, di))
         + _hex2(u(int(cmd, 16), gi, di)))
    body = p + data
    s = sum(int(body[k:k + 2], 16) for k in range(0, len(body), 2)) & 0xFF
    return "A5" + _hex2(len(body) // 2) + body + _hex2(s)


def decode_sent_frame(frame_hex: str) -> dict:
    """反解一个【已转义】的发送帧，还原 d/g/rns0/rns1/cmd/data/sum。用于验证。"""
    raw = unescape(frame_hex)
    if raw[:2] != "A5":
        raise ValueError("不是 A5 帧")
    length = int(raw[2:4], 16)
    body = raw[4:4 + length * 2]
    cs = raw[4 + length * 2:6 + length * 2]
    d, g = body[0:2], body[2:4]
    di, gi = int(d, 16), int(g, 16)
    rns1 = _hex2(i_(int(body[4:6], 16), gi, di))
    rns0 = _hex2(i_(int(body[6:8], 16), gi, di))
    cmd = _hex2(i_(int(body[8:10], 16), gi, di))
    data = body[10:]
    calc = sum(int(body[k:k + 2], 16) for k in range(0, len(body), 2)) & 0xFF
    return {
        "raw": raw, "len": length, "total_bytes": len(raw) // 2,
        "d": d, "g": g, "rns1": rns1, "rns0": rns0,
        "cmd": cmd, "data": data,
        "sum": cs, "sum_ok": cs.upper() == _hex2(calc),
    }


def parse_recv_frame(frame_hex: str) -> dict:
    """
    解析【设备 → 手机】的帧（resolveData）。
    输入可为已转义或未转义；内部会先 unescape。
    """
    raw = unescape(frame_hex) if frame_hex[:2].upper() == "A5" and "1B" in frame_hex.upper() else frame_hex.upper()
    r = raw[4:len(raw) - 2]          # 去掉 A5+len 和末尾校验
    rns3, rns2 = int(r[0:2], 16), int(r[2:4], 16)
    keys = ["rns1", "rns0", "cmd", "type", "version", "way", "status"]
    out = {"rns3": r[0:2], "rns2": r[2:4]}
    for idx, k in enumerate(keys):
        x = int(r[4 + idx * 2:6 + idx * 2], 16)
        out[k] = _hex2(i_(x, rns2, rns3))
    out["data"] = r[18:]
    out["raw"] = raw
    out["total_bytes"] = len(raw) // 2
    return out


# ──────────────────────────── 业务封装 ────────────────────────────
def cmd_f0() -> str:
    """取设备信息"""
    return build_frame("F0")


def cmd_08() -> str:
    """取随机数"""
    return build_frame("08")


def cmd_02(type_: str, id8: str = "00000000", cmd: str = "02") -> str:
    """授权：data = type(1B) + ID(8B)。type 常用 '00'/'01'"""
    if len(id8) != 16:
        raise ValueError("ID 必须是 16 位十六进制（8 字节）")
    return build_frame(cmd, type_.zfill(2) + id8.upper())


def cmd_06_open(type_: str, system_data: str, system_time: str,
                agreement: str = "", cmd: str = "06") -> str:
    """★开阀：data = type + systemData(8B) + systemTime(6B) [+ agreement]"""
    data = type_.zfill(2) + system_data.upper() + system_time.upper() + agreement.upper()
    return build_frame(cmd, data)


def cmd_14_close(id8: str) -> str:
    """★关阀：data = '00' + ID(8B)"""
    if len(id8) != 16:
        raise ValueError("ID 必须是 16 位十六进制（8 字节）")
    return build_frame("14", "00" + id8.upper())


def extract_system_from_02(reply: dict) -> tuple[str, str]:
    """
    从 cmd-02 回复里取 systemData / systemTime。
    源码： data.substr(20,16) = systemData(8B)
           data.substr(36,12) = systemTime(6B)
    """
    d = reply.get("data", "")
    if len(d) < 48:
        raise ValueError(f"cmd-02 回复 data 太短: {len(d)} hex")
    return d[20:36], d[36:48]


# ──────────────────────────── 自检 ────────────────────────────
# 说明：这里用【纯往返测试】——构造帧 → 反解 → 重建 → 必须逐字节一致。
# 好处：不依赖任何外部数据，脱敏/改默认值都不会破坏它。
# （开发过程中还额外用真实抓包帧验证过一遍：F0 / 08 / 02 / 06 四帧全部逐字节吻合；
#   那批样本属于个人数据，已从本文件移除，仅保留在开发者的私有记录里。）
ROUNDTRIP_CASES = [
    # (cmd, data)
    ("F0", ""),
    ("08", ""),
    ("02", "01" + "00112233" + "00000000"),                 # 授权：type + 8字节ID
    ("06", "01" + "A1" * 16 + "B2" * 6),                    # 开阀：type + 16B + 6B
    ("14", "00" + "00112233" + "00000000"),                 # 关阀：00 + 8字节ID
    ("F0", "AB" * 40),                                      # 带较长载荷
]


def selftest() -> int:
    print("=" * 88)
    print("自检 1/3：帧往返（构造 → 反解 → 重建 → 逐字节比对）")
    print("=" * 88)
    all_ok = True
    for cmd, data in ROUNDTRIP_CASES:
        raw = build_raw(cmd, data, rns0="41", rns1="7C", d="C5", g="E8")
        dec = decode_sent_frame(escape(raw))
        rebuilt = build_raw(dec["cmd"], dec["data"],
                            rns0=dec["rns0"], rns1=dec["rns1"], d=dec["d"], g=dec["g"])
        ok = rebuilt.upper() == raw.upper() and dec["sum_ok"]
        all_ok &= ok
        print(f"\n  cmd={cmd:<3} data={data[:26]}{'…' if len(data) > 26 else ''}")
        print(f"  构造帧 : {raw}")
        print(f"  反解出 : cmd={dec['cmd']} d={dec['d']} g={dec['g']} "
              f"rns1={dec['rns1']} rns0={dec['rns0']}  校验={dec['sum']}"
              f"({'OK' if dec['sum_ok'] else 'BAD'})")
        print(f"  重建帧 : {rebuilt}")
        print(f"  结果   : {'✅ 一致' if ok else '❌ 不一致'}")

    print()
    print("=" * 88)
    print("自检 2/3：转义 / 反转义（A5 → 1BE7，1BE7 → 1B00E7）")
    print("=" * 88)
    for s in ["A5A5", "A51BE7A5", "A5A1B2C3", "A5" + "1B" * 6]:
        e = escape(s)
        back = unescape(e)
        ok = back == s
        all_ok &= ok
        print(f"  {s}  --转义-->  {e}  --还原-->  {back}   {'✅' if ok else '❌'}")

    print()
    print("=" * 88)
    print("自检 3/3：帧长规则（<len> + 3 == 帧字节数）")
    print("=" * 88)
    for cmd, data in ROUNDTRIP_CASES:
        raw = build_raw(cmd, data, rns0="41", rns1="7C", d="C5", g="E8")
        length = int(raw[2:4], 16)
        nbytes = len(raw) // 2
        ok = (length + 3 == nbytes)
        all_ok &= ok
        print(f"  cmd={cmd:<3} len=0x{length:02X}({length})  帧={nbytes} 字节  "
              f"{'✅' if ok else '❌'}")

    print()
    print("=" * 88)
    print("示例：生成各种命令帧（把下面的 MY_ID 换成你自己的 studentHex）")
    print("=" * 88)
    MY_ID = "00112233" + "00000000"
    print(f"\n  取设备信息 F0 : {cmd_f0()}")
    print(f"  取随机数  08 : {cmd_08()}")
    print(f"  授权      02 : {cmd_02('00', MY_ID)}")
    print(f"  关阀      14 : {cmd_14_close(MY_ID)}")

    print()
    print("=" * 88)
    print("自检结果:", "✅ 全部通过" if all_ok else "❌ 有失败项")
    print("=" * 88)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(selftest())
