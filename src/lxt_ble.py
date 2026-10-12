#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
lxt_ble.py —— 乐校通水控器 BLE 客户端（基于逆向出的完整协议）

设备：BT19S-T   蓝牙地址 AA:BB:CC:DD:EE:FF
服务：0000FFE0   特征：0000FFE1（notify + write）

开阀流程（源自 util/nbBle.js 的 M 函数）：
    send08                        取随机数
    send02 {cmd:02, type:00, ID: studentHex+"00000000"}   授权
        → 从回复 data 取 systemData = data[20:36], systemTime = data[36:48]
    send06 {cmd:06, type:02, data: systemData+systemTime}   ★开阀
关阀：send14 {ID}   data = "00" + ID
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# 让本文件无论从哪里运行都能 import lxt_nb
_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import lxt_nb as nb
from config import AppConfig

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 从 config.json 读取（没有就用占位默认值，向导会写入真实值）
_CFG = AppConfig.load()

ADDRESS = _CFG.machine.address or "AA:BB:CC:DD:EE:FF"
SERVICE = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR = "0000ffe1-0000-1000-8000-00805f9b34fb"

# 你的身份标识（来自服务器 getUserInfo 的 studentHex）
MY_STUDENT_HEX = _CFG.machine.student_hex or "00112233"
MY_ID = _CFG.machine.id8          # 8 字节，如 0011223300000000


@dataclass
class DeviceInfo:
    sn: str = ""
    mac: str = ""
    imei: str = ""


class LxtError(Exception):
    pass


class LxtDevice:
    def __init__(self, address: str = ADDRESS, verbose: bool = True):
        self.address = address
        self.verbose = verbose
        self._client = None
        self._acc = ""                      # 收帧累积缓冲（hex）
        self._frame_evt: asyncio.Event | None = None
        self._frame: str | None = None

    # ---------------- 日志 ----------------
    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"  [{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    # ---------------- 通知回调 ----------------
    def _on_notify(self, _char, data: bytearray) -> None:
        chunk = bytes(data).hex().upper()
        self.log(f"← 收到 {len(data):>2}B  {chunk}")
        self._acc += chunk
        # 尝试反转义
        try:
            decoded = nb.unescape(self._acc)
        except Exception:
            decoded = self._acc
        if len(decoded) < 4:
            return
        try:
            total = 2 * int(decoded[2:4], 16) + 6      # ★ 帧长规则
        except ValueError:
            return
        if len(decoded) >= total:
            self._frame = decoded[:total]
            self._acc = ""
            if self._frame_evt is not None:
                self._frame_evt.set()

    # ---------------- 连接 ----------------
    async def connect(self, timeout: float = 20.0) -> None:
        from bleak import BleakClient
        self._client = BleakClient(self.address, timeout=timeout)
        await self._client.connect()
        if not self._client.is_connected:
            raise LxtError("连接失败")
        self.log(f"已连接  MTU={getattr(self._client, 'mtu_size', '?')}")
        await self._client.start_notify(CHAR, self._on_notify)
        self.log("已订阅通知")

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.stop_notify(CHAR)
            except Exception:
                pass
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self.log("已断开")

    # ---------------- 收发 ----------------
    async def send(self, frame_hex: str, wait: float = 6.0) -> dict | None:
        """发送一帧并等待完整回复，返回 parse_recv_frame 结果（超时返回 None）"""
        if self._client is None:
            raise LxtError("未连接")
        self._acc = ""
        self._frame = None
        self._frame_evt = asyncio.Event()

        payload = bytes.fromhex(frame_hex)
        self.log(f"→ 发送 {len(payload):>2}B  {frame_hex}")
        try:
            await self._client.write_gatt_char(CHAR, payload, response=False)
        except Exception as exc:
            self.log(f"   无响应写入失败({type(exc).__name__})，改用带响应写入")
            await self._client.write_gatt_char(CHAR, payload, response=True)

        try:
            await asyncio.wait_for(self._frame_evt.wait(), wait)
        except asyncio.TimeoutError:
            self.log(f"   等待 {wait}s 超时，无完整回复")
            return None

        assert self._frame is not None
        parsed = nb.parse_recv_frame(self._frame)
        self.log(f"   解析: cmd={parsed['cmd']} type={parsed['type']} "
                 f"ver={parsed['version']} way={parsed['way']} status={parsed['status']} "
                 f"data({len(parsed['data'])//2}B)={parsed['data']}")
        return parsed

    # ---------------- 业务 ----------------
    async def cmd_f0(self) -> dict | None:
        self.log("命令 F0 取设备信息")
        r = await self.send(nb.cmd_f0())
        if r and len(r["data"]) >= 44:
            d = r["data"]
            info = DeviceInfo(sn=d[0:16], mac=d[16:28], imei=d[28:44])
            self.log(f"   SN={info.sn}  MAC={info.mac}  IMEI={info.imei}")
            return {"info": info, "raw": r}
        return r

    async def cmd_08(self) -> dict | None:
        self.log("命令 08 取随机数")
        return await self.send(nb.cmd_08())

    async def cmd_02_auth(self, type_: str = "00", id8: str = MY_ID) -> dict | None:
        self.log(f"命令 02 授权 (type={type_})")
        return await self.send(nb.cmd_02(type_, id8))

    async def cmd_06_open(self, type_: str, system_data: str, system_time: str,
                          agreement: str = "", cmd: str = "06") -> dict | None:
        self.log(f"命令 {cmd} ★开阀 (type={type_})")
        return await self.send(nb.cmd_06_open(type_, system_data, system_time, agreement, cmd=cmd))

    async def cmd_14_close(self, id8: str = MY_ID) -> dict | None:
        self.log("命令 14 ★关阀")
        return await self.send(nb.cmd_14_close(id8))

    async def open_valve(self) -> tuple[bool, str]:
        """
        完整开阀流程。返回 (成功?, 说明)
        """
        # ① 取随机数
        r08 = await self.cmd_08()
        if r08 is None:
            return False, "取随机数无响应"
        if r08["cmd"] not in ("08",):
            self.log(f"  (cmd 期望 08，实收 {r08['cmd']}，继续)")

        # ② 授权（type=01：实测才返回含 systemData 的长回复）
        r02 = await self.cmd_02_auth("01", MY_ID)
        if r02 is None:
            return False, "授权无响应"
        status = r02["data"][0:2] if len(r02["data"]) >= 2 else ""
        if status not in ("00", "01"):
            return False, f"授权被拒（status={status}）"
        if len(r02["data"]) < 48:
            return False, f"授权回复太短（{len(r02['data'])//2} 字节），无法取 systemData"

        system_data, system_time = nb.extract_system_from_02(r02)
        self.log(f"  systemData={system_data}  systemTime={system_time}")

        # ③ 开阀
        type_ = "02"
        r06 = await self.cmd_06_open(type_, system_data, system_time)
        if r06 is None:
            return False, "开阀命令无响应"
        st = r06["data"][0:2] if len(r06["data"]) >= 2 else ""
        if st in ("00", "AA"):
            return True, "开阀成功"
        if st == "01":
            return False, "授权失败"
        if st == "03":
            return False, "设备正忙，无法使用"
        return False, f"开阀返回异常（status={st}）"


