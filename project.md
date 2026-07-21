# 项目上下文：Spectral Mix Template Selector

> 本文档是后续需求修改的首要上下文入口。最后核对日期：2026-07-17。
>
> 后续处理需求时，先读本文件，再读用户指定的 `xxprd.MD`，然后按本文的“需求定位索引”只打开相关代码。除非需求跨越架构边界、文档与代码不一致，或局部信息不足，不要重新通读整个仓库。

## 1. 项目目标

本项目是一组命令行工具，用频谱能量分布和谱峰特征分析人声/音频，并从三种混音处理模板中选出一个：

- `template_A`：Muddy / Boxy Vocal，普通的厚、闷、糊、鼻、箱感。
- `template_B`：Peaky / Harsh Vocal，炸、刺、硬、毛、金属感或局部高频冲出。
- `template_C`：Imbalanced / Heavy Low-Mid，主体极重、存在感严重不足、头重脚轻。

项目不是训练型机器学习分类器，没有模型文件、数据库或服务端。分类结果完全由 Python 中可读、可追踪的工程规则产生，主要用于批量筛查、规则调参和问题样本回归。

## 2. 后续需求处理约定

每次收到新需求时按以下顺序工作：

1. 阅读根目录 `project.md`，建立项目结构和影响面上下文。
2. 阅读用户指定的 `xxprd.MD`。如果用户只说“按 PRD 修改”而未指定文件，先在根目录查找 `*prd.MD` / `*prd.md`；只有唯一、明确的需求文件时直接使用，存在多个候选且无法判断时再向用户确认。
3. 从本文第 10 节“需求定位索引”确定要读的代码，只阅读目标文件及其直接依赖、调用方和相关测试。
4. 修改前检查 `git status --short` 和目标文件 diff，保留用户已有改动，不覆盖、不回滚无关内容。
5. 实现需求并执行第 11 节中与改动匹配的最小验证；分类规则变化必须做基线差异检查。
6. 如果架构、模块职责、入口、输出字段、分类规则、依赖、测试方法或已知限制发生变化，同步更新 `project.md`。纯阈值微调且本文已有准确入口说明时，无需重写整份文档。

只有以下情况才扩大阅读范围或重新盘点全仓：

- PRD 改变端到端数据链路或新增跨模块能力；
- 公共输出字段、命令行参数、频段/指标定义发生变化；
- 实际代码与本文描述不一致；
- 目标函数的调用方或数据消费者不明确；
- 局部验证暴露出跨模块回归。

## 3. 技术栈与运行形态

- Python 3 命令行脚本，无 Python package/build 配置。
- 核心依赖：`librosa`、`numpy`、`scipy`、`openpyxl`，记录在 `requirements.txt`，目前未锁版本。
- Python 标准库用于参数解析、JSON/CSV、路径、HTTP 下载和重试。
- 辅助抓取脚本使用 Node.js CommonJS + `playwright`；仓库没有 `package.json`，需单独安装 Playwright。
- 没有 Web 服务、GUI、数据库、CI 配置和正式测试框架配置。

## 4. 目录与文件职责

```text
spectral-mix-template-selector/
├── project.md                       # 本文件：架构和后续需求导航
├── README.md                        # 面向使用者的安装、命令和规则说明
├── requirements.txt                 # 未锁版本的 Python 运行依赖
├── spectrum_template_analyzer.py    # 核心：频谱分析、指标计算、A/B/C 分类、单文件 CLI
├── batch_analyze_spectrum.py        # 批量扫描、调用核心分析、生成 CSV 和汇总 JSON
├── batch_analyze.py                 # 旧批量入口的兼容包装器
├── download_reconstruct_audio.py    # 从 xlsx 单元格提取 URL、下载音频、生成 manifest
├── scripts/
│   └── capture_feishu_sheet.js      # 登录飞书后抓页面和相关网络响应的辅助脚本
└── tests/
    ├── capture_baseline.py          # 在线样本标签快照/对比工具，不是 pytest 用例
    ├── baseline.json                # 当前回归样本的标签、命中规则和关键指标快照
    └── test_classify_rules.py       # 可移植的分类决策测试，合成 metrics，不依赖音频
```

