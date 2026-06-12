# Spectrum-Based Mix Template Selector

基于频谱能量比例和峰值突出度的混音模板选择工具。

这个项目的目标不是做一个完整的学术分类器，而是把“闷、糊、箱感、刺、炸、缺高频”这类主观听感转成可复查的频谱规则，方便批量筛查音频、定位问题类型，并输出可追溯的 JSON/CSV 结果。

## 本次改写点

这版代码和文档主要围绕“更稳定地选模板”和“把数据处理链路跑通”做了改写：

- 重写了三类模板的判定逻辑，让 `template_A`、`template_B`、`template_C` 尽量互斥，避免一个音频同时打到多个模板后难以解释。
- 新增 `group_ratios.body` / `group_ratios.presence`，用 `lowmid + mid` 表示主体，用 `upper + harsh` 表示存在感区域。
- 新增 `body_to_presence`，用主体能量和存在感能量的比值判断“主体偏厚/高频不足”的程度。
- 改写 `template_C` 的触发条件：只有同时满足主体占比高、存在感不足，并且主体区域有峰值，才进入 C；如果存在感已经坍塌到很低，也允许直接归入 C。
- 改写 `template_A` 的边界：A 负责普通的 muddy / boxy / 箱感问题，只在 C 的结构性失衡区域外触发。
- 改写 `template_B` 的优先级：明显的 harsh 峰值会直接优先归到 B，符合“先处理刺耳峰值”的混音优先级。
- 修正峰值检测的可信度判断：不再用频段占比过滤峰值，而是用频段最大电平是否低于 `PEAKINESS_NOISE_FLOOR_DB` 来判断该频段是否接近空频段，避免主体很重时把真实齿音/刺耳峰误屏蔽。
- 新增原始采样率和有效 Nyquist 处理：如果源文件本身是 24 kHz 这类带宽受限音频，即使加载到 44.1 kHz，也会自动丢弃超出源 Nyquist 的频段，避免空的 `air` 频段拉偏比例。
- 新增批量分析脚本 `batch_analyze_spectrum.py`，输出每个文件的模板、比例、峰值和命中规则。
- 保留 `batch_analyze.py` 作为兼容入口，内部转调 `batch_analyze_spectrum.py`。
- 新增 Excel 音频链接下载脚本 `download_reconstruct_audio.py`，可从指定列解析 `algo_audio_reconstruct_event_result.output_url` 并批量下载。
- 新增飞书页面抓取脚本 `scripts/capture_feishu_sheet.js`，用于登录后抓取页面文本、HTML、截图和相关网络响应，辅助整理飞书表里的音频数据。
- 更新 `.gitignore` 预期：本地音频、Excel、下载产物、缓存和批量报告不应提交。

## 目录结构

```text
spectral-mix-template-selector/
├── spectrum_template_analyzer.py      # 单文件频谱分析和模板分类核心逻辑
├── batch_analyze_spectrum.py          # 批量分析音频目录或文件列表
├── batch_analyze.py                   # 兼容旧入口，转调批量分析脚本
├── download_reconstruct_audio.py      # 从 Excel 指定列批量下载重建音频
├── scripts/
│   └── capture_feishu_sheet.js        # 通过 Playwright 抓取飞书表页面数据
├── requirements.txt
└── README.md
```

## 安装

建议使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Python 依赖：

```text
librosa
numpy
openpyxl
scipy
```

如果需要运行飞书抓取脚本，还需要 Node.js 和 Playwright：

```bash
npm install playwright
```

## 快速开始

分析单个音频：

```bash
python3 spectrum_template_analyzer.py path/to/audio.wav
```

批量分析目录：

```bash
python3 batch_analyze_spectrum.py path/to/audio_folder
```

默认会在输入目录下生成：

```text
spectrum_classification_results.csv
spectrum_classification_summary.json
```

从 Excel 下载重建音频后再分析：

```bash
python3 download_reconstruct_audio.py "/path/to/反馈数据.xlsx"
python3 batch_analyze_spectrum.py downloads/reconstruct_audio
```

## 单文件分析

