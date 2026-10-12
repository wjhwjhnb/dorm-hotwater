#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
token_source.py —— 登录票据获取

这是整个开源方案里**最需要谨慎处理**的模块：它要拿到"用户已经登录好的会话"，
但**不碰密码、不碰验证码**。

提供三种策略，按优先级尝试：

  ① existing()          本地已存（state/token_full.txt）→ 校验是否还有效
  ② from_wechat_cookie() 读取微信 WebView 的 Cookie 库（需微信完全退出）
  ③ manual_paste()       用户手工粘贴（兜底）

设计约束：
  * 任何策略都**不接收、不存储用户密码**
  * 票据只写本地 state/，绝不外传（除了发给乐校通官方服务器）
  * 拿到的票据一律先调一次 getUserInfo 校验（Code=0 才算成功）
"""

from __future__ import annotations

import glob
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import lxt_api
from config import load_token, save_token, token_hash, AppConfig

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ──────────────────────── 校验 ────────────────────────
def verify(token: str, timeout: float = 15.0) -> tuple[bool, str, dict]:
    """
    调一次 getUserInfo 判断票据是否可用。
    返回 (是否有效, 说明, 用户信息)
    """
    if not token or "tokenInfo" not in token:
        return False, "票据为空或格式不对", {}
    try:
        api = lxt_api.LxtApi(token=token, domain="https://v3-app.lxt6.cn", verbose=False)
        r = api.get("user/basic/getUserInfo", timeout=timeout)
    except Exception as exc:
        return False, f"网络异常：{type(exc).__name__}: {exc}", {}
    code = r.get("Code")
    if code == 0:
        d = r.get("Data") or {}
        return True, "有效", d
    return False, f"Code={code} {r.get('Message')}", {}


# ──────────────────────── 策略 ① ────────────────────────
def existing() -> tuple[str, str]:
    """读本地已存的票据并校验"""
    tk = load_token()
    if not tk:
        return "", "本地没有票据"
    ok, msg, info = verify(tk)
    if ok:
        return tk, f"本地票据有效（{info.get('studentName','?')} / {info.get('studentHex','?')}）"
    return "", f"本地票据已失效（{msg}）"


# ──────────────────────── 策略 ② ────────────────────────
def _wechat_dirs() -> list[Path]:
    base = Path(os.environ.get("APPDATA", "")) / "Tencent/xwechat/radium/web/profiles"
    return sorted(base.glob("*/Network/Cookies")) if base.exists() else []


def _chromium_key(local_state: Path) -> bytes | None:
    """
    取出 Chromium 的 AES 密钥。
    Local State 里的 os_crypt.encrypted_key 是 DPAPI 加密的，同用户可解。
    """
    try:
        import base64
        import json as _json
        import ctypes
        from ctypes import wintypes

        raw = _json.loads(local_state.read_text(encoding="utf-8"))
        enc = base64.b64decode(raw["os_crypt"]["encrypted_key"])
        if enc[:5] != b"DPAPI":
            return None
        blob = enc[5:]

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        def _blob(data: bytes) -> DATA_BLOB:
            buf = ctypes.create_string_buffer(data, len(data))
            return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

        out = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(_blob(blob)), None, None, None, None, 0, ctypes.byref(out)):
            return None
        return ctypes.string_at(out.pbData, out.cbData)
    except Exception:
        return None


def _decrypt_cookie(enc_val: bytes, key: bytes | None) -> str:
    """
    Chromium cookie 的 encrypted_value：
      v10/v11 前缀 + 12 字节 nonce + 密文 + 16 字节 tag（AES-256-GCM）
    """
    if not enc_val:
        return ""
    if enc_val[:3] not in (b"v10", b"v11"):
        # 老格式：整块 DPAPI
        try:
            import ctypes
            from ctypes import wintypes

            class DATA_BLOB(ctypes.Structure):
                _fields_ = [("cbData", wintypes.DWORD),
                            ("pbData", ctypes.POINTER(ctypes.c_char))]

            buf = ctypes.create_string_buffer(enc_val, len(enc_val))
            binp = DATA_BLOB(len(enc_val), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
            out = DATA_BLOB()
            ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(binp), None, None, None, None, 0, ctypes.byref(out))
            return ctypes.string_at(out.pbData, out.cbData).decode("utf-8", "replace")
        except Exception:
            return ""
    if not key:
        return ""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce, payload = enc_val[3:15], enc_val[15:]
        return AESGCM(key).decrypt(nonce, payload, None).decode("utf-8", "replace")
    except Exception:
        return ""


def from_wechat_cookie(verbose: bool = True) -> tuple[str, str]:
    """
    从微信 WebView 的 Cookie 库里读出 tokenInfo。

    ⚠ 前提：微信必须**完全退出**，否则 Cookies 文件被独占锁住（WinError 32）。
    """
    dbs = _wechat_dirs()
    if not dbs:
        return "", "找不到微信的 Cookie 库（路径不存在）"

    # Local State 里拿密钥
    key = None
    for ls in (Path(os.environ.get("APPDATA", "")) / "Tencent/xwechat").rglob("Local State"):
        key = _chromium_key(ls)
        if key:
            break

    for db in dbs:
        # 文件可能被锁，用只读 URI 直接连；锁住就跳过
        tmp = None
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=3)
        except Exception:
            try:
                tmp = Path(tempfile.gettempdir()) / "lxt_cookies_copy"
                tmp.write_bytes(db.read_bytes())
                con = sqlite3.connect(f"file:{tmp.as_posix()}?mode=ro", uri=True, timeout=3)
            except Exception:
                if verbose:
                    print(f"  [跳过] {db.parent.parent.name}：文件被占用（请先完全退出微信）")
                continue
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT host_key, name, value, encrypted_value FROM cookies "
                "WHERE name LIKE '%tokenInfo%' OR name LIKE '%jsluid%'")
            rows = cur.fetchall()
        except Exception as exc:
            if verbose:
                print(f"  [跳过] {db.parent.parent.name}：{exc}")
            con.close()
            continue

        tok, jsl = "", ""
        for host, name, val, enc in rows:
            v = val or _decrypt_cookie(enc, key)
            if not v:
                continue
            if name == "tokenInfo":
                tok = v
            elif "jsluid" in name:
                jsl = v
        con.close()

        if tok:
            full = tok if tok.startswith("tokenInfo=") else f"tokenInfo={tok}"
            if jsl:
                full += f"; __jsluid_s={jsl}"
            ok, msg, info = verify(full)
            if ok:
                return full, f"从微信 Cookie 读出（{info.get('studentName','?')}）"
            return "", f"读到了票据但已失效：{msg}"
    return "", "微信 Cookie 里没有可用的登录票据"


# ──────────────────────── 策略 ③ ────────────────────────
def manual_paste() -> tuple[str, str]:
    print("""
  ── 手工粘贴票据 ──
  如果你会用抓包工具（如 mitmproxy / Fiddler），抓一条乐校通的请求，
  把请求头里的 x-token（或 Cookie）整串粘到下面。

  只要以 tokenInfo= 开头即可。直接回车跳过。
