#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
lxt_login.py —— 登录乐校通（手机号 + 密码 + 图片验证码）

★★★ 重要更正 ★★★
    以前我把 userLoginV2 的 code 参数当成【短信验证码】，还去调 moblieMessageV2。
    那是错的 —— moblieMessageV2 属于【忘记密码/重置密码】流程
    （对应 user/modify/loginPasswordByAuthenticationMessage）。

    真实登录流程只有两步，全程没有短信：

        GET  user/authentication/getCode?account=<手机号>&topservice=true
             -> {"Code":0,"Data":"data:image/jpeg;base64,..."}     图片验证码

        POST user/login/userLoginV2
             {"studentMobile":"..","loginPassword":"..","code":"<图片验证码>","topservice":true}
             -> {"Code":0,"Data":{"studentId":"..","url":"https://v3-app.lxt6.cn","state":0}}
                响应头 Set-Cookie: tokenInfo=<studentId>#?<32位hex>

    已实测验证：用【假密码 + 真图片码】请求，服务器回
        {"Code":-45,"Message":"登录账号或密码不正确!,还有2次机会!"}
    说明验证码先被接受了 —— 因此 code 就是图片码，登录不需要短信。

    ⚠ 服务器有尝试次数限制（错误密码剩 2 次机会），所以密码必须一次输对。

签名规则（还原自 util/util.js 的 fnJson）：
    X-Sign = md5("1FF75E512" + F)
        POST -> F = JSON.stringify(body)
        GET  -> F = "k=v&k=v"（不含 "?"，按字面量顺序）
★ 坑：requests 会把 Python 的 True 变成 "True"，而签名里要的是 "true"，
      两边不一致服务端返回 -301 数据安全校验不通过。必须显式转字符串。
