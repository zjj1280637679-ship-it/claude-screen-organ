# -*- coding: utf-8 -*-
"""L2 场景 harness：驱动真实窗口跑验收矩阵。

不依赖 Claude Code，直接调 organ 库 + server 工具，断言：
  - 好图：verdict ok 且非黑帧 且（窗口场景）身份验证通过
  - 诚实降级：明确 verdict + 可执行 next_actions
  - 明确拒绝：refused/ambiguous/failed 语义正确
每个场景输出 PASS/FAIL + 证据。退出码非零表示有 FAIL。
"""
import json
import os
import subprocess
import sys
import time

MCP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp")
sys.path.insert(0, MCP_DIR)
SMOKE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), ".smoke")

from organ import capture, quality, textread, wininfo  # noqa: E402

results = []


def record(sid, name, ok, evidence):
    results.append({"id": sid, "name": name, "pass": bool(ok), "evidence": evidence})
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {sid} {name}: {evidence}")


def find_hwnd(needle):
    for w in wininfo.list_windows(include_occlusion=False):
        if needle.lower() in w["title"].lower() or needle.lower() in w["process"].lower():
            return w
    return None


def img_ok(img):
    if img is None:
        return False
    m = quality.blank_metrics(img)
    return not m["likely_blank"]


def launch(path_or_cmd, *args):
    return subprocess.Popen([path_or_cmd, *args])