被 `.gitignore` 排除的本地产物主要包括：虚拟环境、缓存、音频、xlsx、`downloads/`、下载 manifest、批量 CSV/JSON。真实数据集不在仓库中。

## 5. 总体架构与数据流

项目可分为四层：

```text
数据获取层
  飞书页面 ── capture_feishu_sheet.js ──> 页面文本/HTML/截图/响应
  Excel 文件 ─ download_reconstruct_audio.py ─> 本地音频 + manifest.csv
                                                     │
分析与决策层                                         ▼
  单个音频 ─ spectrum_template_analyzer.analyze_audio ─> 指标字典
                                                     │
                                                     ▼
             spectrum_template_analyzer.classify ───> A/B/C 分类及命中规则
                                                     │
输出与回归层                                         ▼
  单文件 CLI ─> 完整 JSON
  batch_analyze_spectrum.py ─> 明细 CSV + 汇总 JSON
  tests/capture_baseline.py ─> baseline.json / 标签差异报告
```

直接代码依赖关系：

- `batch_analyze.py` 只导入并调用 `batch_analyze_spectrum.main`。
- `batch_analyze_spectrum.py` 从核心模块导入 `BANDS`、`analyze_audio`、`numpy_json_default`。
- `tests/capture_baseline.py` 导入整个 `spectrum_template_analyzer` 模块并调用 `analyze_audio`。
- 下载脚本和飞书抓取脚本不依赖频谱分析模块，它们只负责准备数据。

## 6. 核心分析模块

### 6.1 入口与处理步骤

`spectrum_template_analyzer.py` 的公共核心入口是：

```python
analyze_audio(
    audio_path,
    sr=44100,
    n_fft=4096,
    hop_length=None,       # 实际默认 n_fft // 4
    top_db=40.0,
    trim=True,
    peak_prominence_db=6.0,
) -> dict
```

处理顺序如下：

1. 用 `librosa.get_samplerate` 读取源采样率，再用 `librosa.load(..., mono=True)` 转单声道并重采样到目标采样率。
2. 默认用 `librosa.effects.split` 找非静音区间并拼接；这不是人声活动检测。
3. 取 `min(目标采样率, 源采样率) / 2` 作为有效 Nyquist；超出源带宽的频段被标记到 `dropped_bands`，不进入总能量分母。
4. 执行 STFT，计算线性功率谱 `abs(stft) ** 2`。
5. 计算各频段总能量、频段占比、组合占比和 `body_to_presence`。
6. 对时间维取平均功率谱并转相对 dB，计算 upper/harsh/sib 的 peak prominence。
7. 若频段最大值不高于 `PEAKINESS_NOISE_FLOOR_DB = -60 dB`，将该频段 peakiness 清零，避免把编解码残留当成峰值。
8. 在线性平均功率谱上计算 `flatness_upper`。
9. 调用 `classify(metrics)`，把分类详情放入返回字典的 `classification` 字段。

模块导入前会把 `NUMBA_CACHE_DIR` 默认设置到系统临时目录，以规避部分 librosa/numba 组合无法向 site-packages 邻近目录写缓存的问题。

### 6.2 频段定义

| 字段 | 范围 | 主要语义 |
| --- | ---: | --- |
| `sub` | 20–80 Hz | 次低频 |
| `low` | 80–180 Hz | 低频基础 |
| `lowmid` | 180–500 Hz | 闷、糊、厚的主要区域 |
| `mid` | 500–1000 Hz | 中频主体、鼻音/箱感 |
| `upper` | 1–4 kHz | 存在感、清晰度、主体谱峰 |
| `harsh` | 4–8 kHz | 刺、硬、毛 |
| `sib` | 8–12 kHz | 齿音、嘶声 |
| `air` | 12–20 kHz | 空气感 |

区间均为左闭右开。源 Nyquist 落在频段内部时，该频段上界会被截断；整个频段位于 Nyquist 之上时才会进入 `dropped_bands`。

### 6.3 指标定义

