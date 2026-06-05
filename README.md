# Spectrum-Based Mix Template Selector

该脚本通过分析音频的频段能量比例、`body_to_presence` 比值，以及
`upper`/`harsh` 频段的峰值突出度，判断音频更接近哪类混音问题特征，
从而选择对应的混音处理模板。

It is a lightweight, rule-based audio feature analyzer for selecting mix
templates from spectral balance and peak-prominence cues.

## Project Value

This project is useful as a practical engineering tool rather than a finished
academic benchmark.

- Batch triage audio clips before manual mixing or model-based post-processing.
- Convert subjective feedback such as muddy, boxy, harsh, sharp, or sibilant
  into inspectable spectral rules.
- Keep template decisions reproducible by writing all ratios, peakiness values,
  and hit rules to JSON/CSV.
- Tune the thresholds for a specific product, vocal model, language, genre, or
  mix style.

For research use, the script can be a baseline feature extractor or rule-based
classifier, but academic value depends on validating the thresholds against a
labeled dataset and reporting accuracy, agreement, and failure cases.

## Install

```bash
pip install -r requirements.txt
```

## Quick Start

Analyze one audio file:

```bash
python3 spectrum_template_analyzer.py path/to/audio.wav
```

Batch analyze a directory:

```bash
python3 batch_analyze_spectrum.py path/to/audio_folder
```

This writes:

```text
path/to/audio_folder/spectrum_classification_results.csv
path/to/audio_folder/spectrum_classification_summary.json
```

## Auto Mix Bridge

This repository owns the spectrum analyzer. The actual Faust/template renderer
lives in the sibling project:

```text
D:\code\music_auto_mix1\music_auto_mix1
```

For convenience, `scripts/auto_template_mix.py` in this repo is a thin bridge
that forwards to the renderer project and uses this repo's
`spectrum_template_analyzer.py` by default.

Current production renders should enter through `scripts/render_pipeline.py`
for a single case, or through `scripts/batch_auto_template_mix.py` for Feishu
manifest batches. The batch script now only pairs rows and writes sheet files;
the shared render path lives in `scripts/render_pipeline.py`.

The lower-level bridge still forwards to:

```text
D:\code\music_auto_mix1\music_auto_mix1\scripts\auto_template_mix.py
```

The current renderer defaults are:

- volume automation is off unless `--with-volume-automation` is explicitly set;
- the template renderer keeps DelayVerb send at the renderer default, currently
  85% pre-fader;
- DelayVerb estimates BPM from the reference backing and selects reverb mode via
  a measured `space_profile` instead of a fixed short-tail Chamber rule;
- accompaniment transient safety, reference balance ducking, and the master
  loudness finalizer run when reference files can be resolved;
- old batch outputs are reused unless `--force` is passed.

From this repository:

```powershell
.\python\python.exe scripts\auto_template_mix.py vocal.wav accomp.wav final.wav --dry-run
```

Single-case production entrypoint:

```powershell
.\.venv\Scripts\python.exe scripts\render_pipeline.py `
  --dry vocal.wav `
  --accomp accomp.wav `
  --case-name test `
  --extra-name song `
  --row 1
```

If you created a local venv here, this also works:

```powershell
.\.venv\Scripts\python.exe scripts\auto_template_mix.py vocal.wav accomp.wav final.wav --dry-run
```

You can still override the analyzer environment explicitly:

```powershell
.\python\python.exe scripts\auto_template_mix.py vocal.wav accomp.wav final.wav `
  --analyzer-python D:\path\to\python.exe `
  --analyzer D:\code\spectral-mix-template-selector\spectrum_template_analyzer.py
```

Batch Feishu render, forcing stale old-chain outputs to be regenerated:

```powershell
.\.venv\Scripts\python.exe scripts\batch_auto_template_mix.py --row 35 --force
.\.venv\Scripts\python.exe scripts\batch_auto_template_mix.py --force
```

When `--row` is used, the sheet export is written with a row suffix such as
`feishu_sheet_rows_row_035.tsv`, so the full batch sheet is not overwritten.

For the current production chain, do not pass `--with-volume-automation` unless
you are deliberately testing the older paragraph-level automation. A correct
new render summary should show renderer stdout containing:

```text
[step 1d] External DelayVerb group send
[step 2a] Accompaniment transient safety
[step 3a.1] Reference vocal/accompaniment dynamic balance
[step 4] Master bus chain: Pro-Q3 -> GW MixCentric -> L2 -> loudness finalizer
```

The batch script explicitly forwards `--reference-audio`, `--reference-vocal`,
and `--reference-accomp` to the renderer. This is important because some dry
vocals are resampled into the output directory before rendering, and their
temporary `_sr44100` filenames are not reliable for automatic reference lookup.

## Single File Usage

```bash
python3 spectrum_template_analyzer.py path/to/audio.mp3 --sr 44100 --top-db 35
```

Common options:

```bash
--sr 44100                 Target sample rate. Default: 44100
--n-fft 4096               STFT FFT size. Default: 4096
--hop-length 1024          STFT hop length. Default: n_fft / 4
--top-db 40                Silence trimming threshold. Default: 40
--no-trim                  Disable silence trimming
--peak-prominence-db 6     Peak prominence threshold in dB. Default: 6
```

## Batch Usage

Run over all supported audio files in a directory:

```bash
python3 batch_analyze_spectrum.py downloads/reconstruct_audio
```

Run recursively:

```bash
python3 batch_analyze_spectrum.py downloads/reconstruct_audio --recursive
```

Write outputs to custom paths:

```bash
python3 batch_analyze_spectrum.py downloads/reconstruct_audio \
  --output-csv results.csv \
  --summary-json summary.json
