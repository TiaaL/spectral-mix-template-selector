#!/usr/bin/env python3
"""Portable classification tests — synthetic metrics, no audio, no local paths.

Run: python3 tests/test_classify_rules.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import spectrum_template_analyzer as S  # noqa: E402

FAILURES: list[str] = []


def metrics(**over):
    """Neutral metrics: nothing qualifies. Override to build a case."""
    m = {
        "ratios": {b: 0.0 for b in S.BANDS},
        "group_ratios": {"body": 0.0, "presence": 0.5},
        "peakiness_upper": 0.0,
        "peakiness_harsh": 0.0,
        "peakiness_sib": 0.0,
        "body_to_presence": 0.0,
    }
    ratio_over = over.pop("ratios", {})
    group_over = over.pop("group_ratios", {})
    m["ratios"].update(ratio_over)
    m["group_ratios"].update(group_over)
    m.update(over)
    return m


def c_structure(**over):
    """Body-dominant, presence-starved: the real badcase shape."""
    ratio_over = over.pop("ratios", {})
    m = metrics(
        ratios={"lowmid": 0.72, "mid": 0.20, "upper": 0.013, "harsh": 0.026, **ratio_over},
        group_ratios={"body": 0.92, "presence": 0.038},
        peakiness_upper=9.5,
        body_to_presence=24.0,
    )
    m.update(over)
    return m


def check(name, cond, detail=""):
    if cond:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


print("single harsh peak must never overturn a qualified C")
r = S.classify(c_structure(peakiness_harsh=12.58))
check("badcase shape -> C", r["label"] == "template_C", r["label"])
check("C qualified", r["template_C"]["qualified"])
check("harshness recorded as secondary", "hf_harshness" in r["secondary_issues"] or not r["secondary_issues"], r["secondary_issues"])

r = S.classify(c_structure(peakiness_harsh=30.0))
check("even an extreme lone harsh peak -> C", r["label"] == "template_C", r["label"])

print("\nstrong multi-group B may still compete with C")
# C territory needs body>=0.70 & presence<=0.10, so a mix that is both
# structurally C and genuinely harsh sits right at the presence boundary.
r = S.classify(metrics(
    ratios={"lowmid": 0.60, "mid": 0.20, "upper": 0.06, "harsh": 0.04, "sib": 0.12},
    group_ratios={"body": 0.80, "presence": 0.10},
    peakiness_upper=10.0,
    peakiness_harsh=13.0,
    body_to_presence=8.0,
))
check("multi-evidence B beats C", r["label"] == "template_B", r["label"])
check("C structure kept as secondary", "body_heavy_structure" in r["secondary_issues"], r["secondary_issues"])

print("\nunqualified template must not win")
r = S.classify(metrics(ratios={"upper": 0.26}))
check("single B hit does not win", r["label"] != "template_B", r["label"])
r = S.classify(metrics(peakiness_harsh=12.58))
check("lone harsh spike, no other evidence, does not win B", r["label"] != "template_B", r["label"])

print("\nno-hit input falls back visibly")
r = S.classify(metrics())
check("falls back to A", r["label"] == "template_A", r["label"])
check("fallback flagged", r["fallback"] is True)
check("confidence low", r["confidence"] == "low", r["confidence"])
check("reason present", bool(r["selection_reason"]))

print("\nqualified A is not a fallback")
r = S.classify(metrics(
    ratios={"lowmid": 0.30, "mid": 0.21},
    group_ratios={"body": 0.51, "presence": 0.30},
    body_to_presence=1.20,
))
check("A selected", r["label"] == "template_A", r["label"])
check("not flagged fallback", r["fallback"] is False)

print("\nplain B alongside A stays secondary")
r = S.classify(metrics(
    ratios={"lowmid": 0.30, "mid": 0.21, "upper": 0.27},
    group_ratios={"body": 0.51, "presence": 0.30},
    body_to_presence=1.20,
    peakiness_harsh=9.5,
))
check("A stays primary", r["label"] == "template_A", r["label"])
check("HF harshness is secondary", "hf_harshness" in r["secondary_issues"], r["secondary_issues"])

print("\nrule boundaries")
r = S.classify(c_structure(peakiness_harsh=11.99))
check("just below strong harsh -> C", r["label"] == "template_C", r["label"])
r = S.classify(metrics(ratios={"upper": 0.2599}))
check("just below upper threshold -> no B", r["label"] != "template_B", r["label"])

print("\nB alone (no A, no C) still selectable")
r = S.classify(metrics(
    ratios={"upper": 0.30, "harsh": 0.20},
    group_ratios={"body": 0.20, "presence": 0.55},
    peakiness_harsh=10.0,
))
check("multi-group B wins", r["label"] == "template_B", r["label"])
check("not fallback", r["fallback"] is False)

print("\noutput contract")
r = S.classify(metrics())
for key in ("label", "label_name", "fallback", "confidence", "secondary_issues",
            "template_A", "template_B", "template_C"):
    check(f"has {key}", key in r)
for t in ("template_A", "template_B", "template_C"):
    for key in ("name", "tags", "hits", "hit_rules", "strong_hits", "strong_rules", "qualified"):
        check(f"{t}.{key}", key in r[t])

print()
if FAILURES:
    print(f"{len(FAILURES)} failure(s): {FAILURES}")
    raise SystemExit(1)
print("all classification tests passed")
