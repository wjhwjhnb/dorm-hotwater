#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
lxt_login.py —— 直接登录乐校通（不经过微信）

三个接口（全部来自逆向源码 + 抓包验证）：

    ① 取图片验证码（不需要登录）
       GET  https://topservice.lxt6.cn/user/authentication/getCode
            ?account=<手机号>&topservice=true
       → { Code:0, Data:"<base64 PNG>" }

    ② 发送短信验证码（需要图片验证码的文字）
       GET  user/authentication/moblieMessageV2
            ?studentMobile=<手机号>&funFlag=<1|2|4>&platform=<..>
            &verificationCode=<图片码文字>&topservice=<true|false>

    ③ 登录
       POST user/login/userLoginV2WithEncrypt
            body = {"data": base64( JSON.stringify({
                        studentMobile, loginPassword, code, topservice:true
                    }) )}
            → 响应头 Set-Cookie: tokenInfo=…       ★ 票据到手

关键点（都是踩过的坑）：
  * 登录接口名带 **WithEncrypt** 后缀 —— 源码里对 I 列表内的接口会
    `e += "WithEncrypt"; S = JSON.stringify({data: jsBase64.encode(S)})`
  * 所谓 "Encrypt" **只是 base64**，不是加密（已用抓包逐字节验证）
  * GET 的签名 = md5(v3_md5 + **原始查询串**)
  * POST 的签名 = md5(v3_md5 + **请求体字符串**)
  * 内层 JSON 的键顺序必须是 studentMobile, loginPassword, code, topservice

安全说明：
  * 不保存密码。密码只在本进程内存里用于构造一次请求体。
  * 调用方如果打印日志，务必打码。本模块自身不打印密码。
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import sys
import time
from pathlib import Path

import requests

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lxt_api import V3_MD5, V3_SID, x_ghost

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOGIN_DOMAIN = "https://topservice.lxt6.cn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 "
      "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
      "MiniProgramEnv/Windows WindowsWechat/WMPF XWEB/25715")


def _b3() -> str:
    return "".join(random.choice("0123456789abcdef") for _ in range(16))


