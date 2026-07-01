# -*- coding: utf-8 -*-
"""video_synth —— 生成"类似视频"的工作流(video_evidence 的**综合对偶**)。

理解的硬证据 = 能反过来生成:把参考视频**因子化**成
  【不可替换的生成结构 TEMPLATE】(换了就不再"相似")+【可替换的槽位 SLOTS】(换了仍"相似"),
换掉槽位(**人物必然可替换**)仍保持结构 → 生成"同结构不同角色"的视频 = 证明抓住了生成结构。

**易错点 = 没分清可替换/不可替换**。本模块用数据结构把这条分离**显式冻结**:
render 时 TEMPLATE 原封不动,只有 SLOTS 被覆盖 → "相似"由不可替换元素定义,人物是头号槽。

分层:感知/生成调 doubao+seedance(经额度网关)⊥ 本模块只做**因子化 + 槽位管理 + prompt 组装**(薄)。
用法: python video_synth.py <参考webvideo目录> [--swap-character "新角色"] [--swap-setting ..] [--i2v] [--no-generate]
"""
import argparse
import json
import os
import time
import urllib.request

import webvideo
from video_evidence import vision, _img, _chat  # 复用感知原语(调 doubao)

GATEWAY = os.environ.get("ARK_GATEWAY", "http://127.0.0.1:8788")

FACTOR_HINT = """把这段视频**因子化**成两部分,只输出 JSON(不要多余文字):
{
 "non_replaceable": {   // 不可替换的"生成结构"——换掉它就不再是"相似视频"
   "genre":"", "visual_style":"", "lighting":"", "camera":"",
   "shot_structure":[""], "pacing_audio_sync":"", "aspect":"", "duration_s":0 },
 "slots": {             // 可替换的"槽位"——换掉仍是"相似视频"
   "character":"", "setting":"", "audio":"" }
}
铁律:**人物/场景/音频=可替换槽位;风格/打光/镜头结构/运镜/节奏=不可替换结构**。character 写清主体外观(必然可替换)。"""


def extract_template(work_dir):
    """用视觉模型把参考视频因子化成 {non_replaceable, slots}。'理解'落成的数据。"""
    manifest = json.load(open(os.path.join(work_dir, "manifest.json"), encoding="utf-8"))
    frames = [f["path"] for f in manifest.get("frames", [])]
    txt, _ = vision(frames, FACTOR_HINT, max_tokens=900)
    s = txt[txt.find("{"): txt.rfind("}") + 1]
    tpl = json.loads(s)
    tpl.setdefault("non_replaceable", {})
    tpl.setdefault("slots", {})
    return tpl


def render_prompt(tpl, slot_overrides=None):
    """TEMPLATE 冻结 + SLOTS 覆盖 → Seedance 生成 prompt。**只有 slots 变,结构不变**。"""
    slots = dict(tpl.get("slots", {}))
    slots.update(slot_overrides or {})
    nr = tpl.get("non_replaceable", {})
    ss = nr.get("shot_structure", [])
    shots = "；".join(ss) if isinstance(ss, list) else str(ss)
    prompt = (f"{nr.get('visual_style', '')},{nr.get('lighting', '')}。"
              f"主体:{slots.get('character', '')}。场景:{slots.get('setting', '')}。"
              f"镜头结构:{shots}。运镜:{nr.get('camera', '')}。节奏:{nr.get('pacing_audio_sync', '')}。")
    import re
    m = re.search(r"(\d+)\s*:\s*(\d+)", str(nr.get("aspect") or ""))
    ratio = f"{m.group(1)}:{m.group(2)}" if m else "16:9"
    try:
        dur = int(float(nr.get("duration_s") or 5))
    except Exception:
        dur = 5
    dur = max(3, min(10, dur))
    return prompt.strip(), ratio, dur


