# -*- coding: utf-8 -*-
"""video_evidence —— 路由式递归视频证据编排(薄环)。

分层(P7):抽取=webvideo(确定性,不调模型) ⊥ 感知=doubao(经额度网关) ⊥ 编排=本模块(控制,当黑箱调另两层)。
第一性原理落地:
  P1 测量诚实/三值:每问只判 answered|refuted|UNMEASURED;缺口不凑答案,显式招认。
  P2/P3 可证停机:停机谓词只读**可核查结构事实**(coverage、跑没跑反驳),不读模型自报置信度;
        rank=未闭 gap 数严格递减 + max_steps 硬上限 → 可证终止(Safety=不变式 / Liveness=秩)。
  P4 可证伪:高成本视觉断言必须先跑一次**主动找反例**,不被反驳才 verified=true。
  P5 出处:每条证据带 source∈{pcm_measured, motion_measured, video_model_assertion} + verified。
  P6 信号基完备:证据基 = {视觉,音频}×{点,段} + 跨信道 SYNC(Δ);音频语义无 ASR 信道 → UNMEASURED,不硬编。

用法: python video_evidence.py <webvideo产出目录(含manifest.json)|视频文件> [--task video-gen-eval] [--max-steps 14]
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
import wave

import webvideo  # 抽取层(同目录):sample_frames / clip / acquire

GATEWAY = os.environ.get("ARK_GATEWAY", "http://127.0.0.1:8788")


# ============ 感知原语(黑箱:调 doubao,本层不做模型数学) ============
def _chat(messages, max_tokens=400, model="auto-chat", timeout=180):
    body = {"model": model, "max_tokens": max_tokens, "messages": messages}
    req = urllib.request.Request(GATEWAY + "/v1/chat/completions",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    j = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    return j["choices"][0]["message"]["content"].strip(), j.get("model")


def _img(path):
    b = base64.b64encode(open(path, "rb").read()).decode()
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b}}


def vision(frame_paths, prompt, max_tokens=400):
    """把若干帧 + 问题喂视觉模型。返回 (text, model)。"""
    content = [{"type": "text", "text": prompt}] + [_img(p) for p in frame_paths[:16]]
    return _chat([{"role": "user", "content": content}], max_tokens=max_tokens)


# ============ 确定性测量(无模型 → verified=true) ============
def audio_envelope(wav, win_s=0.1):
    """16k mono wav → [(t, rms)] 能量包络。纯测量。无 wav/audioop → None。"""
    if not wav or not os.path.isfile(wav):
        return None
    try:
        import audioop
        w = wave.open(wav, "rb")
    except Exception:
        return None
    sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
    n = max(1, int(sr * win_s))
    env, t = [], 0.0
    while True:
        buf = w.readframes(n)
        if not buf:
            break
        if ch > 1:
            buf = audioop.tomono(buf, sw, 0.5, 0.5)
        env.append((round(t, 3), audioop.rms(buf, sw)))
        t += win_s
    w.close()
    return env


def audio_facts(env):
    """从包络提可核查事实(has_sound / 峰值时刻 / 响窗 / 静音比)。"""
    if not env:
        return None
    rms = [r for _, r in env]
    mx = max(rms) or 1
    peak_t = max(env, key=lambda x: x[1])[0]
    loud = [t for t, r in env if r > 0.6 * mx]
    silent = sum(1 for r in rms if r < 0.05 * mx) / len(rms)
    return {"has_sound": mx > 200, "max_rms": mx, "peak_t": peak_t,
            "loud_windows_t": loud[:20], "silent_ratio": round(silent, 2)}


def visual_motion(frame_items):
    """帧间视觉变化包络 [(t, diff)](灰度降采样均差)。PIL 缺 → None(上层标 UNMEASURED)。"""
    try:
        from PIL import Image
    except Exception:
        return None
    prev, env = None, []
    for it in frame_items:
        try:
            px = Image.open(it["path"]).convert("L").resize((64, 36)).tobytes()
        except Exception:
            continue
        if prev is not None and it.get("t_seconds") is not None:
            env.append((it["t_seconds"], round(sum(abs(a - b) for a, b in zip(px, prev)) / len(px), 2)))
        prev = px
    return env or None


# ============ 证据(P5 出处 / P4 反驳) ============
def _ev(channel, kind, time, content, source, verified=False, disconfirm=None):
    return {"channel": channel, "kind": kind, "time": time, "content": content,
            "source": source, "verified": verified, "disconfirm": disconfirm}


def disconfirm_visual(frame_paths, claim):
    """P4:主动找反例。返回 (refuted, why)。"""
    prompt = ("下面几帧来自同一视频。有人断言:「%s」。**主动找反例**:"
              "若这些帧里有任何证据表明该断言不成立或相反,输出 'REFUTED:<简短原因>';"
              "找不到反例则输出 'NOT_REFUTED'。只输出这一行。" % claim)
    txt, _ = vision(frame_paths, prompt, max_tokens=180)
    return txt.upper().startswith("REFUTED"), txt[:180]


# ============ 编排:路由 → 递归 → 可核查停机 → 三值表达 ============
QUESTIONS = {
    "video-gen-eval": [
        {"id": "cam_motion", "ch": "visual", "kind": "causality", "cost": "high",
         "q": "**同一镜头内部**运动是否平滑(有无跳变/扭曲/瞬移)?剪辑切镜属正常,不算跳变。"},
        {"id": "obj_consistency", "ch": "visual", "kind": "state", "cost": "high",
         "q": "**同一镜头内**画面主体/物体是否一致(有无形变、闪烁、凭空增减)?不同镜头的场景变化是正常剪辑,不算不一致。"},
        {"id": "character_identity", "ch": "visual", "kind": "state", "cost": "high",
         "q": "**跨越剪辑切点**,同一角色(面容/服饰/装备)是否前后可辨认为同一人(AI 视频常见的角色漂移)?"},
        {"id": "audio_presence", "ch": "audio", "kind": "state", "cost": "low",
         "q": "是否有真实音轨声音、响的时刻在哪?"},
        {"id": "av_sync", "ch": "sync", "kind": "sync", "cost": "med",
         "q": "音频能量高峰是否与画面运动高峰对齐(给 Δ)?"},
        {"id": "audio_semantic", "ch": "audio", "kind": "state", "cost": "low",
         "q": "音频内容是什么(语音/音乐/音效,说了什么)?"},
    ]
}


def _frames_between(frames, t0, t1):
    return [f for f in frames if f.get("t_seconds") is not None and t0 <= f["t_seconds"] <= t1]


def _parse_suspect(txt):
    """从模型答复里抽 SUSPECT:[t0,t1];没有则 None。"""
    import re
    m = re.search(r"SUSPECT\s*[:：]\s*\[?\s*([\d.]+)\s*[,，]\s*([\d.]+)", txt)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


def analyze(work_dir, task="video-gen-eval", max_steps=14):
    manifest = json.load(open(os.path.join(work_dir, "manifest.json"), encoding="utf-8"))
    frames = manifest.get("frames") or []           # [{path, t_seconds}]
    video = manifest.get("video")
    wav = manifest.get("audio")
    dur = manifest.get("duration_s")
    all_paths = [f["path"] for f in frames]

    rep = {"video": video, "duration_s": dur, "task": task,
           "evidence": [], "verdicts": {}, "steps_log": [], "uncertainties": [], "next_action": []}
    steps = [0]

    def step(kind, detail):
        steps[0] += 1
        rep["steps_log"].append({"n": steps[0], "kind": kind, "detail": detail})

    # 先算好确定性通道(音频包络 / 视觉运动),供 audio/sync 用(P6)
    aenv = audio_envelope(wav)
    afacts = audio_facts(aenv)
    menv = visual_motion(frames)

    # 确定性检测剪辑切点:把"镜头内(生成质量)"与"跨镜头(剪辑=正常)"分开,别把剪辑当缺陷
    try:
        cuts = webvideo.detect_shots(video) if video else []
    except Exception:
        cuts = []
    rep["shots"] = {"n": len(cuts) + 1, "cuts": cuts}
    if video:
        rep["evidence"].append(_ev("visual", "state", None,
            f"启发式检测约 {len(cuts) + 1} 个镜头,切点 t≈{cuts}s(ffmpeg 场景阈值0.3;"
            "暗场/渐变切点可能漏,best-effort——'剪辑不算缺陷'由问题措辞兜底、不依赖此计数)",
            "shot_heuristic", verified=True))
    cut_hint = ((f"(注:本视频检测到 {len(cuts)} 个剪辑切点 t≈{cuts}s,切镜=正常叙事剪辑,"
                 "**请勿把剪辑切换本身当缺陷**,只按问题所指的范围找异常) ") if cuts else "")

    gaps = list(QUESTIONS[task])
    # rank = 未闭 gap 数;每轮闭一个 → 严格递减(P3);另有 max_steps 硬顶
    while gaps and steps[0] < max_steps:
        g = gaps.pop(0)
        qid, q = g["id"], g["q"]

        # ---------- 音频存在(确定性测量,verified=true) ----------
        if qid == "audio_presence":
            if afacts is None:
                rep["verdicts"][qid] = "unmeasured"
                rep["uncertainties"].append("音频包络不可测(无 wav / audioop 不可用)")
            else:
                rep["evidence"].append(_ev("audio", "state", afacts["peak_t"],
                    f"has_sound={afacts['has_sound']} 峰值@{afacts['peak_t']}s 静音比{afacts['silent_ratio']} 响窗{afacts['loud_windows_t'][:6]}",
                    "pcm_measured", verified=True))
                rep["verdicts"][qid] = "answered"
            step("measure-audio", str(afacts))
            continue

        # ---------- 音视频 SYNC(跨信道测量,P6) ----------
        if qid == "av_sync":
            if afacts is None or menv is None:
                rep["verdicts"][qid] = "unmeasured"
                rep["uncertainties"].append(
                    "SYNC 不可测:" + ("无音频包络" if afacts is None else "无视觉运动包络(缺 PIL)"))
                step("measure-sync", "unmeasured")
                continue
            mv_peak = max(menv, key=lambda x: x[1])[0]
            delta = round(abs(mv_peak - afacts["peak_t"]), 3)
            aligned = delta <= 0.5
            rep["evidence"].append(_ev("sync", "sync",
                {"t_visual": mv_peak, "t_audio": afacts["peak_t"], "delta": delta, "tol": 0.5},
                f"视觉运动峰@{mv_peak}s vs 音频能量峰@{afacts['peak_t']}s,Δ={delta}s,{'对齐' if aligned else '错位'}(容差0.5s)",
                "motion_measured", verified=True))
            rep["verdicts"][qid] = "answered"
            if not aligned:
                rep["next_action"].append(f"声画错位 Δ={delta}s:检查生成配音的时间对齐")
            step("measure-sync", f"delta={delta}")
            continue

        # ---------- 音频语义:无 ASR 信道 → UNMEASURED 终态(P1,不硬编) ----------
        if qid == "audio_semantic":
            rep["verdicts"][qid] = "unmeasured"
            rep["uncertainties"].append("音频语义(语音内容/音乐vs音效)需 ASR/音频理解信道,本管线未接 → 未测,不臆断")
            rep["next_action"].append("接 ark 音频理解/ASR 后可测音频语义")
            step("audio-semantic", "UNMEASURED (no ASR channel)")
            continue

        # ---------- 视觉断言:先粗看整段 → 有可疑区间才递归抽段复核(VoI/P2) → 反驳后 verified(P4) ----------
        probe = (q + cut_hint + " 只看这几帧粗判。若发现可疑区间,请在末行输出 'SUSPECT:[t0,t1]'(秒);"
                 "若整体正常,末行输出 'CLEAR'。")
        txt, mdl = vision(all_paths, probe, max_tokens=300)
        step("vision-probe", f"{qid}: {txt[:90]}")
        suspect = _parse_suspect(txt)

        claim_pos = {
            "cam_motion": "在每个镜头内部运动平滑连续、无跳变/扭曲(剪辑切镜属正常、不算跳变)",
            "obj_consistency": "在同一镜头内画面主体/物体一致、无形变或闪烁(不同镜头间的场景变化是正常剪辑、不算不一致)",
            "character_identity": "跨越剪辑切点,同一角色的面容/服饰/装备前后可辨认为同一人",
        }[qid]

        if suspect and video and steps[0] < max_steps - 1:
            # 递归:抽可疑子段、密集采帧(先抽段理解过程,再抽帧复核) → 对该段跑反驳
            t0, t1 = max(0, suspect[0] - 0.3), suspect[1] + 0.3
            try:
                import tempfile
                sub = tempfile.mkdtemp(prefix="ve_clip_", dir=work_dir)
                cpath = webvideo.clip(video, os.path.join(sub, "clip.mp4"), t0, t1)
                dense, _ = webvideo.sample_frames(cpath, sub, fps=8, max_frames=12)
                dpaths = [d["path"] for d in dense]
                step("recurse-clip", f"{qid}: dense [{round(t0,2)},{round(t1,2)}] {len(dpaths)}帧")
                refuted, why = disconfirm_visual(dpaths, claim_pos)
            except Exception as e:
                refuted, why = False, f"抽段失败,退化为粗帧反驳: {e}"
                refuted, why = disconfirm_visual(all_paths, claim_pos)
        else:
            refuted, why = disconfirm_visual(all_paths, claim_pos)
        step("disconfirm", f"{qid}: refuted={refuted} :: {why[:80]}")

        if refuted:
            rep["evidence"].append(_ev("visual", g["kind"],
                (round(suspect[0], 2), round(suspect[1], 2)) if suspect else None,
                f"反驳成立:{why}", "video_model_assertion", verified=True,
                disconfirm={"ran": True, "refuted": True}))
            rep["verdicts"][qid] = "refuted"
            rep["next_action"].append(f"{qid} 发现缺陷:{why[:60]}")
        else:
            rep["evidence"].append(_ev("visual", g["kind"], None,
                f"「{claim_pos}」经主动找反例未被反驳", "video_model_assertion", verified=True,
                disconfirm={"ran": True, "refuted": False}))
            rep["verdicts"][qid] = "answered"

    # 步数耗尽仍有未闭 gap → UNMEASURABLE_IN_SOURCE 终态(P1/P3,催停不催造假)
    for g in gaps:
        rep["verdicts"][g["id"]] = "unmeasured"
        rep["uncertainties"].append(f"{g['id']}:预算/步数耗尽未取证(UNMEASURABLE_IN_SOURCE)")

    # 停机后才算 downstream 置信度 = f(coverage, 三值),单调,绝不当停机输入(P2)
    total = len(QUESTIONS[task])
    covered = sum(1 for v in rep["verdicts"].values() if v in ("answered", "refuted"))
    unmeasured = sum(1 for v in rep["verdicts"].values() if v == "unmeasured")
    rep["coverage"] = round(covered / total, 2)
    rep["confidence_post"] = round(rep["coverage"] * (1 - 0.1 * unmeasured), 2)  # 仅报告用
    rep["steps_used"] = steps[0]

    defects = [k for k, v in rep["verdicts"].items() if v == "refuted"]
    rep["conclusion"] = (
        ("发现缺陷:" + "、".join(defects) + "。") if defects else "未发现明确缺陷(在已测通道内)。"
    ) + f" 覆盖 {covered}/{total} 问,{unmeasured} 项未测(见 uncertainties)。"
    return rep


def main():
    ap = argparse.ArgumentParser(description="路由式递归视频证据编排(薄环)。")
    ap.add_argument("src", help="webvideo 产出目录(含 manifest.json)或视频文件/URL")
    ap.add_argument("--task", default="video-gen-eval")
    ap.add_argument("--max-steps", type=int, default=14)
    a = ap.parse_args()

    work = a.src
    if not os.path.isdir(work) or not os.path.isfile(os.path.join(work, "manifest.json")):
        # 给的是视频文件/URL → 先用 webvideo 抽取
        import time as _t
        work = os.path.join("ve_out", "run")
        os.makedirs(work, exist_ok=True)
        video, how = webvideo.acquire(a.src, work)
        frames, capped = webvideo.sample_frames(video, work, fps=1.0, max_frames=30)
        info = webvideo.probe(video)
        wav = webvideo.extract_audio(video, work) if info.get("has_audio") else None
        json.dump({"video": video, "frames": frames, "audio": wav, **info},
                  open(os.path.join(work, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False)

    rep = analyze(work, task=a.task, max_steps=a.max_steps)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