```bash
python3 spectrum_template_analyzer.py path/to/audio.mp3 --sr 44100 --top-db 35
```

常用参数：

```text
--sr 44100                 目标采样率，默认 44100
--n-fft 4096               STFT FFT size，默认 4096
--hop-length 1024          STFT hop length，默认 n_fft / 4
--top-db 40                静音裁剪阈值，默认 40
--no-trim                  关闭静音裁剪
--peak-prominence-db 6     峰值 prominence 阈值，默认 6 dB
```

输出为 JSON，核心字段包括：

```json
{
  "sample_rate": 44100,
  "native_sample_rate": 24000,
  "effective_nyquist_hz": 12000.0,
  "dropped_bands": ["air"],
  "ratios": {
    "lowmid": 0.31,
    "mid": 0.17,
    "upper": 0.22,
    "harsh": 0.18
  },
  "group_ratios": {
    "body": 0.48,
    "presence": 0.40
  },
  "body_to_presence": 1.2,
  "peakiness_upper": 7.2,
  "peakiness_harsh": 5.8,
  "peakiness_sib": 6.0,
  "classification": {
    "label": "template_A",
    "label_name": "Muddy / Boxy Vocal"
  }
}
```

`dropped_bands` 用来提示哪些频段因为源文件有效带宽不足被移出了比例分母。例如 24 kHz 源文件的 Nyquist 是 12 kHz，`air` 频段没有真实内容，会被丢弃。

## 批量分析

分析目录：

```bash
python3 batch_analyze_spectrum.py downloads/reconstruct_audio
```

递归扫描：

```bash
python3 batch_analyze_spectrum.py downloads/reconstruct_audio --recursive
```

指定输出路径：

```bash
python3 batch_analyze_spectrum.py downloads/reconstruct_audio \
  --output-csv results.csv \
  --summary-json summary.json
```

只分析前几个文件，适合试跑：

```bash
python3 batch_analyze_spectrum.py downloads/reconstruct_audio --limit 5
```

默认支持的音频后缀：

```text
.wav, .mp3, .flac, .m4a, .aac, .aiff, .aif
```

也可以自定义：

```bash
python3 batch_analyze_spectrum.py audio_dir --extensions wav,mp3
```

CSV 每行对应一个音频文件，包含：

- 文件名和路径
- 最终分类 `classification`
- 模板名 `label_name`
- 各频段能量比例 `*_ratio`
- `body_to_presence`
- `peakiness_upper` / `peakiness_harsh` / `peakiness_sib`
- 每个模板的普通命中数、强命中数、命中规则名

summary JSON 包含：

- 总文件数、成功数、失败数
- 各模板分类计数
- 失败文件和错误信息
- harsh / upper / sib 峰值最高的前 10 个文件

## 从 Excel 下载重建音频

`download_reconstruct_audio.py` 用于从 Excel 某一列中解析音频 URL。默认读取 Q 列，从 `algo_audio_reconstruct_event_result.output_url` 提取链接。

先 dry run 看会解析到哪些 URL：

```bash
python3 download_reconstruct_audio.py "/path/to/反馈数据.xlsx" --dry-run
```

正式下载：

```bash
python3 download_reconstruct_audio.py "/path/to/反馈数据.xlsx"
```

默认输出目录：

```text
downloads/reconstruct_audio/
```

默认 manifest：

```text
downloads/reconstruct_audio/download_manifest.csv
```

文件名会带上 Excel 行号，便于回查：

```text
row_0008_xxx.wav
```

常用参数：

```text
--sheet SHEET              指定工作表，默认 active sheet
--column Q                 指定解析列，默认 Q
--start-row 2              起始数据行，默认 2
--key KEY                  JSON 对象 key，默认 algo_audio_reconstruct_event_result
--url-field output_url     URL 字段名，默认 output_url
--output-dir DIR           下载目录
--manifest FILE            manifest CSV 路径
--dry-run                  只打印 URL，不下载
--overwrite                已存在文件也重新下载
--limit 5                  只处理前 5 个 URL
--timeout 60               单次下载超时秒数
--retries 2                每个 URL 的重试次数
--sleep 0.2                下载间隔秒数
```

