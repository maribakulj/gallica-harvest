"""Calibration of the segmentation-complexity score against human judgement.

Phase 0 ends with a claim ("X % of residual OCR error is segmentation-driven")
that is only defensible if the automatic `complexity` score tracks what a human
annotator actually sees. This module closes that loop: join the annotation sheet
export (browser CSV) to the seg-score CSV and measure the rank correlation
between the machine score and a human segmentation-severity signal.

Two human signals are derived per page:
  - `seg_count`  : number of segmentation categories ticked (0..6) — the direct
                   analogue of what `complexity` tries to predict;
  - `gravite`    : the global severity selector, mapped ras/mineure/notable/
                   severe -> 0..3 (a coarser, whole-page signal).

Correlation is Spearman's rho (Pearson on ranks, average-rank ties), computed
with the standard library only — no numpy/scipy, consistent with the toolkit.
A positive, sizeable rho on ~25 pages is what licenses extrapolating the score
to the hundreds of unannotated pages; report it with its n, never without.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

SEG_KEYS = [
    "seg_fusion", "seg_scission", "seg_colonnes",
    "seg_ordre", "seg_zone_manquee", "seg_typage_bloc",
]
GRAVITE_ORDER = {"ras": 0, "mineure": 1, "notable": 2, "severe": 3}


def _truthy(v: Optional[str]) -> bool:
    return str(v).strip() in ("1", "true", "True", "x", "X", "oui")


def load_annotations(path: str | Path) -> dict[tuple[str, str], dict]:
    """Annotation-sheet CSV export -> {(ark, page): {seg_count, gravite, done}}.

    The sheet writes ';'-separated UTF-8 (BOM) with the segmentation checkboxes
    as '1'/'' and a `_gravite` label. `page` is kept as a string key so it joins
    cleanly with the seg-score CSV regardless of int/str formatting.
    """
    out: dict[tuple[str, str], dict] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            ark = (r.get("ark") or "").strip()
            if not ark:
                continue
            page = (r.get("page") or "").strip()
            seg_count = sum(1 for k in SEG_KEYS if _truthy(r.get(k)))
            gravite = GRAVITE_ORDER.get((r.get("_gravite") or "").strip())
            out[(ark, page)] = {
                "seg_count": seg_count,
                "gravite": gravite,
                "done": _truthy(r.get("_fait")),
            }
    return out


def load_scores(path: str | Path) -> dict[tuple[str, str], float]:
    """seg-score CSV -> {(ark, page): complexity} for usable (OCR, non-empty) pages."""
    out: dict[tuple[str, str], float] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("has_ocr") == "True" and r.get("empty") == "False":
                out[(r["ark"], str(r["page"]))] = float(r["complexity"])
    return out


def _ranks(xs: list[float]) -> list[float]:
    """Fractional ranks with average-rank tie handling."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # ranks are 1-based
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:  # a constant vector has undefined correlation
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / (sxx * syy) ** 0.5


def spearman(xs: list[float], ys: list[float]) -> Optional[float]:
    """Spearman's rho = Pearson correlation of the fractional ranks."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    return _pearson(_ranks(xs), _ranks(ys))


def correlate(annotations: dict[tuple[str, str], dict],
              scores: dict[tuple[str, str], float],
              signal: str = "seg_count") -> dict:
    """Join on (ark, page) over pages the annotator marked done, and correlate
    the chosen human `signal` with the machine complexity. Returns a summary
    dict with the paired series, rho and n (rows lacking the signal are dropped).
    """
    pairs = []
    for key, ann in annotations.items():
        if not ann["done"]:
            continue
        if key not in scores:
            continue
        human = ann.get(signal)
        if human is None:
            continue
        pairs.append((key, float(human), scores[key]))
    human_vals = [h for _, h, _ in pairs]
    machine_vals = [m for _, _, m in pairs]
    return {
        "signal": signal,
        "n": len(pairs),
        "spearman": spearman(human_vals, machine_vals),
        "pairs": pairs,
    }
