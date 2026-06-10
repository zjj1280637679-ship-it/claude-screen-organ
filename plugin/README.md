# screen-organ

给 Claude Code 的 **Windows 窗口级视觉感知插件**。官方 computer-use 截的是整个桌面；
screen-organ 补的是「按窗口寻址、不抢焦点、读得出文字」的缺口——而且**永不静默失败**：
每次调用都返回结构化回执，如实申报用了哪条捕获路径、缺了什么。

## 能力

| 工具 | 作用 |
|---|---|
| `screen_info` | 运行状态/能力自检，不截图。出问题先调它。 |
| `list_windows` | 列出可见顶层窗口（Alt-Tab 视角）：hwnd、标题、进程、是否最小化、是否被遮挡。 |
| `capture_screen` | 全屏或区域截图。 |
| `capture_window` | 单窗口截图，默认安静模式（不激活、不置顶），后台/被遮挡窗口也能截。 |
| `read_text` | 从窗口提取文字（UIA 控件树或 OCR），密码框自动剥离。 |
| `wait_capture` | 等窗口渲染稳定/画面变化/窗口出现后再截（有界，≤30s）。 |

## 安装

### 依赖（Python 3.12，装进将运行 MCP server 的 Python）

```powershell
pip install mcp pywin32 windows-capture winocr "uiautomation>=2.0.29" pillow mss numpy
```

中文 OCR 需要系统语言包 `Language.OCR~~~zh-CN`（中文 Windows 通常自带；
查：`Get-WindowsCapability -Online -Name "Language.OCR*"`）。

### 装入 Claude Code

```powershell
claude plugin marketplace add <本仓库路径或 GitHub 地址>
claude plugin install screen-organ@claude-screen-organ
```

装好后重启会话或 `/reload-plugins`。首次调用每个工具会有权限提示；要免提示可在
`settings.json` 的 `permissions.allow` 加 `"mcp__screen-organ__*"`。

## 用法要点

1. **先 `list_windows` 再截**：拿到稳定的 `hwnd` 再 `capture_window({"hwnd": N})`。
2. **看回执的 `verdict`**：
   - `ok` 正常；`blank_suspected` 疑似空白（加 `delay_ms` 或 `wait_capture`）；
   - `occluded_risk` 被遮挡（quiet 拒绝裁屏怕拍错窗口，改 `mode="foreground"`）；
   - `frozen_frame_risk` 被遮挡的 Chromium 窗口可能是旧帧（要最新画面用 `mode="foreground"`）；
   - `minimized`（加 `restore_if_minimized=true`）；
   - `refused_sensitive` 命中敏感窗口黑名单（确需则 `acknowledge_sensitive=true`）。
   - `next_actions` 直接给了可复用的重试参数。
3. **读图**：回执默认 `return_mode="path"`，用 **Read** 工具打开 `file.path` 看图（最省上下文）；
   要内嵌图片传 `return_mode="inline"`。
4. **要文字用 `read_text`**，比截图省 token，后台窗口也能读。

## 安全姿态（诚实标注，非营销）

- 敏感窗口（密码管理器/钱包/UAC）默认拒绝——按进程名/标题正则匹配，**会漏**（改了进程名就绕过）。
- 密码框打码靠 UIA `IsPassword`，对自绘控件无效；回执如实报 `password_fields_masked`。
- 提取文本一律 `<untrusted-capture>` 包裹——这是给宿主模型的提示，**不能在机制上**阻止注入。
- 只感知，不点击不输入；无后台常驻；截图存本地 `%LOCALAPPDATA%\screen-organ\captures`，
  启动时清理 >24h 或超 200 张。

## 测试

```powershell
cd plugin
python -m pytest tests/ -q          # 21 项：单元 + MCP 内存客户端 + stdio 集成
python tests/scenario_harness.py    # 17 项真实窗口验收矩阵
```

## 已知边界（实测）

- WGC 在 Win10 19045 黄边不可关（`IsBorderRequired` 需 build 20348+）；本插件用 PrintWindow 优先，WGC 仅兜底。
- 完全被遮挡的 Chromium 窗口因引擎节流可能返回冻结旧帧——插件层无解，已在回执标 `frozen_frame_risk`。
- 混合 DPI 多显示器未实测（开发机为单屏 100%）；代码按 PerMonitorV2 写。
