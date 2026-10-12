#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
lxt_api.py —— 乐校通 v3 服务器接口客户端（复现小程序的 HTTP 请求）

签名算法（已用抓包逐字节验证通过）：
    X-Sign = md5("1FF75E512" + F)
        POST 时 F = JSON.stringify(body)     ← 用两次抓包验证过，完全吻合
        GET  时 F = A(params)
    X-Sid  = "2021011300001"
    登录票据放在 x-token 与 Cookie 两个头里（值为 tokenInfo=... 整串）

域名：requstDomain2 = https://v3-prod-beta-app.lxt6.cn （由 getHostUrl 下发）
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import time
from functools import reduce
from pathlib import Path

import requests

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

V3_MD5 = "1FF75E512"
V3_SID = "2021011300001"
DOMAIN = "https://v3-prod-beta-app.lxt6.cn"
DOMAIN_FALLBACK = ["https://v3-app.lxt6.cn", "https://topservice.lxt6.cn"]

# 兜底票据：**留空**。真实票据由 config.load_token() 从 state/token_full.txt 读取。
# （千万不要把任何人的真实票据写进源码——这是开源项目最容易犯的泄露错误）
TOKEN_CAPTURED = ""


def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def make_sign(body_str: str) -> str:
    """X-Sign = md5(v3_md5 + F)，POST 时 F 就是 body 字符串"""
    return md5_hex(V3_MD5 + body_str)


def b3() -> str:
    return "".join(random.choice("0123456789abcdef") for _ in range(16))


def x_ghost() -> str:
    """
    ★ X-Ghost：设备时间指纹（不是随机数！）

    源码 util/util.js:
        q = function(){ var e=(new Date).getTime(); return E(J(e)) + J(e) + R(J(e)) }
        J: 毫秒时间戳 → 12 位大写 HEX（补零）
        E: 每字节累加和 % 256 → 2 位 HEX
        R: 每字节异或        → 2 位 HEX
    服务器会用错这个值就报 -45「请求已过期,请核对设备时间!」
    """
    ts = format(int(time.time() * 1000), "012X")        # 12 位大写 HEX
    bs = [int(ts[i:i + 2], 16) for i in range(0, 12, 2)]
    return f"{sum(bs) % 256:02X}{ts}{reduce(lambda a, b: a ^ b, bs):02X}"


class LxtApi:
    def __init__(self, token: str = TOKEN_CAPTURED, domain: str = DOMAIN, verbose: bool = True):
        self.domain = domain.rstrip("/")
        self.token = token.strip().rstrip(";")
        self.verbose = verbose
        self.http = requests.Session()
        # 不走系统代理（抓包时系统代理指向 mitmproxy，Python 不认它的根证书）
        self.http.trust_env = False
        self.http.proxies = {"http": None, "https": None}

    def log(self, m: str) -> None:
        if self.verbose:
            print(m, flush=True)

    def _headers(self, body_str: str) -> dict:
        return {
            "content-type": "application/json",
            "X-Sid": V3_SID,
            "X-Sign": make_sign(body_str),
            "X-B3-TraceId": b3(),
            "X-B3-SpanId": b3(),
            "X-Product-Ver": "4.1.15.13#?1.13.22",
            "X-Mobile-Info": "windows#?microsoft#?Windows 11 x64",
            "X-clientType": "4",
            "X-Ghost": x_ghost(),
            "x-token": self.token,
            "Cookie": self.token,
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 "
                           "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
                           "MiniProgramEnv/Windows WindowsWechat/WMPF XWEB/25715"),
        }

    def post(self, path: str, params: dict, timeout: float = 15.0) -> dict:
        body_str = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
        url = f"{self.domain}/{path.lstrip('/')}"
        self.log(f"POST {url}")
        self.log(f"  X-Sign={make_sign(body_str)}")
        self.log(f"  body={body_str[:400]}")
        r = self.http.post(url, data=body_str.encode("utf-8"),
                           headers=self._headers(body_str), timeout=timeout)
        self.log(f"  HTTP {r.status_code}")
        try:
            return r.json()
        except Exception:
            return {"_text": r.text[:800], "_status": r.status_code}

    def get(self, path: str, params: dict | None = None, timeout: float = 15.0) -> dict:
        # GET 的签名：把参数拼成 k=v&... 再签（顺序按传入顺序）
        params = params or {}
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{self.domain}/{path.lstrip('/')}"
        if qs:
            url += "?" + qs
        self.log(f"GET {url}")
        self.log(f"  X-Sign={make_sign(qs)}")
        r = self.http.get(url, headers=self._headers(qs), timeout=timeout)
        self.log(f"  HTTP {r.status_code}")
        try:
            return r.json()
        except Exception:
            return {"_text": r.text[:800], "_status": r.status_code}


# ─────────────────────── 业务接口 ───────────────────────
def handle_data(api: LxtApi, *, type_id: int, machine_id: str, device_ver: str,
                open_client_type: int, machine_data: str, machine_random: str,
                pos_system_time: str, temperature: int,
                machine_data_full: str, random_key: str,
                signature_file: str = "") -> dict:
    """复现 nbBle.js 里的 C()：调用 bath/handleData 换取开阀令牌"""
    info: dict = {
        "machineData": machine_data,
        "machineRandom": machine_random,
        "posSystemTime": pos_system_time,
        "temperature": temperature,
    }
    if signature_file:
        info["signatureFile"] = signature_file
    params = {
        "typeId": type_id,
        "channelWay": 1,
        "machineId": machine_id,
        "deviceVer": device_ver,
        "openClientType": open_client_type,
        "machineInfoJson": info,
        "machineDataFull": machine_data_full,
        "randomKey": random_key,
    }
    return api.post("bath/handleData", params)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="乐校通接口调试")
    ap.add_argument("action", choices=["userinfo", "coupon", "handle"], default="userinfo", nargs="?")
    ap.add_argument("--token", default=TOKEN_CAPTURED)
    args = ap.parse_args()

    api = LxtApi(token=args.token)
    if args.action == "userinfo":
        print(json.dumps(api.get("user/basic/getUserInfo"), ensure_ascii=False, indent=2))
    elif args.action == "coupon":
        print(json.dumps(api.get("promotion/student/getCouponAmount",
                                 {"schoolId": "80058", "investorId": "600000000000000047"}),
                         ensure_ascii=False, indent=2))
