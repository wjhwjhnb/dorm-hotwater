#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
captcha.py —— 图片验证码自动识别

三级策略，从快到慢自动降级：

  1. ppllocr  ——  纯 ONNX 的验证码识别库（YOLOv11 + onnxruntime），
                   内置模型，CPU 推理 <150ms
                       pip install ppllocr
  2. 本地 ONNX —— 若你自己训了模型，放进 models/*.onnx 会被自动加载
  3. 人工      —— 弹出图片让人输（永远可用的兜底）

★ 为什么不上打码平台：
     打码平台要钱、要联网、要把用户的验证码图片发给第三方。
     这个验证码只有 5 位数字、干扰很轻，本地模型足够。
     而且验证码可以无限重取 —— 识别率 50% 的话，平均 2 次就能过，
     配合重试循环，用户完全感觉不到。

★ 为什么不用 captcha_trainer 那类框架：
     要求标注几千张样本 + 长时间训练 + 数 GB 体积，不划算。
"""

from __future__ import annotations

import glob
import os
import pathlib
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
MODEL_DIR = HERE.parent / "models"

_engine = None
_engine_name = ""


def _try_local_onnx():
    files = sorted(glob.glob(str(MODEL_DIR / "*.onnx")))
    if not files:
        return None, ""
    try:
        from ppllocr import OCR
        return (lambda: OCR(model_path=files[0])), f"本地模型:{os.path.basename(files[0])}"
    except Exception:
        return None, ""


def _try_ppllocr():
    try:
        from ppllocr import OCR
        return OCR, "ppllocr"
    except Exception:
        return None, ""


def _try_ddddocr():
    """备用引擎：有些人机器上已经有 ddddocr"""
    try:
        import ddddocr  # type: ignore
        return (lambda: ddddocr.DdddOcr(show_ad=False)), "ddddocr"
    except Exception:
        return None, ""


def engine():
    """惰性初始化识别引擎，返回 (实例, 名称)"""
    global _engine, _engine_name
    if _engine is not None:
        return _engine, _engine_name
    for factory, name in (_try_local_onnx(), _try_ppllocr(), _try_ddddocr()):
        if factory is None:
            continue
        try:
            _engine, _engine_name = factory(), name
            return _engine, _engine_name
        except Exception:
            continue
    return None, ""


def recognize(img: bytes) -> str:
    """识别验证码；没有可用引擎时返回空串"""
    eng, _ = engine()
    if eng is None:
        return ""
    try:
        out = eng.classification(img)
    except Exception:
        return ""
    if isinstance(out, tuple):
        out = out[0]
    return "".join(str(out).split())


def normalize(text: str, length: int | None = None,
              charset: str = "0123456789abcdefghijklmnopqrstuvwxyz"
                       "ABCDEFGHIJKLMNOPQRSTUVWXYZ") -> str:
    """过滤非法字符；给了 length 就优先取等长的连续片段"""
    t = "".join(ch for ch in (text or "") if ch in charset)
    if length and len(t) != length:
        for i in range(0, max(0, len(t) - length) + 1):
            seg = t[i:i + length]
            if len(seg) == length:
                return seg
    return t


def ask_human(img: bytes, save_as: str = "") -> str:
    """兜底：存图片 + 用系统默认程序打开 + 等用户输入"""
    # ★ 文件名带时间戳：避免上一轮的看图程序锁住旧文件
    import time
    if save_as:
        path = pathlib.Path(save_as)
    else:
        path = HERE.parent / "state" / f"captcha_{time.strftime('%H%M%S')}_{os.getpid()}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(img)
    except Exception:
        pass
    print(f"\n  需要人工看验证码，图片在：\n    {path}")
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))                     # noqa: S606
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}" >/dev/null 2>&1')
    except Exception as exc:
        print(f"  （自动打开失败：{type(exc).__name__}，请手动打开）")
    return input("  请输入图片里的字符: ").strip()


def solve(img: bytes, length: int | None = None,
          allow_human: bool = True) -> tuple[str, str]:
    """返回 (验证码, 来源)；来源为 ppllocr / 本地模型:xx / ddddocr / human / ''"""
    eng, name = engine()
    if eng is not None:
        raw = recognize(img)
        text = normalize(raw, length)
        if text:
            return text, name
    if allow_human:
        return ask_human(img), "human"
    return "", ""


def describe() -> str:
    eng, name = engine()
    if eng is None:
        return ("未找到验证码识别引擎。\n"
                "  安装：python -m pip install ppllocr\n"
                f"  或把自有模型放到 {MODEL_DIR}\\*.onnx")
    return f"验证码识别引擎：{name}"


if __name__ == "__main__":
    print("=" * 84)
    print("验证码识别引擎自检")
    print("=" * 84)
    print("  " + describe().replace("\n", "\n  "))
    eng, name = engine()
    if eng is not None:
        # 用一张现成的验证码图片测试（state/ 下运行时产生的）
        cands = sorted((HERE.parent / "state").glob("captcha*.png"))
        if cands:
            p = cands[-1]
            got = recognize(p.read_bytes())
            print(f"\n  测试图片: {p.name}")
            print(f"  识别结果: 「{got}」")
        else:
            print("\n  （state/ 下没有验证码图片可测；跑一次登录就有了）")
