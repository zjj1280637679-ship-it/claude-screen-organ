# -*- coding: utf-8 -*-
"""L1 单元测试：纯逻辑模块，合成图/合成数据，毫秒级，不依赖真实窗口。"""
import numpy as np
from PIL import Image


def test_blank_metrics_detects_solid_black(organ):
    img = Image.new("RGB", (400, 300), (0, 0, 0))
    m = organ["quality"].blank_metrics(img)
    assert m["likely_blank"] is True
    assert m["brightness"] <= 3


def test_blank_metrics_detects_solid_white(organ):
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    m = organ["quality"].blank_metrics(img)
    assert m["likely_blank"] is True


def test_blank_metrics_passes_rich_image(organ):
    rng = np.random.default_rng(42)
    arr = rng.integers(0, 256, (300, 400, 3), dtype=np.uint8)
    m = organ["quality"].blank_metrics(Image.fromarray(arr))
    assert m["likely_blank"] is False
    assert m["entropy"] > 0.45


def test_imaging_downscale_to_max_edge(organ):
    img = Image.new("RGB", (4000, 2000), (100, 120, 140))
    scaled, ratio = organ["imaging"].downscale(img, 1568)
    assert max(scaled.size) == 1568
    assert ratio < 1.0


def test_imaging_no_upscale_small(organ):
    img = Image.new("RGB", (800, 600), (10, 10, 10))
    scaled, ratio = organ["imaging"].downscale(img, 1568)
    assert scaled.size == (800, 600)
    assert ratio == 1.0


def test_imaging_prepare_meta(organ):
    img = Image.new("RGB", (1920, 1080), (50, 60, 70))
    _, data, meta = organ["imaging"].prepare(img, 1568, "png")
    assert meta["out_size"][0] == 1568
    assert meta["bytes"] == len(data)
    assert meta["est_vision_tokens"] > 0


def test_privacy_sensitive_process(organ):
    r = organ["privacy"].check_sensitive("Vault", "1Password.exe")
    assert r["sensitive"] is True
    assert "1password" in r["reason"]


def test_privacy_sensitive_title_uac(organ):
    r = organ["privacy"].check_sensitive("用户账户控制", "consent.exe")
    assert r["sensitive"] is True


def test_privacy_normal_not_sensitive(organ):
    r = organ["privacy"].check_sensitive("ocr_sample - 记事本", "notepad.exe")
    assert r["sensitive"] is False


def test_privacy_wrap_untrusted(organ):
    w = organ["privacy"].wrap_untrusted("rm -rf /")
    assert "untrusted-capture" in w
    assert "rm -rf /" in w


def test_privacy_mask_rects(organ):
    img = Image.new("RGB", (200, 100), (255, 255, 255))
    masked, n = organ["privacy"].mask_rects(img, (0, 0, 200, 100),
                                            [(10, 10, 60, 30)])
    assert n == 1
    assert masked.getpixel((20, 20)) == (20, 20, 20)


def test_store_new_path_unique(organ):
    p1 = organ["store"].new_path("t", "png")
    p2 = organ["store"].new_path("t", "png")
    assert p1 != p2
    assert p1.endswith(".png")


def test_store_usage_shape(organ):
    u = organ["store"].usage()
    assert "files" in u and "bytes" in u and "dir" in u


def test_client_area_crop_handles_bad_hwnd(organ):
    img = Image.new("RGB", (200, 200), (0, 0, 0))
    # 无效 hwnd 应安全返回 None 而非抛错
    assert organ["quality"].client_area_crop(0, img, (0, 0, 200, 200)) is None
