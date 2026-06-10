# 插件骨架（Claude Code plugin scaffold）

> **状态：脚手架占位，未实现。** 这里只放 Claude Code 插件的骨架与"设计规范 → 插件组件"的映射。动工前必须先焊掉 [待焊清单](../文档/待定决策与待焊清单.md) 里的 3 个致命矛盾。

## 这个插件长什么样（Claude Code 形态）

一个 Claude Code 插件可以由 manifest + 若干组件目录构成。本插件规划的组件：

```
插件骨架/
├── .claude-plugin/
│   └── plugin.json          # 插件 manifest（本仓库已放占位）
├── .mcp.json                # TODO：声明捕获用的 MCP server
├── mcp/                     # TODO：捕获后端（Python FastMCP）
│   └── screen_organ_server  #   暴露 5 轴参数模型的 capture 工具，内嵌三闸门 + 结构脱敏器
├── skills/
│   └── 纤维审计/            # TODO：SKILL.md —— EMP4 式判别树，工具 schema 设计期评审清单
└── commands/                # TODO：函级预设截图指令（slash command）
```

## 设计规范 → 插件组件 的映射

| [设计规范 v2](../文档/设计规范-v2.md) 中的东西 | 落到哪个插件组件 |
|---|---|
| 三条主干后端（`mcp_fullscreen` 快路径 / `os_window` 像素 / `chrome_tab` 结构提取） | **MCP server**：`os_window` 走 PowerShell（`CopyFromScreen`/`PrintWindow`/WinRT OCR/UIAutomation），`chrome_tab` 走 CDP/扩展，`mcp_fullscreen` 复用现成截图 |
| 5 正交参数轴（target / when / modality / validity / sink）+ 模态私有子参数 | MCP capture 工具的入参 schema（判别联合，让非法组合在契约层写不出来） |
| 入口闸门 / 有效性闸门 / 安全闸门 + 收束耦合 + ε_pool | MCP server 内部的门控管线（不暴露给模型，强制执行） |
| 不可关闭的**结构脱敏器**（剥离 password 节点 / Luhn / 高熵 token） | MCP server 内 structured/OCR 路径的强制前置 |
| 固定**回执信封**（content/source/modality/completeness/redaction/residency/budget） | MCP 工具的统一返回结构 |
| 纤维审计（σ 分辨率 vs ι 分辨率核对） | **skill**：作为给工具 schema 做设计期评审的可执行检查清单（这是理论里少数真能直接用的产物之一） |
| 函级预设指令（shot.screen / shot.active / read.ocr / watch.appear ...） | **slash commands** |

## 动工顺序（来自一致性审查）

1. **先焊 3 个致命矛盾**（见 [待焊清单](../文档/待定决策与待焊清单.md)）：ε_pool 扣费点 → "物化即消费"；补 PREFLIGHT 失败出口堵"永不静默"的缝；默认模态在寻址期分流（`by:dom` 即等价深度读取授权）。
2. 搭 `mcp_fullscreen` 快路径 + `os_window` 像素两条主干，跑通最小回执信封。
3. 接 `chrome_tab` 结构提取（注意：CDP 需应用开 `--remote-debugging-port`，普通应用默认没开——核心管线不依赖它）。
4. 把纤维审计 skill 做成独立可用件。

## 一个诚实标注

本骨架的组织语言（双闸门、最小泄露面、证据非授权、结构脱敏器）来自 [那套 AI 工具论](../理论/)，但 [检验结论](../理论/用插件检验理论-总结.md) 表明：这些是它换了名字的标准工程原则。骨架照用这些**好姿态**，但实现时按标准工程（capability security / 注入防御 / 信息流安全）落地即可，不必背理论的数学外壳。