""")
    try:
        s = input("  票据: ").strip()
    except (EOFError, KeyboardInterrupt):
        return "", "已跳过"
    if not s:
        return "", "已跳过"
    ok, msg, info = verify(s)
    if ok:
        return s, f"有效（{info.get('studentName','?')}）"
    return "", f"粘贴的票据无效：{msg}"


# ──────────────────────── 统一入口 ────────────────────────
def acquire(strategy: str = "auto", verbose: bool = True) -> tuple[str, str]:
    """
    strategy: auto | existing | cookie | manual
    返回 (票据, 说明)；失败时票据为空字符串
    """
    order = {
        "auto":   [("本地已存", existing), ("微信 Cookie", from_wechat_cookie)],
        "existing": [("本地已存", existing)],
        "cookie": [("微信 Cookie", from_wechat_cookie)],
        "manual": [("手工粘贴", manual_paste)],
    }.get(strategy, [])

    for label, fn in order:
        if verbose:
            print(f"  尝试：{label}…")
        try:
            tk, msg = fn()
        except Exception as exc:
            tk, msg = "", f"异常 {type(exc).__name__}: {exc}"
        if verbose:
            print(f"    → {msg}")
        if tk:
            save_token(tk)
            if verbose:
                print(f"    ✅ 已保存（会话 {token_hash(tk)[:8]}…）")
            return tk, msg
    return "", "所有方式都没能拿到有效票据"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="获取/校验登录票据")
    ap.add_argument("strategy", nargs="?", default="auto",
                    choices=["auto", "existing", "cookie", "manual", "verify"])
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args()

    if a.strategy == "verify":
        tk = load_token()
        ok, msg, info = verify(tk)
        print(f"票据哈希: {token_hash(tk) or '-'}")
        print(f"结果: {'✅ 有效' if ok else '❌ 无效'}  {msg}")
        if ok:
            print(f"  {info.get('studentName')} / {info.get('studentMobile')} / {info.get('studentHex')}")
        sys.exit(0 if ok else 1)

    tk, msg = acquire(a.strategy, verbose=not a.quiet)
    print(f"\n结果: {msg}")
    sys.exit(0 if tk else 1)