## 抓取飞书表

`scripts/capture_feishu_sheet.js` 会打开一个持久化 Chrome profile，访问脚本内置的飞书 wiki/sheet 页面，并保存页面文本、HTML、截图和相关网络响应。

运行：

```bash
node scripts/capture_feishu_sheet.js
```

输出目录：

```text
downloads/feishu_e5973a_capture/
├── page_text.txt
├── page_title.txt
├── page_url.txt
├── page_html.html
├── page.png
└── responses/
```

注意事项：

- 脚本默认使用 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`。
- 如果飞书要求登录，需要在弹出的 Chrome 窗口里手动登录。
- 脚本会运行最多 5 分钟，页面加载到可识别内容后再额外抓取约 30 秒。
- 这是数据辅助抓取脚本，不是核心分类逻辑。

## 频段定义

分析时会先把音频转 mono，按需裁掉静音，再计算 STFT power spectrum。

| 名称 | 频率范围 | 含义 |
| --- | --- | --- |
| `sub` | 20-80 Hz | 次低频 |
| `low` | 80-180 Hz | 低频基础 |
| `lowmid` | 180-500 Hz | 低中频，闷、糊、厚的主要区域 |
| `mid` | 500-1000 Hz | 中频主体，鼻音/箱感常见区域 |
| `upper` | 1-4 kHz | 存在感、清晰度、主体峰值 |
| `harsh` | 4-8 kHz | 刺、硬、毛、齿音前段 |
| `sib` | 8-12 kHz | 齿音、嘶声 |
| `air` | 12-20 kHz | 空气感 |

每个频段会统计 power 能量并换算为占比：

```text
band_ratio = band_energy / sum(active_band_energy)
```

其中 `active_band_energy` 会排除源文件有效 Nyquist 以上的空频段。

## 核心指标

```text
body = lowmid + mid
presence = upper + harsh
body_to_presence = body_energy / presence_energy
```

峰值突出度使用 `scipy.signal.find_peaks` 的 prominence，分别计算：

```text
peakiness_upper
peakiness_harsh
peakiness_sib
```

峰值检测有一个噪声地板保护：

```text
PEAKINESS_NOISE_FLOOR_DB = -60.0
```

如果某个频段的最大电平都低于该值，就认为该频段没有可靠内容，对应 `peakiness_*` 置为 0。

## 模板规则

规则定义在 `spectrum_template_analyzer.py` 的 `CLASSIFICATION_RULES` 和相关常量中。

### template_A: Muddy / Boxy Vocal

目标听感：

```text
厚、闷、糊、鼻、箱感、主体偏暗
```

A 代表普通的主体偏厚、低中频偏多、箱感或糊感。它会避开 C 的结构性失衡区域，也就是说：

- 如果只是低中频多、偏暗、偏箱感，归 A。
- 如果已经出现主体极重、存在感严重不足、主体峰值异常，才交给 C。

主要规则：

```text
lowmid_ratio_high       lowmid >= 0.28
mid_ratio_high          mid >= 0.20
body_to_presence_high   body_to_presence >= 1.15
```

强规则：

```text
very_high_lowmid        lowmid >= 0.34
strong_body_to_presence body_to_presence >= 1.35
```

### template_B: Peaky / Harsh Vocal

目标听感：

```text
炸、刺、硬、毛、金属感、某些字突然冲
```

B 只处理高频/存在感区域的问题，不因为低中频厚而触发。高频比例过高或峰值明显时进入 B。

主要规则：

```text
upper_ratio_high        upper >= 0.26
harsh_ratio_high        harsh >= 0.16
sib_ratio_high          sib >= 0.12
upper_peak_spiky        peakiness_upper >= 9 dB
harsh_peak_spiky        peakiness_harsh >= 9 dB
```

强规则：

```text
very_spiky_harsh        peakiness_harsh >= 12 dB
very_high_harsh_ratio   harsh >= 0.22
very_high_sib_ratio     sib >= 0.18
```

额外优先级：

```text
DECISIVE_HARSH_PEAK_DB = 12.0
```

只要 `peakiness_harsh >= 12 dB`，最终模板直接选 B。

### template_C: Imbalanced / Heavy Low-Mid

目标听感：

```text
闷、糊、头重脚轻、缺高频、不通透
```

C 代表结构性失衡：主体区域占比过高，存在感区域严重不足，并且主体/upper 区域有异常峰值；如果 presence 已经低到接近坍塌，也会归 C。

C 的入口条件：

| 条件 | 阈值 | 常量 |
| --- | --- | --- |
| 主体占比高 | `group_ratios.body >= 0.70` | `C_BODY_DOMINANT_RATIO` |
| 存在感不足 | `group_ratios.presence <= 0.10` | `C_PRESENCE_STARVED_RATIO` |
| 存在感坍塌 | `group_ratios.presence <= 0.04` | `C_PRESENCE_COLLAPSED_RATIO` |
| 主体峰值明显 | `peakiness_upper >= 9 dB` | `C_BODY_PEAK_DB` |

入口逻辑：

```text
body_heavy and presence_starved and (has_body_peak or presence_collapsed)
```

主要规则：

```text
extreme_lowmid             lowmid >= 0.55
very_high_body_to_presence body_to_presence >= 5.0
band_limited_highs         upper <= 0.06 and harsh <= 0.005
body_peak_spiky            peakiness_upper >= 9 dB
```

强规则：

```text
mega_lowmid                lowmid >= 0.70
extreme_body_to_presence   body_to_presence >= 10.0
very_spiky_body_peak       peakiness_upper >= 12 dB
```

## 决策流程

最终标签选择逻辑：

1. 如果 `peakiness_harsh >= 12 dB`，直接选择 `template_B`。
2. 否则 A/B/C 平级竞争。
3. 如果只有一个模板有 `strong_hits`，该模板胜出。
4. 否则按 `(hits, strong_hits)` 排序。
5. 完全打平时按 `template_A > template_B > template_C` 的顺序兜底。

`minimum_hits` 现在主要作为诊断字段 `qualified` 输出，不再直接阻止模板成为最终分类。

## 调参入口

最常改的阈值都在 `spectrum_template_analyzer.py`：

| 名称 | 作用 |
| --- | --- |
| `PEAKINESS_NOISE_FLOOR_DB` | 判断峰值所在频段是否有真实内容 |
| `DECISIVE_HARSH_PEAK_DB` | harsh 峰值直接归 B 的阈值 |
| `C_BODY_DOMINANT_RATIO` | C 所需的主体占比 |
| `C_PRESENCE_STARVED_RATIO` | C 所需的存在感不足阈值 |
| `C_PRESENCE_COLLAPSED_RATIO` | presence 坍塌时跳过主体峰值要求的阈值 |
| `C_BODY_PEAK_DB` | C 所需的主体峰值阈值 |
| `C_BODY_PEAK_STRONG_DB` | C 的强主体峰值阈值 |
| `CLASSIFICATION_RULES` | A/B/C 的普通规则和强规则 |

当某个样本分类不符合听感时，建议先跑：

```bash
python3 spectrum_template_analyzer.py FILE.wav
```

重点看这些字段：

```text
ratios
group_ratios
body_to_presence
peakiness_upper
peakiness_harsh
peakiness_sib
classification.template_*.hit_rules
classification.template_*.strong_rules
```

## 注意事项

- 默认目标采样率是 44100，因此可以覆盖到 20 kHz 的 `air` 频段。
- 源文件如果本身带宽较低，会按源文件 Nyquist 自动裁掉不可用高频段。
- 静音裁剪是简单能量阈值裁剪，不等于人声活动检测。
- 更建议对干声、人声 stem 或明确的人声片段做分析；整首歌混合音频会受伴奏影响。
- 规则阈值是工程经验型，需要结合具体数据集、模型、语言、曲风继续校准。
- 本地音频、Excel、下载目录和批量报告通常包含大文件或隐私数据，不建议提交到 git。
