#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
credentials.py —— 在本机安全保存登录凭据

为什么要这个东西：
    乐校通的登录接口（userLoginV2）**强制要求短信验证码**，而短信只到用户手机上。
    所以做不到"全自动登录"。退而求其次：把账号密码加密存在本机，
    票据过期时自动重登——用户只需要在极少数情况下输一次短信码。

加密方式：Windows DPAPI（CryptProtectData）
    * 密钥由 Windows 从【当前用户】的登录凭据派生
    * 密文**只能被同一台机器、同一个 Windows 用户解开**
    * 拷给别人 / 重装系统 / 换个账户 → 打不开（这是好事）
    * 不需要我们管理任何密钥，也不会把密码写进日志

文件位置：state/credentials.dat（已在 .gitignore 里，不会进 git）

★ 安全提醒（会写进 README）：
    这仍然是"把密码存在本机"。如果你不能接受，就不要启用；
    跑 `python src/credentials.py --clear` 可以随时删除。
"""

from __future__ import annotations

import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path

_SRC = Path(__file__).resolve().parent
ROOT = _SRC.parent
STORE = ROOT / "state" / "credentials.dat"

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ──────────────────────── DPAPI ────────────────────────
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _protect(data: bytes) -> bytes:
    """DPAPI 加密"""
    out = _BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(_blob(data)), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptProtectData 失败")
    return ctypes.string_at(out.pbData, out.cbData)


def _unprotect(data: bytes) -> bytes:
    """DPAPI 解密"""
    out = _BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(_blob(data)), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptUnprotectData 失败（可能换了机器或 Windows 账户）")
    return ctypes.string_at(out.pbData, out.cbData)


# ──────────────────────── 读写 ────────────────────────
def save(phone: str, password: str) -> Path:
    """保存账号+密码（DPAPI 加密）"""
    payload = json.dumps({"phone": phone, "password": password},
                         ensure_ascii=False).encode("utf-8")
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_bytes(_protect(payload))
    try:
        # 尽量收紧权限（仅当前用户可读）
        import subprocess
        subprocess.run(["icacls", str(STORE), "/inheritance:r", "/grant:r",
                        f"{__import__('os').environ.get('USERNAME','')}:F"],
                       capture_output=True, timeout=15, check=False)
    except Exception:
        pass
    return STORE


def load() -> tuple[str, str]:
    """返回 (手机号, 密码)；没有或解不开就返回 ("","")"""
    if not STORE.exists():
        return "", ""
    try:
        obj = json.loads(_unprotect(STORE.read_bytes()).decode("utf-8"))
        return str(obj.get("phone") or ""), str(obj.get("password") or "")
    except Exception:
        return "", ""


def exists() -> bool:
    return STORE.exists()


def clear() -> bool:
    if STORE.exists():
        STORE.unlink()
        return True
    return False


def describe() -> str:
    if not STORE.exists():
        return "未保存凭据"
    p, w = load()
    if not p:
        return "凭据文件存在但解不开（可能换了机器/Windows 账户，需要重新登录）"
    return f"已保存：{p[:3]}****{p[-4:]}（密码 {len(w)} 位，已加密）"


# ──────────────────────── 自检 ────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="本机凭据管理")
    ap.add_argument("--save", nargs=2, metavar=("手机号", "密码"), help="保存（会加密）")
    ap.add_argument("--clear", action="store_true", help="删除已保存的凭据")
    ap.add_argument("--show", action="store_true", help="查看状态（不显示密码）")
    a = ap.parse_args()

    if a.clear:
        print("已删除" if clear() else "本来就没有")
        sys.exit(0)

    if a.save:
        p = save(a.save[0], a.save[1])
        print(f"✅ 已加密保存到 {p}")
        print(f"   状态：{describe()}")
        # 立刻验证能解回来
        _, w = load()
        print(f"   回读校验：{'✅ 通过' if w == a.save[1] else '❌ 失败'}")
        sys.exit(0)

    print(f"凭据文件：{STORE}")
    print(f"状态    ：{describe()}")