class LxtLogin:
    def __init__(self, domain: str = LOGIN_DOMAIN, verbose: bool = True):
        self.domain = domain.rstrip("/")
        self.verbose = verbose
        self.http = requests.Session()
        self.http.trust_env = False
        self.http.proxies = {"http": None, "https": None}
        self.cookie_token = ""      # 最近一次响应里拿到的 tokenInfo（若服务端下发）

    # ---------------- 日志 ----------------
    def log(self, m: str) -> None:
        if self.verbose:
            print(f"  {m}", flush=True)

    # ---------------- 请求头 ----------------
    def _headers(self, signed_text: str, with_token: bool = False) -> dict:
        h = {
            "content-type": "application/json",
            "X-Sid": V3_SID,
            "X-Sign": hashlib.md5((V3_MD5 + signed_text).encode("utf-8")).hexdigest(),
            "X-B3-TraceId": _b3(),
            "X-B3-SpanId": _b3(),
            "X-Product-Ver": "4.1.15.13#?1.13.22",
            "X-Mobile-Info": "windows#?microsoft#?Windows 11 x64",
            "X-clientType": "4",
            "X-Ghost": x_ghost(),
            "Accept": "*/*",
            "Referer": "",
            "User-Agent": UA,
        }
        if with_token and self.cookie_token:
            h["x-token"] = self.cookie_token
            h["Cookie"] = self.cookie_token
        return h

    # ---------------- 会话持久化 ----------------
    # ★ 关键：图片验证码绑定在会话（__jsluid_s cookie）上，
    #   所以"取验证码"和"发短信"必须用同一个会话。
    #   分进程两步走时，把 cookie 存盘再读回来。
    def save_session(self, path: Path) -> None:
        import requests.utils
        path.parent.mkdir(parents=True, exist_ok=True)
        cj = requests.utils.dict_from_cookiejar(self.http.cookies)
        path.write_text(json.dumps(cj, ensure_ascii=False), encoding="utf-8")
        self.log(f"     会话已保存（{len(cj)} 个 cookie）: {path.name}")

    def load_session(self, path: Path) -> bool:
        import requests.utils
        if not path.exists():
            return False
        try:
            cj = requests.utils.cookiejar_from_dict(
                json.loads(path.read_text(encoding="utf-8")))
            self.http.cookies.update(cj)
            self.log(f"     已载入会话（{len(cj)} 个 cookie）: {path.name}")
            return True
        except Exception as exc:
            self.log(f"     会话载入失败: {exc}")
            return False

    # ---------------- ① 图片验证码 ----------------
    def get_code(self, phone: str) -> tuple[bool, str, str]:
        """返回 (成功?, base64图片, 说明)"""
        qs = f"account={phone}&topservice=true"
        url = f"{self.domain}/user/authentication/getCode?{qs}"
        self.log(f"GET  {url}")
        r = self.http.get(url, headers=self._headers(qs), timeout=20)
        self.log(f"     HTTP {r.status_code}")
        try:
            j = r.json()
        except Exception:
            return False, "", f"响应不是 JSON: {r.text[:120]}"
        if j.get("Code") != 0:
            return False, "", f"Code={j.get('Code')} {j.get('Message')}"
        data = j.get("Data") or ""
        if data.startswith("data:image"):
            data = data.split(",", 1)[-1]
        return True, data, "取到验证码图片"

    # ---------------- ② 发送短信 ----------------
    def send_sms(self, phone: str, captcha_text: str, fun_flag: int = 1,
                 platform: str = "1") -> tuple[bool, str]:
        qs = (f"studentMobile={phone}&funFlag={fun_flag}&platform={platform}"
              f"&verificationCode={captcha_text}&topservice="
              f"{'true' if fun_flag in (1, 2) else 'false'}")
        url = f"{self.domain}/user/authentication/moblieMessageV2?{qs}"
        self.log(f"GET  {url}")
        r = self.http.get(url, headers=self._headers(qs), timeout=20)
        self.log(f"     HTTP {r.status_code}")
        try:
            j = r.json()
        except Exception:
            return False, f"响应不是 JSON: {r.text[:120]}"
        if j.get("Code") == 0:
            return True, "短信已发送，请查看手机"
        return False, f"Code={j.get('Code')} {j.get('Message')}"

    # ---------------- ③ 登录 ----------------
    def login(self, phone: str, password: str, sms_code: str) -> tuple[bool, str, dict]:
        """返回 (成功?, 说明, 附加信息)"""
        # 内层 JSON：键顺序必须与小程序一致
        inner = json.dumps({
            "studentMobile": phone,
            "loginPassword": password,
            "code": sms_code,
            "topservice": True,
        }, separators=(",", ":"), ensure_ascii=False)

        encoded = base64.b64encode(inner.encode("utf-8")).decode()
        body = json.dumps({"data": encoded}, separators=(",", ":"))

        url = f"{self.domain}/user/login/userLoginV2WithEncrypt"
        self.log(f"POST {url}")
        self.log(f"     body 长度 {len(body)}（内层已 base64，不含明文密码）")

        r = self.http.post(url, data=body.encode("utf-8"),
                           headers=self._headers(body), timeout=25)
        self.log(f"     HTTP {r.status_code}")

        # 从响应头收集 tokenInfo（可能在 Set-Cookie 或 x-token）
        self._harvest_token(r)

        try:
            j = r.json()
        except Exception:
            return False, f"响应不是 JSON: {r.text[:160]}", {}
        if j.get("Code") != 0:
            return False, f"Code={j.get('Code')} {j.get('Message')}", j
        d = j.get("Data") or {}
        info = {
            "studentId": d.get("studentId"),
            "url": d.get("url"),
            "state": d.get("state"),
            "token": self.cookie_token,
        }
        if not self.cookie_token:
            return False, "登录成功但响应里没有拿到 tokenInfo（服务端未下发 cookie）", info
        return True, "登录成功", info

    def _harvest_token(self, resp: requests.Response) -> None:
        """
        从登录响应里捞 tokenInfo。可能出现在三处，都要试：
          ① 响应头 x-token
          ② Set-Cookie（可能有多个）
          ③ requests 解析出来的 cookie jar
        """
        raw_sets: list[str] = []
        try:
            raw_sets = list(resp.raw.headers.getlist("Set-Cookie"))
        except Exception:
            sc = resp.headers.get("Set-Cookie")
            if sc:
                raw_sets = [sc]

        tok_val = ""
        jsl_val = ""
        # ② Set-Cookie
        for sc in raw_sets:
            for piece in sc.split(";"):
                p = piece.strip()
                if p.startswith("tokenInfo="):
                    tok_val = p[len("tokenInfo="):]
                elif "__jsluid" in p and "=" in p:
                    jsl_val = p.split("=", 1)[1]
        # ③ cookie jar
        try:
            for c in resp.cookies:
                if c.name == "tokenInfo" and not tok_val:
                    tok_val = c.value
                elif "jsluid" in c.name and not jsl_val:
                    jsl_val = c.value
        except Exception:
            pass
        # ① x-token 头
        if not tok_val:
            x = resp.headers.get("x-token") or ""
            if "tokenInfo" in x:
                tok_val = x.split("tokenInfo=", 1)[1].split(";")[0].strip()

        # 组装成和 App 一样的整串（API 需要带上 __jsluid_s）
        parts = []
        if tok_val:
            parts.append(tok_val if tok_val.startswith("tokenInfo=") else f"tokenInfo={tok_val}")
        if jsl_val:
            parts.append(f"__jsluid_s={jsl_val}")
        if parts:
            self.cookie_token = "; ".join(parts)

        if self.cookie_token:
            h = ""
            if "#?" in self.cookie_token:
                h = self.cookie_token.split("#?")[1].split(";")[0]
            self.log(f"     ✅ 拿到票据（会话 {h[:8]}…）")
        else:
            self.log("     ⚠ 响应里没找到 tokenInfo")


