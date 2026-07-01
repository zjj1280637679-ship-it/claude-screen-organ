#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""webvideo —— 网页/流媒体视频"取数"工具:把一段网页视频下载下来、拆成模型可吃的
帧序列 + 音轨,补上感知插件够不到的那条信道。**只做取数,不调模型**(调模型在 ark/你的 API 那层)。

三段:
  抓(URL)   易 = yt-dlp(1000+站、HLS/DASH、可 --cookies-from-browser 进登录态);
             难(任意登录态 SPA)= 浏览器信道(chrome-devtools MCP)抓网络请求拿 .m3u8/.mp4 直链,再喂本工具。
  下(download) yt-dlp 优先;直链清单退回 ffmpeg -c copy。
  拆(model-ready) ffmpeg 采帧(fps)+ 抽音轨(16k mono wav,ASR 就绪)。
产出 manifest.json:{video, frames[], audio, duration, has_audio, ...} → 交 ark/2.1 转画面 + ASR 转音频。

用法:
  python webvideo.py <页面URL | 直链.m3u8/.mp4 | 本地文件> [--out DIR] [--fps 1.0]
                     [--max-frames 60] [--cookies-from-browser chrome] [--no-audio]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

VIDEO_EXTS = ("mp4", "mkv", "webm", "mov", "ts")


def _run(cmd):
    """跑外部命令,返回 (rc, stdout, stderr)。永不静默:失败把 stderr 带出。"""
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def _have(exe):
    return shutil.which(exe) is not None


def _is_url(s):
    return s.startswith("http://") or s.startswith("https://")


def _is_direct_media(s):
    base = s.split("?")[0].lower()
    if _is_url(s) and any(base.endswith("." + e) for e in ("m3u8", "mp4", "mpd", "ts", "webm", "mov")):
        return True
    # 有些 CDN 直链路径无扩展名(signature 全在 query),靠 mime_type=video_xxx 认出来
    return _is_url(s) and "mime_type=video" in s.lower()


def _find_video(out_dir):
    for f in sorted(os.listdir(out_dir)):
        if f.startswith("video.") and f.rsplit(".", 1)[-1].lower() in VIDEO_EXTS:
            return os.path.join(out_dir, f)
    return None


def acquire(src, out_dir, cookies_browser=None, referer=None, user_agent=None, headers=None):
    """拿到本地视频文件。返回 (path, how) 或抛 RuntimeError(永不静默,带可执行指引)。

    referer/user_agent/headers = 抓 URL 那一刻浏览器(CDP)实际发的请求上下文。
    防盗链 CDN(如 ByteDance 365yg/tos)只认 URL 会 403,寻址 = URL + 请求头,必须一起回放。"""
    headers = list(headers or [])
    if not _is_url(src):
        if os.path.isfile(src):
            return os.path.abspath(src), "local_file"
        raise RuntimeError(f"不是 URL 也不是存在的文件:{src}")
    ctx_tag = ("+referer" if referer else "") + ("+headers" if headers else "")
    ytd_err = ["yt-dlp 未安装"]
    if _have("yt-dlp"):                       # 1) yt-dlp 优先(页面URL/直链/登录态cookie全包)
        cmd = ["yt-dlp", "--no-playlist", "-f", "bestvideo*+bestaudio/best",
               "--merge-output-format", "mp4", "-o", os.path.join(out_dir, "video.%(ext)s")]
        if cookies_browser:
            cmd += ["--cookies-from-browser", cookies_browser]
        if referer:
            cmd += ["--referer", referer]
        if user_agent:
            cmd += ["--user-agent", user_agent]
        for h in headers:
            cmd += ["--add-header", h]
        cmd += [src]
        rc, so, se = _run(cmd)
        got = _find_video(out_dir)
        if rc == 0 and got:
            return got, "yt-dlp" + (f"+cookies:{cookies_browser}" if cookies_browser else "") + ctx_tag
        ytd_err = (se or so or "").strip().splitlines()[-3:]
    if _is_direct_media(src):                 # 2) 直链清单 → ffmpeg 兜底(带请求头回放)
        video = os.path.join(out_dir, "video.mp4")
        ff = ["ffmpeg", "-y"]
        hdr_lines = list(headers)
        if referer:
            hdr_lines.append(f"Referer: {referer}")
        if hdr_lines:
            ff += ["-headers", "".join(h + "\r\n" for h in hdr_lines)]
        if user_agent:
            ff += ["-user_agent", user_agent]
        ff += ["-i", src, "-c", "copy", "-bsf:a", "aac_adtstoasc", video]
        rc, so, se = _run(ff)
        if rc == 0 and os.path.isfile(video) and os.path.getsize(video) > 0:
            return video, "ffmpeg_copy" + ctx_tag
        raise RuntimeError(f"ffmpeg 下载直链失败:{(se or '')[-400:]}")
    raise RuntimeError(
        "取视频失败:yt-dlp 没拿到、且不是可识别的直链。\n"
        f"  yt-dlp 末行:{ytd_err}\n"
        "  登录态 SPA:用 chrome-devtools MCP 抓网络请求拿 .m3u8/.mp4 直链再喂本工具;"
        "  防盗链 CDN(403):从同一条网络请求里取 Referer/UA,加 --referer/--user-agent/--header 回放;"
        "  或加 --cookies-from-browser chrome 让 yt-dlp 带你的登录(Chrome 在运行可能锁 cookie 库)。")


