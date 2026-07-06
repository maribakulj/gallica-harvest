"""GT-free segmentation-complexity scoring from word-level IIIF annotations.

The premise (see project notes): recognition errors need ground truth to be
detected, but *segmentation* errors leave intrinsic geometric and statistical
signatures in the word-token stream that the IIIF gateway serves — no
transcription required. This module computes those signatures per page.

None of the metrics below inspect the *content* of a token; they use only its
bounding box and its position in the served stream. They are therefore
computable on 100 % of pages, automatically.

Signatures computed per page
----------------------------
- flow_disorder       : fraction of consecutive stream-tokens that go
                        "backwards" relative to a correct top-to-bottom,
                        left-to-right reading order. High = reading-order
                        problems (the rats-page symptom: y jumps 999 -> 279).
- n_columns           : number of column bands detected by 1-D clustering of
                        token x-centres. Multi-column pages are where
                        segmentation dominates.
- column_jump_rate    : fraction of consecutive stream-tokens that jump
                        between different detected columns. High = the OCR
                        engine interleaved columns (contamination).
- wide_box_rate       : fraction of tokens whose width exceeds several median
                        token widths — candidate merged lines / cross-column
                        boxes.
- height_dispersion   : robust coefficient of variation of token heights
                        (MAD / median). High = mixed type sizes, decorative
                        titles, layout heterogeneity.
- coverage_gap        : fraction of the text bounding area not covered by any
                        token box (rasterised coarsely) — proxy for missed
                        zones / sparse detection.
- token_count, page geometry, and a composite `complexity` in [0,1].

The composite is a transparent weighted sum of normalised components; weights
are declared here and meant to be *recalibrated* against ~25 hand-annotated
pages (that is what turns this indicator into a defensible estimate).
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from .iiif3 import parse_annotation_page
from .parsing import Line


# --------------------------------------------------------------------------
# 1-D clustering of x-centres into columns
# --------------------------------------------------------------------------

def detect_columns(tokens_or_xcen, page_width: Optional[int],
                   min_gutter_frac: float = 0.015,
                   min_col_frac: float = 0.04) -> list[tuple[float, float]]:
    """Detect column bands via a vertical projection profile (robust).

    Accepts either a list of Line tokens (preferred: uses full x-extent) or a
    bare list of x-centres (back-compat). Bins the x-axis and marks each bin
    as "inked" if any token covers it. Column bands are maximal runs of inked
    bins; gutters are runs of empty bins wider than min_gutter_frac of the
    page. Bands narrower than min_col_frac of the page are dropped as noise.

    This does NOT rely on inter-token spacing (which collapses when token
    widths vary wildly, e.g. vertical titles mixed with body text) — the flaw
    that produced hundreds of spurious columns on real pages.
    """
    # Normalise input to a list of (x0, x1) extents.
    extents: list[tuple[float, float]] = []
    if tokens_or_xcen and hasattr(tokens_or_xcen[0], "bbox"):
        for t in tokens_or_xcen:
            if t.bbox:
                extents.append((t.bbox[0], t.bbox[0] + t.bbox[2]))
    else:
        # bare centres: give each a nominal 1px width
        extents = [(x, x) for x in tokens_or_xcen]
    if not extents:
        return []

    minx = min(e[0] for e in extents)
    maxx = max(e[1] for e in extents)
    span = maxx - minx
    if span <= 0:
        return [(minx, maxx)]

    W = page_width or maxx
    nbins = 400
    binw = span / nbins
    inked = [False] * nbins
    for x0, x1 in extents:
        b0 = int((x0 - minx) / span * nbins)
        b1 = int((x1 - minx) / span * nbins)
        for b in range(max(0, b0), min(nbins, b1 + 1)):
            inked[b] = True

    min_gutter_bins = max(1, int(min_gutter_frac * W / binw))
    # Find runs of inked bins separated by empty runs >= min_gutter_bins.
    bands = []
    i = 0
    while i < nbins:
        if not inked[i]:
            i += 1
            continue
        j = i
        while j < nbins:
            if inked[j]:
                j += 1
            else:
                # measure the empty run; if it's a real gutter, stop the band
                k = j
                while k < nbins and not inked[k]:
                    k += 1
                if k - j >= min_gutter_bins:
                    break
                j = k  # small gap inside a column, keep going
        x_lo = minx + i * binw
        x_hi = minx + j * binw
        bands.append((x_lo, x_hi))
        i = j

    # Drop bands narrower than min_col_frac of the page.
    min_col_w = min_col_frac * W
    bands = [b for b in bands if (b[1] - b[0]) >= min_col_w] or [(minx, maxx)]
    return bands


def _column_of(x: float, bands: list[tuple[float, float]]) -> int:
    for i, (lo, hi) in enumerate(bands):
        if lo <= x <= hi:
            return i
    # nearest band by centre if x falls in a gap
    return min(range(len(bands)),
               key=lambda i: abs(x - (bands[i][0] + bands[i][1]) / 2))


# --------------------------------------------------------------------------
# Reading-order disorder
# --------------------------------------------------------------------------

def flow_disorder(tokens: list[Line], bands: list[tuple[float, float]],
                  line_tol_frac: float = 0.5) -> float:
    """Fraction of consecutive stream pairs violating reading order.

    Correct order within a column: y increases; at equal y (same line, within
    tolerance), x increases. Across columns, a later column should not precede
    an earlier one at the same vertical level. We score each consecutive pair
    in the *served stream* as ordered/violating and return the violation rate.
    """
    boxes = [t.bbox for t in tokens if t.bbox]
    if len(boxes) < 2:
        return 0.0
    heights = [h for _, _, _, h in boxes]
    med_h = statistics.median(heights) or 1
    tol = med_h * line_tol_frac

    violations = 0
    total = 0
    seen_cols: set[int] = set()
    for (x1, y1, w1, h1), (x2, y2, w2, h2) in zip(boxes, boxes[1:]):
        total += 1
        c1 = _column_of(x1 + w1 / 2, bands) if bands else 0
        c2 = _column_of(x2 + w2 / 2, bands) if bands else 0
        seen_cols.add(c1)

        if c2 < c1:
            # Returned to a more-left column mid-stream: reading-order
            # violation (column ping-pong / interleaving).
            violations += 1
            continue
        if c2 > c1:
            # Forward step. Fine when entering the next column for the first
            # time; a jump to an already-seen column is ping-pong.
            if c2 in seen_cols:
                violations += 1
            continue
        # same column: enforce top-to-bottom, then left-to-right
        if y2 < y1 - tol:
            violations += 1
        elif abs(y2 - y1) <= tol and x2 < x1 - tol:
            violations += 1
    return violations / total if total else 0.0


def column_jump_rate(tokens: list[Line], bands: list[tuple[float, float]]) -> float:
    if not bands or len(bands) < 2:
        return 0.0
    cols = [_column_of(t.bbox[0] + t.bbox[2] / 2, bands)
            for t in tokens if t.bbox]
    if len(cols) < 2:
        return 0.0
    jumps = sum(1 for a, b in zip(cols, cols[1:]) if a != b)
    return jumps / (len(cols) - 1)


# --------------------------------------------------------------------------
# Box statistics
# --------------------------------------------------------------------------

def wide_box_rate(tokens: list[Line], factor: float = 6.0) -> float:
    widths = [t.bbox[2] for t in tokens if t.bbox]
    if not widths:
        return 0.0
    med = statistics.median(widths) or 1
    return sum(1 for w in widths if w > factor * med) / len(widths)


def height_dispersion(tokens: list[Line]) -> float:
    heights = [t.bbox[3] for t in tokens if t.bbox]
    if len(heights) < 2:
        return 0.0
    med = statistics.median(heights) or 1
    mad = statistics.median([abs(h - med) for h in heights])
    return mad / med


def coverage_gap(tokens: list[Line], page_width: Optional[int],
                 page_height: Optional[int], grid: int = 40) -> float:
    """Coarse rasterised fraction of the text bbox not covered by any token.

    Only the area spanned by the tokens themselves is considered (not the full
    page), so margins don't inflate the gap. High values indicate sparse or
    holey detection within the text region.
    """
    boxes = [t.bbox for t in tokens if t.bbox]
    if len(boxes) < 4:
        return 0.0
    minx = min(b[0] for b in boxes)
    miny = min(b[1] for b in boxes)
    maxx = max(b[0] + b[2] for b in boxes)
    maxy = max(b[1] + b[3] for b in boxes)
    W, H = maxx - minx, maxy - miny
    if W <= 0 or H <= 0:
        return 0.0
    cells = [[False] * grid for _ in range(grid)]
    for x, y, w, h in boxes:
        cx0 = int((x - minx) / W * grid)
        cx1 = int((x + w - minx) / W * grid)
        cy0 = int((y - miny) / H * grid)
        cy1 = int((y + h - miny) / H * grid)
        for cy in range(max(0, cy0), min(grid, cy1 + 1)):
            for cx in range(max(0, cx0), min(grid, cx1 + 1)):
                cells[cy][cx] = True
    covered = sum(row.count(True) for row in cells)
    return 1.0 - covered / (grid * grid)


# --------------------------------------------------------------------------
# Per-page aggregate
# --------------------------------------------------------------------------

@dataclass
class PageComplexity:
    ark: str = ""
    page: Optional[int] = None
    doctype: str = ""
    period: str = ""
    n_tokens: int = 0
    n_columns: int = 0
    flow_disorder: float = 0.0
    column_jump_rate: float = 0.0
    wide_box_rate: float = 0.0
    height_dispersion: float = 0.0
    coverage_gap: float = 0.0
    complexity: float = 0.0
    empty: bool = False
    has_ocr: bool = True   # False when the AnnotationPage carries no tokens at all


# Declared weights for the composite. Recalibrate against hand-annotated pages.
WEIGHTS = {
    "flow_disorder": 0.35,
    "column_jump_rate": 0.20,
    "wide_box_rate": 0.15,
    "height_dispersion": 0.15,
    "coverage_gap": 0.15,
}


def _clip01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def score_page(page_obj, ark: str = "", page: Optional[int] = None,
               doctype: str = "", period: str = "") -> PageComplexity:
    tokens = [l for l in page_obj.lines if l.bbox]
    pc = PageComplexity(ark=ark, page=page, doctype=doctype, period=period,
                        n_tokens=len(tokens))
    all_lines = page_obj.lines
    if len(all_lines) == 0:
        pc.empty = True
        pc.has_ocr = False   # AnnotationPage with no items = no production OCR
        return pc
    if len(tokens) < 4:
        pc.empty = True
        return pc

    xcen = [t.bbox[0] + t.bbox[2] / 2 for t in tokens]
    bands = detect_columns(tokens, page_obj.width)
    pc.n_columns = len(bands)
    pc.flow_disorder = round(flow_disorder(tokens, bands), 4)
    pc.column_jump_rate = round(column_jump_rate(tokens, bands), 4)
    pc.wide_box_rate = round(wide_box_rate(tokens), 4)
    pc.height_dispersion = round(height_dispersion(tokens), 4)
    pc.coverage_gap = round(coverage_gap(tokens, page_obj.width, page_obj.height), 4)

    # Normalise components that aren't already in [0,1].
    norm = {
        "flow_disorder": _clip01(pc.flow_disorder),
        "column_jump_rate": _clip01(pc.column_jump_rate),
        "wide_box_rate": _clip01(pc.wide_box_rate * 5),      # rare -> amplify
        "height_dispersion": _clip01(pc.height_dispersion),  # MAD/med ~[0,1+]
        "coverage_gap": _clip01(pc.coverage_gap),
    }
    pc.complexity = round(sum(WEIGHTS[k] * norm[k] for k in WEIGHTS), 4)
    return pc


def score_annotation_file(path: str | Path, **meta) -> PageComplexity:
    page_obj = parse_annotation_page(Path(path).read_bytes(), source_path=str(path))
    return score_page(page_obj, **meta)