def _post(path, body, timeout=180):
    req = urllib.request.Request(GATEWAY + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


def _get(path, timeout=30):
    return json.loads(urllib.request.urlopen(GATEWAY + path, timeout=timeout).read().decode())


def gen_image(prompt, size="1024x1024"):
    """Seedream 出角色图(可替换资产实体化,用于 I2V)。返回 url。"""
    j = _post("/v1/images/generations", {"model": "auto-image", "prompt": prompt, "size": size, "n": 1})
    return j["data"][0]["url"]


def gen_video(prompt, ratio="16:9", dur=5, image_url=None, wait_s=360):
    """Seedance 生成视频。T2V(仅 prompt)/ I2V(带 image_url)。返回 video_url。永不静默:失败带原因抛。"""
    text = f"{prompt} --ratio {ratio} --dur {dur}"
    content = [{"type": "text", "text": text}]
    if image_url:
        content.append({"type": "image_url", "image_url": {"url": image_url}})
    task = _post("/v1/contents/generations/tasks", {"model": "auto-video", "content": content})
    tid = task.get("id") or task.get("task_id")
    if not tid:
        raise RuntimeError(f"建任务失败:{task}")
    deadline = time.time() + wait_s
    last = None
    while time.time() < deadline:
        t = _get(f"/v1/contents/generations/tasks/{tid}")
        last = t
        st = t.get("status")
        if st == "succeeded":
            c = t.get("content") or {}
            url = c.get("video_url") or (t.get("data") or {}).get("video_url")
            if url:
                return url
            raise RuntimeError(f"succeeded 但没 video_url:{t}")
        if st in ("failed", "cancelled", "expired"):
            raise RuntimeError(f"生成失败 status={st}: {t.get('error') or t}")
        time.sleep(10)
    raise RuntimeError(f"生成超时({wait_s}s),末态:{last}")


# 差距轴(按可/不可替换分类):证明因子化在生成中成立 = 非替换轴差距小 ∧ 可替换轴差距大
GAP_AXES = [
    ("visual_style", "不可替换", "整体视觉风格/画质质感"),
    ("lighting", "不可替换", "打光(光源/明暗/对比)"),
    ("shot_structure", "不可替换", "镜头结构(几个镜头/景别/如何推进)"),
    ("camera", "不可替换", "运镜/机位"),
    ("pacing", "不可替换", "节奏/氛围"),
    ("character", "可替换", "主体人物外观身份"),
]


def _keyframes(work_dir, k):
    m = json.load(open(os.path.join(work_dir, "manifest.json"), encoding="utf-8"))
    fs = [f["path"] for f in m.get("frames", [])]
    if len(fs) <= k:
        return fs
    step = len(fs) / k
    return [fs[min(len(fs) - 1, int(i * step))] for i in range(k)]


def measure_gap(ref_dir, gen_dir, k=5):
    """让 doubao **直接检测 参考↔生成 的差距**,按轴因子化(比较型/diff 证据)。
    预期:不可替换(结构)轴 gap 小=结构复现;可替换(人物)轴 gap 大=换槽成功。
    返回 {axes:{轴:{gap,note}}, struct_preserved, character_swapped, verdict}。"""
    A, B = _keyframes(ref_dir, k), _keyframes(gen_dir, k)
    axes_desc = "；".join(f"{a}({tag}:{d})" for a, tag, d in GAP_AXES)
    q = (f"上面先是参考视频A的{len(A)}帧,再是生成视频B的{len(B)}帧。**逐轴判断 A 与 B 的差距**,"
         "gap 取值 {same, minor, major}。轴:" + axes_desc + "。"
         "严格只输出 JSON:{\"axes\":{\"<轴名>\":{\"gap\":\"same|minor|major\",\"note\":\"\"}}}")
    content = ([{"type": "text", "text": "参考视频 A 的帧:"}] + [_img(p) for p in A]
               + [{"type": "text", "text": "生成视频 B 的帧:"}] + [_img(p) for p in B]
               + [{"type": "text", "text": q}])
    txt, _ = _chat([{"role": "user", "content": content}], max_tokens=800)
    s = txt[txt.find("{"): txt.rfind("}") + 1]
    axes = json.loads(s).get("axes", {})
    struct_ok = all(axes.get(a, {}).get("gap") in ("same", "minor")
                    for a, tag, _ in GAP_AXES if tag == "不可替换")
    char_swapped = axes.get("character", {}).get("gap") == "major"
    verdict = ("PASS:结构复现(不可替换轴差距小)+ 人物换成(可替换轴差距大)=因子化成立"
               if (struct_ok and char_swapped) else
               "CHECK:非替换轴或人物轴差距不符预期,因子化可能未完全成立")
    return {"axes": axes, "struct_preserved": struct_ok,
            "character_swapped": char_swapped, "verdict": verdict}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", help="参考视频 webvideo 产出目录(含 manifest.json)")
    ap.add_argument("--swap-character", default=None, help="换掉角色槽(证明人物可替换)")
    ap.add_argument("--swap-setting", default=None)
    ap.add_argument("--i2v", action="store_true", help="先 Seedream 出新角色图再 I2V")
    ap.add_argument("--out", default="synth_out")
    ap.add_argument("--no-generate", action="store_true", help="只出模板+prompt,不真生成")
    ap.add_argument("--gap-vs", default=None,
                    help="给一个生成视频目录 → 让豆包直接检测 参考↔生成 逐轴差距(比较型验证因子化)")
    a = ap.parse_args()

    if a.gap_vs:
        print(json.dumps(measure_gap(a.ref, a.gap_vs), ensure_ascii=False, indent=1))
        return

    tpl = extract_template(a.ref)
    print("=== TEMPLATE(因子化:不可替换 vs 可替换)===")
    print(json.dumps(tpl, ensure_ascii=False, indent=1))

    overrides = {}
    if a.swap_character:
        overrides["character"] = a.swap_character
    if a.swap_setting:
        overrides["setting"] = a.swap_setting
    prompt, ratio, dur = render_prompt(tpl, overrides)
    print("\n=== 生成 PROMPT(TEMPLATE 冻结,只换 SLOTS)===")
    print(f"[ratio {ratio} | dur {dur}s]\n{prompt}")
    print(f"\n换掉的槽位 = {overrides or '(无,复现原片)'}")

    if a.no_generate:
        print("\n(--no-generate:只出因子化+prompt,未真生成)")
        return

    os.makedirs(a.out, exist_ok=True)
    image_url = None
    if a.i2v and overrides.get("character"):
        print("\n[Seedream] 生成新角色图(可替换资产实体化)...")
        image_url = gen_image(overrides["character"] + ",电影级布光,暗背景,半身特写,高细节")
        print("角色图 URL:", image_url)
    print("\n[Seedance] 提交视频生成(异步,可能几分钟)...")
    vurl = gen_video(prompt, ratio, dur, image_url=image_url)
    print("视频 URL:", vurl)
    video, how = webvideo.acquire(vurl, a.out)
    print("已下载:", video, "|", how)
    print("\n下一步:python video_evidence.py", a.out, "→ 验证'同结构不同角色'(结构保持+新角色一致)")


if __name__ == "__main__":
    main()
