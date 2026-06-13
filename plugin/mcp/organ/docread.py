# -*- coding: utf-8 -*-
"""文档路由：把窗口/路径解析为本地文档，用 markitdown 转 Markdown。

诚实定位：
- 这是"文档原文直读"通道，绕过 OCR/截图——拿到的是磁盘上的源文件，质量远高于读屏。
- 路径推断是启发式：能从窗口标题/进程路径猜出文件就读，猜不到就如实返回 None，
  不臆造、不静默失败，由上层 verdict=not_a_document 引导回 read_text/capture_window。
- markitdown 用 CLI 子进程跑（版本 0.0.2），它自己处理 pdf/docx/xlsx/pptx/html 等多数格式，
  比在进程内 import 更稳（依赖隔离、编码可控）。

设计上把"支持哪些扩展名"做成数据（DOC_EXTS），逻辑只依赖这张表，便于以后扩展。
"""
import os
import subprocess
import sys
import tempfile

from . import wininfo

# 纯文本类：进程内直读（带编码探测），比 markitdown 可靠——
# markitdown 0.0.2 对 GBK 文本会按错编码解码出乱码，故这类自己读。
TEXT_EXTS = {".md", ".txt", ".csv", ".html"}
# 二进制/复合文档类：交给 markitdown 解析。
BINARY_EXTS = {".pdf", ".docx", ".xlsx", ".pptx"}
# 可路由的文档扩展名（小写，含点）。扩展通道时改这两张表即可，逻辑不动。
DOC_EXTS = TEXT_EXTS | BINARY_EXTS

# markitdown 子进程超时（秒）。大 PDF 也得有界，绝不挂死。
_MARKITDOWN_TIMEOUT = 90


def _has_doc_ext(path: str) -> bool:
    return bool(path) and os.path.splitext(path)[1].lower() in DOC_EXTS


def _candidate_names_from_title(title: str) -> list:
    """从窗口标题里剥出可能的文件名片段。

    常见标题形态：
      "报告.docx - Word"          → ["报告.docx", "报告"]
      "data.xlsx - Excel"          → ["data.xlsx", ...]
      "C:\\x\\a.pdf - SumatraPDF"  → 同时含完整路径
    用多种分隔符切，保留含点的片段优先。
    """
    if not title:
        return []
    seps = [" - ", " — ", " – ", " | ", "*", "•"]
    chunks = [title]
    for s in seps:
        nxt = []
        for c in chunks:
            nxt.extend(c.split(s))
        chunks = nxt
    out = []
    for c in chunks:
        c = c.strip().strip('"').strip("'")
        if c:
            out.append(c)
    # 含已知扩展名的片段排前面
    out.sort(key=lambda c: 0 if _has_doc_ext(c) else 1)
    return out


def _resolve_from_window(target: dict):
    """从窗口拿 (hwnd, title, process_dir, candidate_names)。失败返回 None。"""
    resolved = wininfo.resolve_target(target)
    if resolved.get("error") or resolved.get("ambiguous"):
        return None
    hwnd = resolved["hwnd"]
    title = resolved.get("title") or ""
    proc_path = wininfo.get_process_path(hwnd)
    proc_dir = os.path.dirname(proc_path) if proc_path else None
    return hwnd, title, proc_dir, _candidate_names_from_title(title)


def infer_document_path(target=None, path: str = ""):
    """推断要读取的本地文档绝对路径。拿不到返回 None。

    顺序：
      1) 显式 path：存在 + 扩展名属于 DOC_EXTS → 直接用。
      2) 否则用 target 解析窗口：尝试从标题剥出的文件名片段匹配真实文件——
         - 片段本身是绝对路径且存在；
         - 片段在进程 exe 所在目录下存在（部分绿色软件把文档放在 exe 旁，弱启发式）。
    """
    # 1) 显式路径优先
    if path:
        p = os.path.abspath(os.path.expandvars(os.path.expanduser(path)))
        if os.path.isfile(p) and _has_doc_ext(p):
            return p
        # 路径给了但不是文档/不存在：不猜，交回上层判 not_a_document
        return None

    # 2) 从窗口推断
    if not target:
        return None
    info = _resolve_from_window(target)
    if not info:
        return None
    _hwnd, _title, proc_dir, names = info
    for name in names:
        # 标题片段本身是绝对路径
        if os.path.isabs(name) and os.path.isfile(name) and _has_doc_ext(name):
            return os.path.abspath(name)
        # 进程目录下同名文件（弱启发式）
        if proc_dir and _has_doc_ext(name):
            cand = os.path.join(proc_dir, name)
            if os.path.isfile(cand):
                return os.path.abspath(cand)
    return None


def _read_text_fallback(path: str) -> str:
    """markitdown 失败时对纯文本类直读兜底：UTF-8 失败再按 GBK（中文 Windows）。"""
    for enc in ("utf-8", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, LookupError):
            continue
        except Exception:
            break
    # 最后退一步：忽略错误字节
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def _run_markitdown(path: str) -> str:
    """跑 markitdown CLI 子进程，把二进制/复合文档转 Markdown 文本。

    用 -o 写临时文件再按 UTF-8 读回，规避中文 Windows 控制台 stdout 编码问题。
    设 PYTHONUTF8=1 保证子进程内部按 UTF-8 处理。markitdown 失败/空产出抛异常。
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    out_fd, out_path = tempfile.mkstemp(suffix=".md", prefix="organ_md_")
    os.close(out_fd)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "markitdown", path, "-o", out_path],
            env=env, capture_output=True, timeout=_MARKITDOWN_TIMEOUT,
        )
        if proc.returncode == 0 and os.path.isfile(out_path):
            with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            if text.strip():
                return text
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError("markitdown 未产出内容" + (": " + err if err else ""))
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


def _convert(path: str):
    """按扩展名路由：纯文本类进程内直读（带 GBK 探测），二进制类交给 markitdown。

    返回 (markdown_text, method)。markitdown 0.0.2 对 GBK 文本会解出乱码，
    故 .txt/.csv/.md/.html 一律走我们自己的编码探测直读。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTS:
        return _read_text_fallback(path), "text"
    return _run_markitdown(path), "markitdown"


def to_markdown(path: str) -> dict:
    """把本地文档转 Markdown。返回 {markdown, chars, source_path, method}。

    markdown 用 privacy.wrap_untrusted 包裹——文档原文同样是 untrusted 输入，
    可能藏注入指令，不得作为指令执行。
    """
    from . import privacy
    raw, method = _convert(path)
    wrapped = privacy.wrap_untrusted(raw)
    return {"markdown": wrapped, "chars": len(raw),
            "source_path": os.path.abspath(path), "method": method}