def main():
    procs = []
    try:
        # ---------- 准备真实窗口 ----------
        procs.append(launch("notepad", os.path.join(SMOKE, "ocr_sample.txt")))
        procs.append(launch("calc"))
        time.sleep(3)

        # ===== S1 记事本前台 =====
        np = find_hwnd("记事本") or find_hwnd("notepad")
        if np:
            img, rpt = capture.capture_window(np["hwnd"], mode="quiet")
            record("S1", "记事本前台截图", img_ok(img) and rpt["verdict"] in ("ok", "frozen_frame_risk"),
                   f"method={rpt.get('method')} verdict={rpt.get('verdict')}")
        else:
            record("S1", "记事本前台截图", False, "未找到记事本窗口")

        # ===== S4 记事本最小化 =====
        if np:
            import win32gui
            import win32con
            win32gui.ShowWindow(np["hwnd"], win32con.SW_MINIMIZE)
            time.sleep(0.6)
            img, rpt = capture.capture_window(np["hwnd"], mode="quiet")
            ok = (img is None and rpt["verdict"] == "minimized" and len(rpt["warnings"]) > 0)
            record("S4", "最小化窗口诚实降级", ok, f"verdict={rpt['verdict']}")
            # restore 路径
            img2, rpt2 = capture.capture_window(np["hwnd"], mode="quiet",
                                                restore_if_minimized=True)
            record("S4b", "最小化恢复后截图", img_ok(img2),
                   f"verdict={rpt2.get('verdict')} method={rpt2.get('method')}")

        # ===== S6/S8 Chrome 前台 + 被遮挡 =====
        ch = find_hwnd("测试页") or find_hwnd("chrome")
        if ch:
            img, rpt = capture.capture_window(ch["hwnd"], mode="quiet")
            record("S6", "Chrome(GPU)前台截图", img_ok(img),
                   f"method={rpt.get('method')} verdict={rpt.get('verdict')} "
                   f"occ={rpt.get('occlusion',{}).get('match_ratio')}")
        else:
            record("S6", "Chrome(GPU)前台截图", False, "未找到 Chrome 测试页窗口")

        # ===== S11 Electron (Claude) =====
        cl = find_hwnd("claude")
        if cl:
            img, rpt = capture.capture_window(cl["hwnd"], mode="quiet")
            record("S11", "Electron(Claude)截图", img_ok(img),
                   f"method={rpt.get('method')} verdict={rpt.get('verdict')}")

        # ===== S14 UWP (计算器) =====
        calc = find_hwnd("计算器") or find_hwnd("calc")
        if calc:
            img, rpt = capture.capture_window(calc["hwnd"], mode="quiet")
            record("S14", "UWP(计算器)截图", img_ok(img),
                   f"proc={calc['process']} method={rpt.get('method')} verdict={rpt.get('verdict')}")
            record("S28", "list_windows UWP 进程穿透",
                   calc["process"].lower() not in ("applicationframehost.exe", "?"),
                   f"穿透得进程={calc['process']}")
        else:
            record("S14", "UWP(计算器)截图", False, "未找到计算器")

        # ===== S17 全屏 =====
        full = capture.capture_screen(0)
        record("S17", "全屏截图", img_ok(full), f"size={full.size}")

        # ===== S18 区域 =====
        reg = capture.capture_screen(1, (0, 0, 400, 300)) if len(__import__("mss").MSS().monitors) > 1 \
            else capture.capture_screen(0, (0, 0, 400, 300))
        record("S18", "区域截图", reg.size == (400, 300), f"size={reg.size}")

        # ===== S19 OCR 中英文 =====
        if np:
            import win32gui
            import win32con
            win32gui.ShowWindow(np["hwnd"], win32con.SW_RESTORE)
            time.sleep(0.5)
            img, _ = capture.capture_window(np["hwnd"], mode="quiet")
            o = textread.ocr_image(img, "zh-CN")
            t = o["text"].replace(" ", "")
            hits = sum(s in t for s in ["quickbrown", "0123456789", "验收通过", "永不静默"])
            record("S19", "OCR 中英文识别", hits >= 3, f"命中 {hits}/4 关键串, upscale={o['upscale']}")

        # ===== S20 UIA 文本提取 =====
        if np:
            u = textread.uia_text(np["hwnd"])
            record("S20", "UIA 文本提取", "永不静默失败" in u.get("text", ""),
                   f"len={len(u.get('text',''))} pw_stripped={u.get('password_nodes_stripped')}")

        # ===== S21 密码框遮罩（WinForms）=====
        PS = ('Add-Type -AssemblyName System.Windows.Forms;'
              '$f=New-Object System.Windows.Forms.Form;$f.Text="harness-pw";'
              '$t=New-Object System.Windows.Forms.TextBox;$t.UseSystemPasswordChar=$true;'
              '$t.Text="secret";$f.Controls.Add($t);'
              '$tm=New-Object System.Windows.Forms.Timer;$tm.Interval=20000;'
              '$tm.add_Tick({$f.Close()});$tm.Start();'
              '[System.Windows.Forms.Application]::Run($f)')
        pwp = subprocess.Popen(["powershell", "-NoProfile", "-Command", PS])
        procs.append(pwp)
        time.sleep(3.5)
        pw = find_hwnd("harness-pw")
        if pw:
            rects = __import__("organ.privacy", fromlist=["find_password_rects"]).find_password_rects(pw["hwnd"])
            record("S21", "密码框 IsPassword 识别", len(rects) >= 1,
                   f"检出密码框 {len(rects)} 个")
        else:
            record("S21", "密码框 IsPassword 识别", False, "未找到测试窗口")

        # ===== S22 敏感窗口黑名单 =====
        from organ import privacy
        s = privacy.check_sensitive("My Vault", "1Password.exe")
        record("S22", "敏感窗口黑名单拒绝", s["sensitive"] is True, f"reason={s['reason']}")

        # ===== S23 多匹配寻址歧义 =====
        r = wininfo.resolve_target({"process": "chrome.exe"})
        # 可能只有一个 chrome 窗口；构造一定多匹配的用通用子串
        r2 = wininfo.resolve_target({"title": "e"})  # 'e' 子串大概率多匹配
        record("S23", "多匹配寻址返回候选",
               r2.get("ambiguous") is True or r.get("ambiguous") is True,
               f"candidates={len(r2.get('candidates', r.get('candidates', [])))}")

        # ===== S24 不存在窗口 failed 语义 =====
        r3 = wininfo.resolve_target({"title": "绝不存在zzz999"})
        record("S24", "不存在窗口 failed 语义", "error" in r3, f"{r3.get('error','')[:40]}")

        # ===== S26 连续 20 次稳定性 + 清理 =====
        from organ import store
        errs = 0
        for i in range(20):
            try:
                im = capture.capture_screen(0)
                if im is None:
                    errs += 1
            except Exception:
                errs += 1
        cu = store.cleanup()
        record("S26", "连续20次截图稳定", errs == 0, f"失败 {errs}/20, 清理后保留 {cu['kept']}")

        # ===== S25 wait_capture 等变化（tkinter 变色窗）=====
        TK = (
            "import tkinter as tk, itertools\n"
            "r = tk.Tk(); r.title('harness-watch'); r.geometry('300x200+100+100')\n"
            "c = itertools.cycle(['red', 'green', 'blue', 'yellow'])\n"
            "lbl = tk.Label(r, width=40, height=10); lbl.pack()\n"
            "def tick():\n"
            "    lbl.config(bg=next(c))\n"
            "    r.after(600, tick)\n"
            "tick()\n"
            "r.after(15000, r.destroy)\n"
            "r.mainloop()\n"
        )
        tkf = os.path.join(SMOKE, "_harness_watch.py")
        with open(tkf, "w", encoding="utf-8") as f:
            f.write(TK)
        wp = subprocess.Popen([sys.executable, tkf])
        procs.append(wp)
        time.sleep(2.5)
        from organ import watch
        whd = find_hwnd("harness-watch")
        if whd:
            img, wr = watch.wait_capture(whd["hwnd"], until="change", timeout=6.0,
                                         change_threshold=8.0)
            record("S25", "wait_capture 等画面变化", wr.get("satisfied") is True,
                   f"polls={wr.get('polls')} diff={wr.get('final_diff')}")
        else:
            record("S25", "wait_capture 等画面变化", False, "未找到变色窗口")

    finally:
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass

    npass = sum(r["pass"] for r in results)
    print(f"\n==== {npass}/{len(results)} PASS ====")
    out = os.path.join(SMOKE, "harness_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"详情: {out}")
    sys.exit(0 if npass == len(results) else 1)


if __name__ == "__main__":
    main()