- `band_energies[name]`：某频段所有频率 bin 和所有帧的线性功率之和。
- `ratios[name]`：频段能量 / 所有有效频段能量。
- `group_ratios.body`：`lowmid_ratio + mid_ratio`。
- `group_ratios.presence`：`upper_ratio + harsh_ratio`。
- `body_to_presence`：`(lowmid_energy + mid_energy) / (upper_energy + harsh_energy)`；分母为 0 时返回 `None`。
- `peakiness_upper` / `peakiness_harsh` / `peakiness_sib`：平均 dB 频谱中，满足 prominence 门槛的最高两个峰的 prominence 均值；没有可靠峰时为 0。
- `flatness_upper`：upper 频段线性平均功率的几何均值 / 算术均值，理论范围 0–1；频段为空或不足 3 个正功率 bin 时返回 1.0。低值表示能量更集中于少数窄峰。

当前接入状态：`flatness_upper` 已由 `analyze_audio` 输出，但尚未被 `CLASSIFICATION_RULES` 使用，也未写入批量 CSV、汇总 JSON 或 `tests/baseline.json`。任何“用谱平坦度识别电音/金属环”的需求都需要同时评估这些消费端。

**这是有意保留的在建特征，不是悬空死代码，不得以“清理”为由删除。** 它针对
peakiness 和频段占比都覆盖不到的盲区：AI 分离产生的电音/金属环 vs 普通闷糊人声
（汤刚黄昏 vs 乐园在 peakiness 和占比上几乎一致，差别在 upper 频段能量是否被少数
窄尖峰霸占）。1.0 PRD 明确将其定为诊断特征，仅因当前没有标注数据可校准阈值；
待有标注样本后再评估接入分类。

### 6.4 分类规则与优先级

规则集中在 `CLASSIFICATION_RULES` 及其邻近常量/territory 函数中。

- A 普通规则：`lowmid >= 0.28`、`mid >= 0.20`、`body_to_presence >= 1.15`，且必须在 `in_a_territory`。
- A 强规则：`lowmid >= 0.34`、`body_to_presence >= 1.35`，且必须在 `in_a_territory`。
- B 普通规则：`upper >= 0.26`、`harsh >= 0.16`、`sib >= 0.12`、`upper peakiness >= 9`、`harsh peakiness >= 9`。
- B 强规则：`harsh peakiness >= 12`、`harsh >= 0.22`、`sib >= 0.18`。
- C territory：`body >= 0.70 and presence <= 0.10 and (upper peakiness >= 9 or presence <= 0.04)`。
- C 普通规则：`lowmid >= 0.55`、`body_to_presence >= 5`、`upper <= 0.06 and harsh <= 0.005`、`upper peakiness >= 9`，全部还需位于 C territory。
- C 强规则：`lowmid >= 0.70`、`body_to_presence >= 10`、`upper peakiness >= 12`，全部还需位于 C territory。
- `in_a_territory` 等于“不在 C territory”，使 A/C 的结构区域互斥。

B 不按规则命中数判定，改用 `b_evidence_groups(metrics)` 返回的独立证据组
（`hf_energy`、`hf_peak`、`hf_energy_multiband`、`dual_peak_strong`），
以消除同一指标重复计票和 A/B/C 规则数量不对等的问题：

- B 达标：`>= 2` 个证据组；B 强成立：`>= 3` 个证据组或命中 `dual_peak_strong`。
- 峰值类证据（`hf_peak`、`dual_peak_strong`）要求
  `group_ratios.presence >= B_PRESENCE_FLOOR_RATIO (0.10)`。
  presence 坍塌时高频能量太少，峰值只说明高频坍塌，不说明刺耳。
- `STRONG_HARSH_PEAK_DB (12)` 现在只是 B 的强证据，不再直接决定标签。

最终选择以 `qualified` 为真实门槛，顺序是：

1. C 达标且 B 未强成立 → C（单个 harsh 峰不能推翻已达标的 C）。
2. C 达标且 B 强成立 → B，C 记入 `secondary_issues`。
3. B 强成立（C 未达标）→ B。
4. A 达标 → A，普通 B 记入 `secondary_issues`。
5. 只有 B 达标 → B。
6. 无模板达标 → 回落 A，且 `fallback=true`、`confidence=low`。

`minimum_hits=2` 现在是 A/C 的真实门槛（B 用证据组门槛）；未达标模板不会成为最终
标签，回落路径除外。

### 6.5 核心辅助函数

