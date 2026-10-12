#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
config.py —— 统一的配置读写

配置文件位置：项目根目录的 config.json（**不要提交到 git**）
登录票据单独放在 state/token_full.txt（同样不提交）

设计原则：
    * 任何跟"个人/某台设备"绑定的东西都不写死在代码里
    * 首次运行为空 → 提示用户先跑配置向导
    * 缺字段给合理默认值，不因为多一个少一个键就崩
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
TOKEN_PATH = ROOT / "state" / "token_full.txt"


# ──────────────────────────── 数据结构 ────────────────────────────
@dataclass
class MachineConfig:
    """用户绑定的那台水控器"""
    address: str = ""          # 蓝牙地址，如 AA:BB:CC:DD:EE:FF
    name: str = ""             # 蓝牙广播名，如 BT19S-T
    sn: str = ""               # 设备 SN（F0 命令返回）
    mac: str = ""              # 设备 MAC（无分隔符大写）
    device_ver: str = ""       # 形如 01,07
    roominfo: str = ""         # 服务器返回的房间，如 某区某栋某层某室
    machine_name: str = ""     # 机号，如 402
    student_hex: str = ""      # 你的身份标识，如 00112233

    @property
    def id8(self) -> str:
        """发帧用的 8 字节 ID（16 位十六进制）"""
        return (self.student_hex.upper().ljust(8, "0") + "00000000")[:16]

    def is_ready(self) -> bool:
        return bool(self.address and self.student_hex)


@dataclass
class AppConfig:
    machine: MachineConfig = field(default_factory=MachineConfig)
    type_id: int = 1                 # 1=洗澡 6=饮水 3=洗衣 8=吹风（见 README）
    confirm_before_open: bool = True  # 开阀前是否弹确认框
    school_id: str = ""              # 学校 id，如 80058
    investor_id: str = ""            # 投资方 id
    domain: str = "https://v3-prod-beta-app.lxt6.cn"

    # ---------- 读写 ----------
    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        p = path or CONFIG_PATH
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[!] 读 config.json 失败：{exc}（按空配置处理）")
            return cls()
        m = cls()
        md = raw.get("machine") or {}
        for k, v in md.items():
            if hasattr(m.machine, k):
                setattr(m.machine, k, v if v is not None else "")
        for k, v in (raw.get("options") or {}).items():
            if hasattr(m, k):
                setattr(m, k, v)
        for k in ("school_id", "investor_id", "domain", "type_id"):
            if k in raw and raw[k] not in (None, ""):
                setattr(m, k, raw[k])
        return m

    def save(self, path: Path | None = None) -> Path:
        p = path or CONFIG_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "machine": asdict(self.machine),
            "options": {
                "type_id": self.type_id,
                "confirm_before_open": self.confirm_before_open,
            },
            "school_id": self.school_id,
            "investor_id": self.investor_id,
            "domain": self.domain,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return p


# ──────────────────────────── 票据 ────────────────────────────
def load_token() -> str:
    """读登录票据（整串 cookie，含 __jsluid_s）"""
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    return ""


def save_token(token: str) -> Path:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(token.strip(), encoding="utf-8")
    return TOKEN_PATH


def token_hash(token: str) -> str:
    """取出会话哈希，便于判断票据有没有换新"""
    if "#?" in token:
        return token.split("#?")[1].split(";")[0]
    return ""


# ──────────────────────────── 自检 ────────────────────────────
if __name__ == "__main__":
    cfg = AppConfig.load()
    print(f"配置文件: {CONFIG_PATH}  ({'存在' if CONFIG_PATH.exists() else '不存在'})")
    print(json.dumps({
        "machine": asdict(cfg.machine),
        "options": {"type_id": cfg.type_id, "confirm_before_open": cfg.confirm_before_open},
        "id8": cfg.machine.id8,
        "ready": cfg.machine.is_ready(),
    }, ensure_ascii=False, indent=2))
    tk = load_token()
    print(f"票据: {'有' if tk else '无'}  哈希={token_hash(tk) or '-'}")
