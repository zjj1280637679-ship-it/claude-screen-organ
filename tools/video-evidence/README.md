# video-evidence —— 路由式递归视频证据编排(薄环)

把"看视频"从**一次性黑箱总结**变成**在时间索引空间里递归检索证据**,最后输出**可验证、带出处、带未测招认**的结论。

> 定位:这是 `plugin/`(screen-organ MCP 感知核心,**不调模型**)之外的一个**编排层工具**。
> 它**组合**三层、自己保持薄:**抽取**(webvideo,确定性)⊥ **感知**(doubao,经额度网关)⊥ **编排**(本模块,控制)。
> 不是 screen-organ 的 MCP 工具、不进感知核心(守分层 P7)。

## 第一性原理(每条都落成了代码,不是口号)
- **P1 测量诚实 / 三值**:每个问只判 `answered | refuted | UNMEASURED`。缺口(如"音频语义"无 ASR 信道)**显式招认进 `uncertainties`,绝不凑答案**。步数/预算耗尽仍未闭的 gap → `UNMEASURABLE_IN_SOURCE` 终态,而非被逼造假。
- **P2 / P3 可证停机**:停机谓词**只读可核查的结构事实**(`coverage` = 有 verified 证据的问数 / 总问数;反驳跑没跑)——**绝不读模型自报的置信度**。`rank` = 未闭 gap 数严格递减 + `max_steps` 硬上限 → 可证终止(Safety=不变式 / Liveness=秩)。`confidence_post` 是**停机之后**才算的单调报告值,永不当停机输入。
- **P4 可证伪(Popper)**:高成本视觉断言必须先跑一次**主动找反例**(`disconfirm_visual`:"找出该断言不成立的一帧"),**不被反驳才 `verified=true`**。只确认不算证据。
- **P5 出处 grounding**:每条证据带 `source ∈ {pcm_measured, motion_measured, video_model_assertion}` + `verified` + `disconfirm`。模型断言永不冒充测量。
- **P6 信号基完备**:证据基 = **{视觉,音频}×{点,段} + 跨信道 SYNC(Δ)**。音频用 PCM 能量包络**确定性测量**,SYNC 用视觉运动峰 vs 音频能量峰对齐算 Δ。**纯视觉评价会漏掉声画错位——这里测得出。**
- **P7 分层**:抽取/感知/编排三层黑箱互调,环不做帧数学、不内联 prompt 到别层。

## 用法
```
# 前置:ffmpeg;额度网关跑在 127.0.0.1:8788(ARK_GATEWAY 可覆盖);PIL 可选(缺则 SYNC 标 UNMEASURED)
python video_evidence.py <webvideo产出目录(含manifest.json) | 视频文件/URL> [--task video-gen-eval] [--max-steps 14]
```
给视频文件/URL 时会先用同目录 `webvideo.py` 抽取(采帧带 `t_seconds` + 抽 16k 音轨)。

## 实证(demo2:一段 Seedance 生成的峡谷穿越,8.05s,带音轨)
- 视觉:镜头平滑、地貌一致 → 各跑一次反驳、NOT_REFUTED → `verified`,**未发现缺陷**。
- 音频:`pcm_measured` has_sound=True、峰@4.2s、静音比 0。
- **SYNC:测出 Δ=1.7s 错位**(视觉运动峰@2.5s vs 音频能量峰@4.2s,>0.5s 容差)→ 进 `next_action`。**纯视觉会说"PASS",它没漏。**
- 音频语义 → `UNMEASURED`(无 ASR 信道,招认不臆断)。coverage 4/5,7 步 < 14 上限收敛。

## 真实视频迭代(v2:镜头感知)
一段真实 AI 生成短片(14s 银面具武士,含剪辑切镜)当场逼出缺陷:原 `obj_consistency=前后一致` /
`cam_motion=无跳变` **默认视频是单一连续镜头**,对**多镜头剪辑视频**必然假阳(把正常切镜当"缺陷")。
修:①webvideo 加 `detect_shots`(ffmpeg 场景检测,best-effort,会漏暗场/渐变切点——故只当参考,不据它硬断言);
②问题拆成**镜头内**(运动/一致=生成质量)与**跨镜头 `character_identity`**(角色面容/服饰是否可辨认同一人=AI 视频真正该问的"角色漂移");
③探针/反驳措辞显式声明"剪辑切镜属正常剪辑、不算缺陷",不依赖切点检出。修后同片:六问全 `answered`、无假阳、角色身份被问出并答、音频 Δ=0.2s 对齐。
**教训**:视频质量问必须**镜头感知**;"剪辑不连续"≠"生成缺陷"。

## 综合对偶:video_synth.py(证明"理解"而非只"看见")
"看见"=观测(video_evidence 做的);"理解"=掌握**生成结构**,硬证据是**能反过来生成**(分析⊥综合的对偶)。
`video_synth.py` 把参考视频**因子化**成【不可替换 TEMPLATE:风格/打光/镜头结构/运镜/节奏】+【可替换 SLOTS:人物/场景/音频】,
render 时 **TEMPLATE 冻结、只有 SLOTS 被覆盖** → 换掉角色仍保结构。
**易错点 = 没分清可替换/不可替换;人物必然是槽**——本模块用数据结构把这条分离**显式冻结**("相似"由不可替换元素定义)。
- 感知/生成经额度网关:doubao(因子化)+ Seedance 1.5(生成,自带音轨)+ Seedream(可选 `--i2v` 先出角色图)。
- **实证**:武士片 → 角色槽换"白瓷狐面女武士" → Seedance 生成 10s **同结构**片 → video_evidence 复验:
  镜头结构复现(黑室→户外对峙)、**新角色跨镜头一致无漂移**、Δ=0.1s 声画对齐。角色换了、结构保住了 = 理解。
- 用法:`python video_synth.py <参考webvideo目录> --swap-character "新角色" [--swap-setting ..] [--i2v] [--no-generate]`
- **三模块自洽闭环**:evidence(看见)→ synth(理解=因子化+换槽生成)→ evidence(给生成片评级)。

## 残差 / 下一步(审查已识别)
- **live 帧序列录制器**:给 UI运行/Agent回放/前端调试用例(源是活屏幕,不是文件)——**唯一"从零"原语,属 screen-organ 感知核心(纯感知无模型)**,应加进 `plugin/` 而非这里。
- **音频语义信道**:接 ark 音频理解/ASR,把 `audio_semantic` 从 UNMEASURED 变可测。
- **多时间线容器**:比较型用例(Agent-vs-规格 / 生成-vs-参考 / 剪辑前后)需 `timelines[]` + 对齐/warp + `diff` 证据类型。
- **SYNC 细化**:当前是全局峰对齐,后续可做事件级对齐。

## 依赖
本机 ffmpeg/ffprobe;Python 3.12(`audioop` 标准库,3.13 起弃用);Pillow 可选;额度网关(`D:\volcengine-ark-mcp` 的 quota gateway,持 Ark key + 记账)。