- `strip_silence`：拼接 librosa 判断出的非静音区间。
- `band_mask` / `band_power`：频段选择和能量求和。
- `band_peakiness`：用 `scipy.signal.find_peaks` 计算峰值 prominence。
- `band_spectral_flatness`：计算指定频段谱平坦度。
- `in_c_territory` / `in_a_territory`：A/C 结构边界。
- `classify`：统计规则命中并完成最终标签选择。
- `numpy_json_default`：把 NumPy scalar 转为 JSON 标量；非有限浮点转 `null`。
- `parse_args` / `main`：单文件命令行入口，向 stdout 输出完整 JSON。

## 7. 周边功能模块

### 7.1 批量分析

文件：`batch_analyze_spectrum.py`；兼容入口：`batch_analyze.py`。

主要流程：

1. `collect_audio_files` 接收文件/目录，按扩展名过滤，可选递归，并排序去重。
2. `analyze_file` 对每个文件调用 `analyze_audio`，把嵌套结果扁平化为 CSV 行。
3. 单文件异常会被捕获并写入错误列表，不中止整批处理。
4. 有成功结果时 `write_csv` 写明细；即使全部失败也会 `write_summary`。
5. `build_summary` 统计标签数量、失败文件，以及 harsh/upper/sib peakiness 前 10。

默认支持 `.wav/.mp3/.flac/.m4a/.aac/.aiff/.aif`。单目录输入时输出到该目录；其他输入形式默认输出到当前工作目录。

当前 CSV 包含文件信息、最终分类、`body_to_presence`、三项 peakiness、八个频段占比以及各模板命中详情。它不包含完整 `band_energies`、`group_ratios`、采样率/Nyquist、`dropped_bands` 或 `flatness_upper`。

### 7.2 Excel URL 解析与音频下载

文件：`download_reconstruct_audio.py`。

- 默认读取活动工作表 Q 列，从第 2 行开始。
- 默认提取 JSON 对象 `algo_audio_reconstruct_event_result.output_url`。
- `extract_url` 先尝试标准 JSON；失败时 `regex_extract_url` 容错解析局部 JSON/文本 URL。
- `load_sheet` 会重置 worksheet dimensions，兼容导出文件错误标记为 `A1` 的情况。
- 文件名格式为 `row_XXXX_<URL文件名>`，并过滤不安全字符。
- 下载基于 `urllib.request`，支持超时、重试、间隔、跳过已有文件和覆盖。
- 每次正式运行生成 manifest CSV，记录源行、URL、文件、状态、字节数和错误。
- `--dry-run` 只列 URL，不创建输出目录和 manifest。

### 7.3 飞书页面抓取

文件：`scripts/capture_feishu_sheet.js`。

它通过 Playwright 启动持久化、非 headless 的本机 Chrome，允许人工登录固定的飞书页面，并保存：

- 页面正文、标题、URL、HTML、整页截图；
- content-type 和内容看起来相关的网络响应文本。

目标 URL、输出目录、持久化 profile 路径和 macOS Chrome 可执行路径均硬编码。脚本最长轮询约 5 分钟，识别到相关页面内容后再等待 30 秒。该脚本是一次性数据辅助工具，不参与分类运行链路。

### 7.4 回归基线

文件：`tests/capture_baseline.py`、`tests/baseline.json`。

基线工具从另一个本地工程的 `row*_analysis.json` 读取音频路径，并额外加入一个本机 Downloads 中的 badcase。它记录最终标签、各模板命中规则/强规则/qualified，以及规则关注的指标和所有 ratios/group ratios。

- 无参数运行：重新分析现有样本并覆盖 `baseline.json`。
- `--check`：只比较保存值与当前值的最终 `label`，不比较指标或命中规则，也不会因标签变化返回非零退出码。
- 外部样本路径是绝对路径，换机器或缺少样本时会静默跳过，因此当前不是可移植、完整的自动化测试。
- `RULE_METRICS` 当前只有 `peakiness_upper`、`peakiness_harsh`、`body_to_presence`，未包含 `peakiness_sib` 和 `flatness_upper`。

`baseline.json` 当前包含 `row2`–`row9` 和 `badcase_codec_out_8a43de53` 共 9 个样本。修改分类规则前后应保留并审阅标签 diff；如果需求明确要求某些样本变更，应在 PRD/提交说明中列出预期变化。