```

Analyze only the first few files while testing:

```bash
python3 batch_analyze_spectrum.py downloads/reconstruct_audio --limit 5
```

Analyze files in parallel:

```bash
python3 batch_analyze_spectrum.py downloads/reconstruct_audio --jobs 4
```

Supported default extensions:

```text
.wav, .mp3, .flac, .m4a, .aac, .aiff, .aif
```

## What It Computes

The script loads mono audio with `librosa`, optionally removes quiet sections
with `librosa.effects.split`, then computes an STFT magnitude spectrum.

Frequency bands:

| Name | Range |
| --- | --- |
| sub | 20-80 Hz |
| low | 80-180 Hz |
| lowmid | 180-500 Hz |
| mid | 500-1000 Hz |
| upper | 1k-4k Hz |
| harsh | 4k-8k Hz |
| sib | 8k-12k Hz |
| air | 12k-20k Hz |

For each band, it computes summed power energy across the matching STFT bins.

It then computes:

- `ratios`: each band energy divided by the sum of all band energies
- `body_to_presence`: `E(180-1000 Hz) / E(1k-8k Hz)`
- `peakiness_upper`: highest peak prominence in the 1k-4k band
- `peakiness_harsh`: highest peak prominence in the 4k-8k band
- `peakiness_sib`: highest peak prominence in the 8k-12k band

## Output

The command prints a JSON dict:

```json
{
  "ratios": {
    "sub": 0.01,
    "low": 0.02,
    "lowmid": 0.31,
    "mid": 0.17,
    "upper": 0.22,
    "harsh": 0.18,
    "sib": 0.06,
    "air": 0.03
  },
  "body_to_presence": 1.17,
  "peakiness_upper": 7.2,
  "peakiness_harsh": 5.8,
  "peakiness_sib": 6.0,
  "classification": {
    "label": "template_A"
  }
}
```

The real output also includes `band_energies`, `group_ratios`, sample-rate
metadata, and detailed classification hit rules.

It also reports source-bandwidth info so band-limited inputs do not silently
skew ratios:

```json
{
  "sample_rate": 44100,
  "native_sample_rate": 24000,
  "effective_nyquist_hz": 12000.0,
  "dropped_bands": ["air"]
}
```

Any band whose lower edge sits above `effective_nyquist_hz` is dropped from
the denominator, and any band partially above it is clipped — so a 24 kHz
source upsampled to 44.1 kHz no longer has its empty `air` band inflating
all other ratios.

Batch output CSV includes one row per audio file with:

- file path and filename
- selected classification label
- all band ratios
- `body_to_presence`
- `peakiness_upper`, `peakiness_harsh`, and `peakiness_sib`
- template hit counts and matched rule names

## Classification Rules

Classification rules live near the top of
`spectrum_template_analyzer.py` in `CLASSIFICATION_RULES`. Each template has
a `rules` dict (regular hits) and a `strong_rules` dict (the same metrics at
a higher severity threshold).

### Templates

| Label | Name | Targets |
| --- | --- | --- |
| template_A | Muddy / Boxy Vocal | 厚、闷、糊、鼻、箱感、主体偏暗 |
| template_B | Peaky / Harsh Vocal | 炸、刺、硬、毛、金属感、某些字突然冲 |
| template_C | Imbalanced / Heavy Low-Mid | 闷、糊、头重脚轻、缺高频、不通透 |

The three templates are designed to be **mutually exclusive** — each one
fires only on its own structural pattern, so a single audio file lines up
cleanly with one template instead of triggering several at once.

### template_A — Muddy / Boxy

A is the default "muddy / boxy" pattern: body energy is on the high side
and there is no qualitatively different signal pushing it into B (high-frequency
problem) or C (full structural imbalance). A's rules are gated by
`in_a_territory()`, which suppresses A only when `in_c_territory()` holds —
so A owns every other body-heavy vocal, **including hollow / boxy vocals
that lack a low foundation** (those are still "箱感", which is A territory).

A's rules (`lowmid_ratio_high`, `mid_ratio_high`, `body_to_presence_high`)
use loose thresholds (e.g. `lowmid >= 0.28`); the strong variants use
stricter ones (e.g. `lowmid >= 0.34`).

### template_B — Peaky / Harsh

B fires only on high-frequency problems:

- large ratio in `upper` (≥ 0.26) / `harsh` (≥ 0.16) / `sib` (≥ 0.12), or
- prominent peaks (`peakiness_upper >= 9 dB`, `peakiness_harsh >= 9 dB`).

Body shape alone is never a B signal — without high-frequency evidence the
vocal is just muddy (A) or imbalanced (C).

If `peakiness_harsh >= 12 dB`, B wins unconditionally — a real de-ess
problem takes priority over everything else.

### template_C — Imbalanced / Heavy Low-Mid

C is the "head-heavy, presence-starved, peaky body" pattern, qualitatively
different from A's smooth muddiness. Its `in_c_territory()` gate requires
**all three** structural conditions simultaneously:

| Condition | Threshold | Constant |
| --- | --- | --- |
| body dominant | `group_ratios.body >= 0.70` | `C_BODY_DOMINANT_RATIO` |
| presence starved | `group_ratios.presence <= 0.10` | `C_PRESENCE_STARVED_RATIO` |
| body resonance peak | `peakiness_upper >= 9 dB` | `C_BODY_PEAK_DB` |

This is the key difference vs A: a vocal that is just heavy in low or
lowmid without a 1-4 kHz resonance peak (e.g. smoothly dark, no sharp
spike) **stays in A** even if its lowmid ratio is very high. Only when the
body region also rings with a resonant peak does C take over.

Once inside C's territory, the individual `rules` (e.g. `extreme_lowmid`,
`very_high_body_to_presence`, `band_limited_highs`, `body_peak_spiky`)
contribute hits, and `strong_rules` (e.g. `mega_lowmid`,
`extreme_body_to_presence`, `very_spiky_body_peak`) contribute strong hits.

### Decision Flow

After every rule is evaluated, the label is chosen as follows (top wins):

1. If `peakiness_harsh >= 12 dB`, choose `template_B` — a real harsh spike
   always takes priority (de-ess before anything else).
2. Otherwise A, B, and C compete as equals:
   - If exactly one template has any `strong_hits`, that "smoking gun"
     template wins.
   - Else rank by `(hits, strong_hits)`; ties break in the order A > B > C.

`minimum_hits` no longer gates label selection — it remains in the JSON
output as a `qualified` flag for diagnostics.

### Tuning

All thresholds are module-level constants or inline numbers in
`CLASSIFICATION_RULES`. The most impactful knobs:

| Constant | Purpose |
| --- | --- |
| `DECISIVE_HARSH_PEAK_DB` | dB threshold for the B override |
| `C_BODY_DOMINANT_RATIO` | how heavy the body group must be for C |
| `C_PRESENCE_STARVED_RATIO` | how dead the presence group must be for C |
| `C_BODY_PEAK_DB` | dB threshold for "body resonance peak" |
| `PEAKINESS_NOISE_FLOOR_DB` | max-dB floor a band must clear before its peakiness is trusted (a band that bottoms out near -80 dB is treated as empty; using a ratio threshold here was wrong — it masked real sib/harsh spikes inside body-dominated mixes) |

Adjust these for your product, vocal model, genre, language, or mixing
style. When a real sample is misclassified, the easiest debug path is to
run `python3 spectrum_template_analyzer.py FILE.wav` and inspect the
returned `ratios`, `group_ratios`, `body_to_presence`, and `peakiness_*`
fields against the gates above.

## Notes

- Default sample rate is `44100`, so the `air` band can cover up to 20kHz.
- If you use `--sr 22050`, frequencies above about 11kHz are unavailable.
- If the source file's native sample rate is below the target (e.g. a 24 kHz
  wav loaded with `--sr 44100`), bands above the source Nyquist are dropped
  automatically and reported in `dropped_bands`.
- Silence trimming is intentionally simple; for full vocal-only analysis, run
  the script on an isolated vocal stem or a clipped vocal section.
- `.gitignore` excludes local audio, Excel files, generated downloads, caches,
  and batch reports so large/private files are not committed by accident.

## Batch Download Reconstruct Audio

Use `download_reconstruct_audio.py` to batch download audio URLs from column Q's
`algo_audio_reconstruct_event_result.output_url`.

Dry run first:

```bash
python3 download_reconstruct_audio.py "/Users/xy/Downloads/4月20到5月9日反馈数据.xlsx" --dry-run
```

Download files:

```bash
python3 download_reconstruct_audio.py "/Users/xy/Downloads/4月20到5月9日反馈数据.xlsx"
```

By default, files are saved to:

```text
downloads/reconstruct_audio/
```

Each filename starts with the Excel row number, for example:

```text
row_0008_1778246283_123754377190_codec_out_05ac95c6.wav
```

A CSV manifest is also written to:

```text
downloads/reconstruct_audio/download_manifest.csv
```

Useful options:

```bash
--column Q                 Column to parse. Default: Q
--start-row 2              First data row. Default: 2
--output-dir DIR           Change download directory
--overwrite                Re-download files that already exist
--limit 5                  Only download the first 5 URLs
--retries 2                Retry count per URL
```
