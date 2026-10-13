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
import os
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
    # ★ 每次用【不同的文件名】：上一轮的看图程序会独占锁住旧文件，
    #   同名写入会抛 OSError: [Errno 22] Invalid argument，把整个登录流程打断。
    #   （这是实测踩到的坑）
    outdir = _SRC.parent / "state"
    outdir.mkdir(parents=True, exist_ok=True)
    png = outdir / f"captcha_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.png"
    try:
        png.write_bytes(base64.b64decode(img))
    except Exception as exc:
        print(f"  ⚠ 验证码图片保存失败（{type(exc).__name__}），改为纯手动输入")
        png = None

    captcha = ""
    # 优先用本地 OCR（若装了 ddddocr）
    # ★ 不能只 catch ImportError：ddddocr 依赖 onnxruntime，
    #   缺 DLL 时抛的是 OSError，之前正是它把登录打断了
    if png is not None:
        try:
            import ddddocr  # type: ignore
            ocr = ddddocr.DdddOcr(show_ad=False)
            captcha = ocr.classification(png.read_bytes())
            print(f"\n  🤖 本地 OCR 识别：{captcha}")
        except Exception as exc:
            print(f"\n  （本地 OCR 不可用：{type(exc).__name__}，改为手动输入）")

    if not captcha:
        # ★ 自动弹出图片，用户照抄即可（和小程序里一样的体验）
        if png is not None:
            print(f"\n  即将打开验证码图片，请照着上面的字符输入：\n    {png}")
            try:
                os.startfile(str(png))
            except Exception as exc:
                print(f"  （自动打开失败：{type(exc).__name__}，请手动打开上面这个文件）")
        else:
            print("\n  验证码图片没能保存，请另想办法看图（比如截图发给自己）")

    shown = captcha or input("  请输入图片里的字符（直接回车可重取）: ").strip()
    if not shown:
        print("  ✗ 没有输入验证码")
        return 1

    # ★ 取图到发短信必须在同一进程内尽快完成，否则验证码会过期
    ok, msg = lg.send_sms(phone, shown, fun_flag)
    print(f"  {'✅' if ok else '✗'} {msg}")
    if not ok:
        print("  （验证码有效期很短，大约 1 分钟；看到图片就快点输）")
        return 1

    import getpass
    saved_phone, pw = "", ""
    try:
        import credentials
        saved_phone, pw = credentials.load()
    except Exception:
        pass

    if saved_phone == phone and pw:
        print(f"  （使用本机已加密保存的密码，长度 {len(pw)} 位）")
    else:
        pw = getpass.getpass("  登录密码（不回显）: ")
        # 只打印长度，不打印内容——万一输入被终端搞乱了，这里能立刻看出来
        print(f"  （已读取密码，长度 {len(pw)} 位）")
    if len(pw) < 6:
        print("  ✗ 密码太短（学校要求 6-18 位），可能输入没被读到，请重试")
        return 1
    code = input("  短信验证码: ").strip()
    print(f"  （已读取短信码，长度 {len(code)} 位）")
    if not code:
        print("  ✗ 短信验证码为空")
        return 1

    ok, msg, info = lg.login(phone, pw, code)
    print(f"\n  {'🎉' if ok else '✗'} {msg}")
    if ok:
        from config import save_token, token_hash
        save_token(info["token"])
        print(f"  票据已保存，会话 {token_hash(info['token'])[:8]}…")

        # 问一下要不要把账号密码加密存本机 → 以后票据过期能自动重登
        if saved_phone != phone:
            print("""
  要不要把账号密码存在本机（Windows DPAPI 加密，只有你这台电脑+这个账户能解开）？
  好处：以后票据过期时能自动重新登录，你只需在极少数情况下输一次短信码。
  不想存就选 n；随时可以用 `python src/credentials.py --clear` 删掉。""")
            ans = input("  保存吗？(y/N): ").strip().lower()
            if ans.startswith("y"):
                try:
                    import credentials
                    credentials.save(phone, pw)
                    print(f"  ✅ {credentials.describe()}")
                except Exception as exc:
                    print(f"  ⚠ 保存失败：{type(exc).__name__}: {exc}")
        return 0

    # 失败时给人话解释
    if "-45" in msg and "验证码" in msg:
        print("""
  ── 这是什么意思 ──
  服务器说【短信验证码】不对。常见原因：
    · 输入太慢，短信码已过期（有效期通常 5 分钟）→ 重来一次，收到就立刻输
    · 手机上收到了好几条短信，用了旧的那条 → 用【最新】那条
    · 看错数字 → 重来一次
  注意：这一步失败跟你的密码无关，也跟图片验证码无关。
""")
    elif "密码" in msg:
        print("  ── 密码不对，确认一下大小写 ──")
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