def probe(video):
    """ffprobe → 时长/音轨/分辨率/帧率。失败返回保守默认 + probe_ok=False 招认。"""
    rc, so, _ = _run(["ffprobe", "-v", "quiet", "-print_format", "json",
                      "-show_format", "-show_streams", video])
    info = {"duration_s": None, "has_audio": None, "width": None, "height": None,
            "fps": None, "probe_ok": rc == 0}
    if rc != 0:
        return info
    try:
        d = json.loads(so)
        dur = float(d.get("format", {}).get("duration", 0) or 0)
        info["duration_s"] = round(dur, 2) or None
        for s in d.get("streams", []):
            if s.get("codec_type") == "video" and info["width"] is None:
                info["width"], info["height"] = s.get("width"), s.get("height")
                fr = s.get("avg_frame_rate", "0/0")
                try:
                    n, dd = fr.split("/")
                    info["fps"] = round(int(n) / int(dd), 2) if int(dd) else None
                except Exception:
                    pass
            if s.get("codec_type") == "audio":
                info["has_audio"] = True
        if info["has_audio"] is None:
            info["has_audio"] = False
    except Exception:
        info["probe_ok"] = False
    return info


def sample_frames(video, out_dir, fps, max_frames):
    """采帧 + 超额均匀抽稀(别只取前 N、丢尾)。返回 (frames[], capped)。
    frames[i] = {"path", "t_seconds"}:t_seconds 由帧序号/fps 反推(采样时刻近似;证据要引用时间点,
    这是抽取时就该带的内在字段,不算编排层concern)。"""
    frames_dir = os.path.join(out_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    rc, _, se = _run(["ffmpeg", "-y", "-i", video, "-vf", f"fps={fps}", "-q:v", "3",
                      os.path.join(frames_dir, "f_%04d.jpg")])
    paths = sorted(os.path.join(frames_dir, f)
                   for f in os.listdir(frames_dir) if f.endswith(".jpg"))
    capped = False
    if max_frames and len(paths) > max_frames:
        step = len(paths) / max_frames
        keep = {paths[min(len(paths) - 1, int(i * step))] for i in range(max_frames)}
        for f in paths:
            if f not in keep:
                os.remove(f)
        paths, capped = sorted(keep), True
    if rc != 0 and not paths:
        raise RuntimeError(f"采帧失败:{(se or '')[-400:]}")

    def _t(p):  # f_0001.jpg -> 序号 -> (序号-1)/fps 秒
        try:
            idx = int(os.path.basename(p).split("_")[1].split(".")[0])
            return round((idx - 1) / float(fps), 3)
        except Exception:
            return None
    frames = [{"path": p, "t_seconds": _t(p)} for p in paths]
    return frames, capped


def clip(video, out_path, t0, t1):
    """切子片 [t0,t1](秒),供把可疑区间重喂视频模型追问(递归'再看深一点'的底料)。
    帧准确=重编码。失败抛 RuntimeError(永不静默)。"""
    dur = max(0.01, float(t1) - float(t0))
    rc, _, se = _run(["ffmpeg", "-y", "-ss", str(t0), "-i", video, "-t", str(dur),
                      "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", out_path])
    if rc == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        return out_path
    raise RuntimeError(f"切子片失败:{(se or '')[-400:]}")


def detect_shots(video, threshold=0.3):
    """启发式检测剪辑切点(场景切换)。ffmpeg scene 滤镜 → [切点秒]。无模型。
    threshold 越低越敏感;**单阈值天生会漏暗场/渐变切点**(如暗室→户外的低分切换),故调用方应把它当 best-effort、
    别据它硬断言镜头数(见 video_evidence:'剪辑不算缺陷'由问题措辞兜底,不依赖此计数)。失败/无切点 → []。"""
    import re
    _, _, se = _run(["ffmpeg", "-i", video, "-vf",
                     f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"])
    cuts = sorted({round(float(m.group(1)), 2)
                   for m in re.finditer(r"pts_time:([\d.]+)", se or "")
                   if float(m.group(1)) > 0.2})
    return cuts


def extract_audio(video, out_dir):
    """抽 16k mono wav(ASR 就绪)。无音轨/失败 → None,上层据 has_audio 招认。"""
    audio = os.path.join(out_dir, "audio.wav")
    rc, _, _ = _run(["ffmpeg", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", audio])
    if rc == 0 and os.path.isfile(audio) and os.path.getsize(audio) > 44:
        return audio
    return None


def main():
    ap = argparse.ArgumentParser(description="网页视频取数(下载+采帧+抽音轨),不调模型。")
    ap.add_argument("src", help="页面URL | 直链.m3u8/.mp4 | 本地视频文件")
    ap.add_argument("--out", default=None, help="输出目录(默认 ./webvideo_out/<时间戳>)")
    ap.add_argument("--fps", type=float, default=1.0, help="采帧频率(帧/秒,默认1)")
    ap.add_argument("--max-frames", type=int, default=60, help="最多保留帧(均匀抽稀,默认60)")
    ap.add_argument("--cookies-from-browser", default=None,
                    help="yt-dlp 读浏览器cookie进登录态,如 chrome")
    ap.add_argument("--referer", default=None,
                    help="回放抓URL时浏览器的 Referer(防盗链CDN 403 必需,从CDP网络请求头取)")
    ap.add_argument("--user-agent", default=None, help="回放浏览器 User-Agent")
    ap.add_argument("--header", action="append", default=None,
                    help="额外请求头 'K: V',可重复(任意防盗链上下文)")
    ap.add_argument("--no-audio", action="store_true", help="跳过抽音轨")
    a = ap.parse_args()

    if not _have("ffmpeg") or not _have("ffprobe"):
        print("[FATAL] 需要 ffmpeg/ffprobe", file=sys.stderr)
        sys.exit(2)

    out_dir = a.out or os.path.join("webvideo_out", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    rep = {"source": a.src, "out_dir": os.path.abspath(out_dir)}

    try:
        video, how = acquire(a.src, out_dir, a.cookies_from_browser,
                             referer=a.referer, user_agent=a.user_agent, headers=a.header)
    except RuntimeError as e:
        rep.update({"ok": False, "stage": "acquire", "error": str(e)})
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        sys.exit(1)
    rep["video"], rep["acquired_by"] = video, how
    rep.update(probe(video))

    frames, capped = sample_frames(video, out_dir, a.fps, a.max_frames)
    rep["frames"], rep["frame_count"], rep["frames_capped"] = frames, len(frames), capped

    if a.no_audio:
        rep["audio"], rep["audio_note"] = None, "skipped (--no-audio)"
    elif rep.get("has_audio"):
        au = extract_audio(video, out_dir)
        rep["audio"] = au
        rep["audio_note"] = None if au else "有音轨但抽取失败"
    else:
        rep["audio"], rep["audio_note"] = None, "源无音轨"

    rep["ok"] = True
    # 诚实提示:画面走帧序列、声音走音轨;两条都喂才不漏(尤其音视频同步类如 Seedance 1.5)
    rep["next"] = ("frames→视觉模型转画面;audio→ASR转文字;"
                   "音频缺则只能转画面(会漏声音那一半)")
    mf = os.path.join(out_dir, "manifest.json")
    with open(mf, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    rep["manifest"] = mf
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