"""

from __future__ import annotations

import base64
import functools
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings()

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import captcha as captcha_mod  # noqa: E402

TOPSERVICE = "https://topservice.lxt6.cn"
V3_SID = "2021011300001"
V3_MD5 = "1FF75E512"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) MicroMessenger/7.0.20.1781 MiniProgramEnv/Windows")

TIMEOUT = 25
CAPTCHA_LEN = 5

# 小程序里被当作「验证码类错误、可以重试」的返回码
CAPTCHA_ERR = {-44, -45, -50, -52, -521}

# 图片验证码过期/错误的提示词（用于决定要不要重试）
CAPTCHA_HINT = ("验证码", "校验", "过期")


# ──────────────────────── 签名 ────────────────────────
def _jsval(v) -> str:
    """按 JS 的 String() 语义转字符串（True -> "true"）"""
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    return str(v)


def serialize(data: dict) -> str:
    """{k:v,...} -> "k=v&k=v"（保持字面量顺序，用于 GET 签名）"""
    if not isinstance(data, dict) or not data:
        return ""
    return "&".join(f"{k}={_jsval(v)}" for k, v in data.items())


def query_params(data: dict) -> dict:
    """GET 查询参数：必须显式转成 JS 语义字符串，否则签名对不上（-301）"""
    if not isinstance(data, dict):
        return {}
    return {k: _jsval(v) for k, v in data.items()}


def x_ghost(ts_ms: int | None = None) -> str:
    """
    X-Ghost = 累加校验(2位) + 毫秒时间戳HEX(12位大写) + 异或校验(2位)

    ★ 它不是随机数！填错服务端返回 -45 请求已过期,请核对设备时间!
    """
    ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
    h = format(ts, "x").upper()
    if len(h) != 12:
        h = "0" + h
    b = bytes.fromhex(h)
    return ("%02X" % (sum(b) % 256)) + h + \
           ("%02X" % functools.reduce(lambda a, c: a ^ c, b, 0))


def x_sign(signed: str = "") -> str:
    """X-Sign = md5(v3_md5 + F)，小写"""
    return hashlib.md5((V3_MD5 + signed).encode()).hexdigest()


def headers(signed: str, token: str = "", host: str = "") -> dict:
    h = {
        "content-type": "application/json",
        "X-Sid": V3_SID,
        "X-Sign": x_sign(signed),
        "X-B3-TraceId": os.urandom(8).hex(),
        "X-B3-SpanId": os.urandom(8).hex(),
        "X-Product-Ver": "3.11.3#?release",
        "X-Mobile-Info": "windows#?PC#?Windows 10 x64",
        "X-Ghost": x_ghost(),
        "User-Agent": UA,
    }
    if host:
        h["Host"] = host
    if token:
        # 服务端把 x-token 当纯值回显；Cookie 用 tokenInfo=<值>
        h["x-token"] = token
        h["Cookie"] = f"tokenInfo={token}"
    return h


def new_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False                     # 不走系统代理
    s.proxies = {"http": None, "https": None}
    return s


# ──────────────────────── 登录 ────────────────────────
class LoginError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(f"Code={code} {message}")
        self.code = code
        self.message = message


def fetch_captcha(mobile: str, session: requests.Session | None = None) -> bytes:
    """取图片验证码，返回图片字节"""
    s = session or new_session()
    d = {"account": mobile, "topservice": True}
    r = s.get(f"{TOPSERVICE}/user/authentication/getCode",
              params=query_params(d), headers=headers(serialize(d)),
              timeout=TIMEOUT, verify=False)
    j = r.json()
    if j.get("Code") != 0:
        raise LoginError(j.get("Code"), j.get("Message"))
    return base64.b64decode(j["Data"].split(",")[-1])


def login_once(mobile: str, password: str, code: str,
               session: requests.Session | None = None) -> dict:
    """
    提交一次登录。成功返回 {studentId, url, state, token}
    token 来自 Set-Cookie 的 tokenInfo（不在返回体里）。
    """
    s = session or new_session()
    data = {"studentMobile": mobile, "loginPassword": password,
            "code": code, "topservice": True}
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    r = s.post(f"{TOPSERVICE}/user/login/userLoginV2",
               data=body.encode(), headers=headers(body),
               timeout=TIMEOUT, verify=False)
    try:
        j = r.json()
    except ValueError:
        raise LoginError(None, f"HTTP {r.status_code} 非 JSON: {r.text[:200]}")

    if j.get("Code") != 0:
        raise LoginError(j.get("Code"), j.get("Message"))

    # token 在 Set-Cookie 里
    token = ""
    for k, v in r.cookies.items():
        if k == "tokenInfo":
            token = v
    if not token:
        # 也看看响应头里的 x-token
        xt = r.headers.get("x-token") or ""
        if "tokenInfo" in xt:
            token = xt.split("tokenInfo=", 1)[1].split(";")[0].strip()
    d = dict(j.get("Data") or {})
    d["token"] = token
    return d


def auto_login(mobile: str, password: str, max_attempts: int = 8,
               verbose: bool = True, allow_human: bool = True) -> dict:
    """
    全自动登录：取图 → OCR → 登录；验证码不对就换一张重试。

    实测 ppllocr 一次就能认对，所以通常第一次就成功。
    ⚠ 密码必须正确 —— 服务器限制尝试次数（错误密码剩 2 次机会）。
    """
    def log(*a):
        if verbose:
            print(*a, flush=True)

    s = new_session()
    last_err = None
    for i in range(1, max_attempts + 1):
        try:
            img = fetch_captcha(mobile, s)
        except LoginError as e:
            log(f"  第 {i} 次：取验证码失败 Code={e.code} {e.message}")
            last_err = e
            time.sleep(1)
            continue

        text, src = captcha_mod.solve(img, length=CAPTCHA_LEN,
                                      allow_human=allow_human)
        if not text:
            log(f"  第 {i} 次：没识别出验证码，换一张")
            continue
        log(f"  第 {i} 次：验证码「{text}」（{src}）")

        try:
            d = login_once(mobile, password, text, s)
        except LoginError as e:
            last_err = e
            msg = e.message or ""
            if any(k in msg for k in CAPTCHA_HINT):
                log(f"          验证码没过，换一张重试")
                continue
            # 不是验证码问题 → 密码错了 / 账号被锁，直接抛
            raise
        if not d.get("token"):
            log("          ⚠ 登录返回 0 但没拿到 tokenInfo，重试")
            continue
        log(f"  ✅ 登录成功（第 {i} 次尝试）")
        return d

    raise LoginError(getattr(last_err, "code", None),
                     f"连续 {max_attempts} 次未通过。最后一次：{last_err}")


def save_session(d: dict) -> Path:
    """把 token/域名/学号 存到 state/session.json"""
    out = _SRC.parent / "state" / "session.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    obj = {"token": d.get("token", ""),
           "host": (d.get("url") or "https://v3-app.lxt6.cn").rstrip("/"),
           "studentId": d.get("studentId", ""),
           "updatedAt": time.time()}
    out.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_session() -> dict:
    p = _SRC.parent / "state" / "session.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ──────────────────────── 命令行 ────────────────────────
def _cli(mobile: str = "", password: str = "") -> int:
    print("=" * 84)
    print("登录乐校通（手机号 + 密码 + 图片验证码，全程无短信）")
    print("=" * 84)
    print("  " + captcha_mod.describe().replace("\n", "\n  "))
    print()

    if not mobile:
        mobile = input("  手机号: ").strip()
    if len(mobile) != 11 or not mobile.isdigit():
        print("  ✗ 手机号格式不对")
        return 1

    if not password:
        import getpass
        password = getpass.getpass("  密码（不回显）: ")
    print(f"  （密码长度 {len(password)} 位）")
    if len(password) < 6:
        print("  ✗ 密码太短，可能没读到输入")
        return 1

    print("\n  正在自动登录（验证码由本地模型识别）…")
    try:
        d = auto_login(mobile, password)
    except LoginError as e:
        print(f"\n  ✗ 登录失败：{e}")
        msg = e.message or ""
        if "密码" in msg:
            print("\n  ── 密码不对。注意服务器限制尝试次数，别反复试 ──")
        return 1

    p = save_session(d)
    tok = d.get("token", "")
    h = tok.split("#?")[-1][:8] if "#?" in tok else tok[:8]
    print(f"\n  🎉 登录成功")
    print(f"     学号   {d.get('studentId', '')}")
    print(f"     会话   {h}…")
    print(f"     业务域名 {d.get('url', '')}")
    print(f"     已保存 {p}")

    # 顺便加密存一下账号密码，方便以后自动重登
    try:
        import credentials
        if credentials.load()[0] != mobile:
            ans = input("\n  把账号密码加密存在本机吗？（以后自动重登用）(y/N): ").strip().lower()
            if ans.startswith("y"):
                credentials.save(mobile, password)
                print(f"  ✅ {credentials.describe()}")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="登录乐校通")
    ap.add_argument("--phone", default="")
    ap.add_argument("--password", default="", help="不填则交互输入（不回显）")
    a = ap.parse_args()
    sys.exit(_cli(a.phone, a.password))