基线经过验证：用当前分类器重跑真实音频，与 `music_auto_mix1` 存档的生产 label
对比 8/8 一致。样本分布高度偏斜（A×6、B×1、C×1）：**row3 是唯一的 C，row6 是唯一的
B**，改动 B/C 规则时必须重点审阅这两个。这也是本项目不采用加权评分的原因——
样本量不足以支撑可信权重。

`tests/test_classify_rules.py` 是可移植补充：用合成 metrics 覆盖决策优先级、
证据组门槛、阈值边界、复合问题（B+C、B+A）、无命中回落和输出契约，
不依赖本机音频或绝对路径。分类逻辑变更应同时运行它和基线 `--check`。

## 8. 对外接口与输出契约

### 单文件分析输出

`analyze_audio` / 单文件 CLI 返回这些顶层字段：

```text
audio_path
sample_rate
native_sample_rate
effective_nyquist_hz
dropped_bands
n_fft
hop_length
trim_silence
top_db
band_energies
ratios
group_ratios
body_to_presence
peakiness_upper
peakiness_harsh
peakiness_sib
flatness_upper
classification
```

`classification` 包含 `label`、`label_name`、`selection_reason`、`fallback`、
`confidence`、`secondary_issues`、`b_evidence_groups`，以及 `template_A/B/C`。
每个模板详情包含 `name`、`tags`、`hits`、`hit_rules`、`strong_hits`、`strong_rules`、
`qualified`。

`fallback=true` 表示没有任何模板达标、回落到维护中的 A 链，此时 `confidence=low`；
下游据此可区分「确信是 A」和「什么都没测到」。`secondary_issues` 供下游动态齿音 /
HF 保护使用，不应叠加固定 EQ。

这些字段同时被批量脚本、基线脚本或人工分析使用。重命名、删除、改变类型时必须检查所有消费者并更新 README/project 文档。

### 命令行入口

```bash
python3 spectrum_template_analyzer.py AUDIO [分析参数]
python3 batch_analyze_spectrum.py PATH... [批量和分析参数]
python3 batch_analyze.py PATH... [同上，兼容入口]
python3 download_reconstruct_audio.py FILE.xlsx [解析/下载参数]
python3 tests/capture_baseline.py [--check]
node scripts/capture_feishu_sheet.js
```

详细参数以各脚本 `parse_args` 和 `--help` 为准；面向用户的示例在 `README.md`。

## 9. 设计边界与已知风险

- 分类阈值是项目/数据集相关的经验规则，不是跨场景保证有效的统计模型。
- 推荐分析干声、人声 stem 或明确人声片段；完整混音会被伴奏频谱显著影响。
- 静音裁剪会拼接所有非静音段，无法排除伴奏、噪声或非人声事件。
- 频段能量占比只对 20 Hz–20 kHz 中的有效频段归一化；不同源采样率/带宽仍可能影响样本可比性。
- `classify` 直接按固定字典结构读指标；增加新规则时要保证所有调用路径都提供所需字段，必要时为手工构造 metrics 的测试补默认值。
- `flatness_upper` 处于“已计算、未决策/未批量输出/未回归”的半接入状态。
- requirements 未锁版本，librosa/numba/scipy 的版本变化可能造成数值或运行环境差异。
- 批量脚本全部成功行为与“全部失败”行为不同：没有成功行时不会生成 CSV，但仍生成 summary JSON。
- 下载失败时，底层一次性读取完整响应；如果写文件前失败不会落盘，但没有内容类型、状态码或音频有效性校验。
- 飞书抓取依赖固定页面、本机 Chrome 路径、人工登录和网页结构，不能作为稳定自动化接口。
- 基线测试依赖本机绝对路径，缺样本会改变捕获集合；且 `--check` 只报告标签变化，不作为严格 CI 门禁。

## 10. 需求定位索引

