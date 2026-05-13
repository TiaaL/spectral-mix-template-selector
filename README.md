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
  "classification": {
    "label": "template_A"
  }
}
```

The real output also includes `band_energies`, `group_ratios`, sample-rate
metadata, and detailed classification hit rules.

Batch output CSV includes one row per audio file with:

- file path and filename
- selected classification label
- all band ratios
- `body_to_presence`
- `peakiness_upper` and `peakiness_harsh`
- template hit counts and matched rule names

## Classification Rules

Classification rules live near the top of
`spectrum_template_analyzer.py` in `CLASSIFICATION_RULES`.

Current behavior:

- Count how many rules each template satisfies.
- A template qualifies when it hits at least 2 rules.
- If both templates qualify, choose the one with more hits.
- If hits tie, choose the one with more strong-rule hits.
- If still tied, return `undetermined`.

The thresholds are intentionally explicit and easy to tune. Adjust the values
in `CLASSIFICATION_RULES` for your product, vocal model, genre, language, or
mixing style.

## Notes

- Default sample rate is `44100`, so the `air` band can cover up to 20kHz.
- If you use `--sr 22050`, frequencies above about 11kHz are unavailable.
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