# ---------------- 调试入口 ----------------
async def _main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="乐校通水控器 BLE 调试")
    ap.add_argument("action", choices=["info", "rnd", "auth", "open", "close"])
    ap.add_argument("--address", default=ADDRESS)
    ap.add_argument("--type", default="01", dest="ctype",
                    help="cmd 02 授权的 type（00 或 01），01 通常返回含 systemData 的长回复")
    args = ap.parse_args()

    dev = LxtDevice(args.address)
    try:
        await dev.connect()
        if args.action == "info":
            await dev.cmd_f0()
        elif args.action == "rnd":
            await dev.cmd_08()
        elif args.action == "auth":
            r = await dev.cmd_02_auth(args.ctype, MY_ID)
            if r and len(r["data"]) >= 48:
                sd, st = nb.extract_system_from_02(r)
                print(f"\n  ★ systemData = {sd}")
                print(f"  ★ systemTime = {st}")
                print(f"     -> 可以据此构造开阀帧（cmd 06）")
            else:
                n = len(r["data"]) // 2 if r else 0
                print(f"\n  回复 data 只有 {n} 字节，不含 systemData")
        elif args.action == "open":
            ok, msg = await dev.open_valve()
            print(f"\n结果: {'✅' if ok else '❌'} {msg}")
        elif args.action == "close":
            r = await dev.cmd_14_close()
            print(f"\n关阀回复: {r}")
    except Exception as exc:
        print(f"[X] {type(exc).__name__}: {exc}")
        return 1
    finally:
        await dev.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