| PRD/修改类型 | 首先阅读 | 可能联动 | 最小验证 |
| --- | --- | --- | --- |
| 调整 A/B/C 阈值、territory、优先级 | `spectrum_template_analyzer.py` 的常量、`CLASSIFICATION_RULES`、`classify` | `README.md`、`tests/baseline.json`、本文件 | 语法检查 + 基线 `--check` + 代表样本 JSON |
| 新增频段或修改频段边界 | 核心模块的 `BANDS`、`analyze_audio` | 批量 CSV、基线、README、所有按频段名取值处 | 搜索字段消费者 + 单/批量试跑 |
| 新增/修改指标（如 flatness） | 指标函数、`analyze_audio` 返回值 | `classify`、`analyze_file`、`build_summary`、`RULE_METRICS`、文档 | 数值边界测试 + 输出字段检查 + 基线 diff |
| 修改单文件 CLI 参数或 JSON | 核心模块 `parse_args/main/analyze_audio` | 批量参数透传、README | `--help` + 单样本运行 |
| 修改批量扫描、CSV、汇总 | `batch_analyze_spectrum.py` | 核心返回字段、README | 临时目录小样本批量运行，检查 CSV/JSON |
| 保持/废弃旧批量命令 | `batch_analyze.py` | `batch_analyze_spectrum.py`、README | 两个入口 `--help` / 同参数试跑 |
| 修改 Excel 字段解析 | `extract_url`、`regex_extract_url`、`iter_urls` | CLI 默认值、README | 用最小 xlsx/字符串样例测试合法和非标准 JSON |
| 修改下载策略、命名、manifest | `safe_filename_from_url`、`download_one`、下载 `main` | `.gitignore`、README | `--dry-run` + 本地/受控 URL 测试 |
| 修改飞书数据抓取 | `scripts/capture_feishu_sheet.js` | README、忽略目录 | JS 语法检查；实际登录抓取需用户环境 |
| 修改回归策略/样本集 | `tests/capture_baseline.py` | `baseline.json`、外部样本路径 | capture/check 两种模式并人工审阅 diff |
| 安装、依赖或运行环境 | `requirements.txt`、各模块 import | README、numba cache 处理、Playwright 安装 | 新环境安装 + 各 CLI `--help` |

常用定位搜索：

```bash
rg "CLASSIFICATION_RULES|in_c_territory|b_evidence_groups|STRONG_HARSH" .
rg "flatness_upper|peakiness_|body_to_presence|group_ratios" .
rg "analyze_audio|classification|label_name" .
rg "extract_url|output_url|download_manifest" .
```

## 11. 验证策略

按风险从小到大选择，不需要每次都运行全套：

1. 所有 Python 变更至少做语法编译：

   ```bash
   python3 -m py_compile spectrum_template_analyzer.py batch_analyze_spectrum.py batch_analyze.py download_reconstruct_audio.py tests/capture_baseline.py
   ```

2. CLI 参数或入口变化检查对应 `--help`。
3. 指标/分类变化用一个代表音频运行单文件分析，核对 JSON 类型、有限值、命中规则和最终标签。
4. 批量输出变化用少量音频检查 CSV 表头、行值、summary 统计和失败记录。
5. 分类规则变化运行：

   ```bash
   python3 tests/test_classify_rules.py
   python3 tests/capture_baseline.py --check
   ```

   先确认外部样本都存在，再人工判断每个标签变化是否符合 PRD；不要只看命令退出码。
6. 更新基线只能在确认变化为需求预期后执行无参数 capture，不能为了消除 diff 直接覆盖。
7. 文档或输出字段变化最后用 `rg` 检查旧字段名、旧阈值和过期示例。

当前仓库没有 pytest 单元测试。若 PRD 涉及核心数学函数、边界值、URL 容错解析或决策优先级，优先补充可移植的测试数据/单元测试，而不是继续只依赖本机在线样本。

## 12. 文档维护规则

- `project.md` 面向后续开发者/编码代理，强调真实架构、依赖、影响面和验证方式。
- `README.md` 面向工具使用者，强调安装、命令、参数、输出和分类含义。
- `xxprd.MD` 是单次需求来源，不应把未实现的 PRD 描述提前写成当前架构事实。
- 代码是最终事实来源；一旦发现本文与代码不一致，应在完成需求时同步修正文档。
- 保持本文为可导航的稳定索引，不记录临时调试过程、个人机器上的隐私 URL/音频内容或每次提交流水账。
