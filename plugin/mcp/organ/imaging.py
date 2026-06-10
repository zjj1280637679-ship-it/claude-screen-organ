# -*- coding: utf-8 -*-
"""给 LLM 看图前的标准处理：缩到长边≤max_edge（默认1568，Claude 视觉饱和点），
PNG（UI 文字锐利）或 JPEG。返回 (处理后 Image, 落盘 bytes, meta)。"""
import io

from PIL import Image

DEFAULT_MAX_EDGE = 1568


def downscale(img: Image.Image, max_edge: int = DEFAULT_MAX_EDGE):
    w, h = img.size
    long_edge = max(w, h)
    if max_edge and long_edge > max_edge:
        s = max_edge / long_edge
        img = img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
        return img, round(s, 4)
    return img, 1.0


def encode(img: Image.Image, fmt: str = "png", quality: int = 85):
    buf = io.BytesIO()
    if fmt == "jpeg":
        img.convert("RGB").save(buf, "JPEG", quality=quality)
        media = "image/jpeg"
    else:
        img.save(buf, "PNG")
        media = "image/png"
    return buf.getvalue(), media


def prepare(img: Image.Image, max_edge: int = DEFAULT_MAX_EDGE,
            fmt: str = "png", quality: int = 85):
    orig = img.size
    scaled, ratio = downscale(img, max_edge)
    data, media = encode(scaled, fmt, quality)
    meta = {"orig_size": list(orig), "out_size": list(scaled.size),
            "scale": ratio, "format": fmt, "media_type": media,
            "bytes": len(data),
            "est_vision_tokens": round(scaled.size[0] * scaled.size[1] / 750)}
    return scaled, data, meta