# ──────────────────────── 命令行 ────────────────────────
def _interactive(domain: str, phone: str = "", fun_flag: int = 1) -> int:
    """
    交互式登录。密码只在本进程内存里，不写盘、不打印。
    图片验证码：手动抄；若装了 ddddocr 则自动识别。
    """
    lg = LxtLogin(domain=domain, verbose=True)

    if not phone:
        phone = input("  手机号: ").strip()
    if len(phone) != 11:
        print("  ✗ 手机号位数不对")
        return 1

    ok, img, msg = lg.get_code(phone)
    if not ok:
        print(f"  ✗ 取验证码失败：{msg}")
        return 1

    # 保存图片
    outdir = _SRC.parent / "state"
    outdir.mkdir(parents=True, exist_ok=True)
    png = outdir / "captcha.png"
    png.write_bytes(base64.b64decode(img))

    captcha = ""
    # 优先用本地 OCR（若装了 ddddocr）
    try:
        import ddddocr  # type: ignore
        ocr = ddddocr.DdddOcr(show_ad=False)
        captcha = ocr.classification(png.read_bytes())
        print(f"\n  🤖 本地 OCR 识别：{captcha}")
    except ImportError:
        pass

    if not captcha:
        # ★ 自动弹出图片，用户照抄即可（和小程序里一样的体验）
        print(f"\n  即将打开验证码图片，请照着上面的字符输入：\n    {png}")
        try:
            import os
            os.startfile(str(png))
        except Exception as exc:
            print(f"  （自动打开失败：{exc}，请手动打开上面这个文件）")

    shown = captcha or input("  请输入图片里的字符（直接回车可重取）: ").strip()
    if not shown:
        print("  ✗ 没有输入验证码")
        return 1

    # ★ 取图到发短信必须在同一进程内尽快完成，否则验证码会过期
    ok, msg = lg.send_sms(phone, shown, fun_flag)
    print(f"  {'✅' if ok else '✗'} {msg}")
    if not ok:
        print("  （验证码有效期很短，失败通常是输入超时；重新运行即可）")
        return 1

    import getpass
    pw = getpass.getpass("  登录密码（不回显、不保存）: ")
    code = input("  短信验证码: ").strip()

    ok, msg, info = lg.login(phone, pw, code)
    print(f"\n  {'🎉' if ok else '✗'} {msg}")
    if ok:
        from config import save_token, token_hash
        save_token(info["token"])
        print(f"  票据已保存，会话 {token_hash(info['token'])[:8]}…")
        return 0
    return 1


# ──────────────────────── 对外便捷入口 ────────────────────────
def interactive_login(phone: str = "", fun_flag: int = 1,
                      domain: str = LOGIN_DOMAIN) -> tuple[bool, str]:
    """
    供配置向导调用的登录入口。
    返回 (成功?, 说明)。成功时票据已保存到 state/。
    """
    rc = _interactive(domain, phone, fun_flag)
    if rc == 0:
        return True, "登录成功，票据已保存"
    return False, "登录未完成"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="直接登录乐校通")
    ap.add_argument("--phone", default="")
    ap.add_argument("--fun-flag", type=int, default=1)
    ap.add_argument("--domain", default=LOGIN_DOMAIN)
    a = ap.parse_args()
    try:
        sys.exit(_interactive(a.domain, a.phone, a.fun_flag))
    except KeyboardInterrupt:
        print("\n  已取消")
        sys.exit(130)
