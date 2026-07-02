"""Alignment of GT lines against production-OCR lines, and character-level
edit scripts.

Two matching signals are combined:
  - geometric IoU when both sides carry bounding boxes in the same image
    frame (the normal case for Gallica-sourced GT, since GT projects reuse
    BnF scans);
  - normalised text similarity as fallback / tie-breaker.

Unmatched GT lines and unmatched production lines are both reported: the
former usually indicate zones missed by production segmentation, the latter
indicate over-segmentation or non-GT'd noise. Both feed the phase-0
segmentation diagnostic; the matched pairs feed triplet extraction.
"""
from __future__ import annotations

import difflib
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from .parsing import Line


# --------------------------------------------------------------------------
# Similarity primitives
# --------------------------------------------------------------------------

def normalise(s: str, fold_case: bool = True, fold_diacritics: bool = False) -> str:
    """Loose normalisation used ONLY for matching, never for the edit script.

    The invariance group of the *metric* is a separate, declared decision;
    this here is merely a retrieval heuristic.
    """
    s = unicodedata.normalize("NFC", s)
    s = " ".join(s.split())
    if fold_case:
        s = s.casefold()
    if fold_diacritics:
        s = "".join(
            c for c in unicodedata.normalize("NFD", s)
            if unicodedata.category(c) != "Mn"
        )
    # Long s: retrieval-level equivalence with s (transcription conventions differ).
    s = s.replace("\u017f", "s")
    return s


def text_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalise(a), normalise(b)).ratio()


def bbox_iou(a: Optional[tuple], b: Optional[tuple]) -> float:
    if not a or not b:
        return 0.0
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(ax, bx)
    iy = max(ay, by)
    ix2 = min(ax + aw, bx + bw)
    iy2 = min(ay + ah, by + bh)
    if ix2 <= ix or iy2 <= iy:
        return 0.0
    inter = (ix2 - ix) * (iy2 - iy)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


# --------------------------------------------------------------------------
# Edit scripts
# --------------------------------------------------------------------------

@dataclass
class EditOp:
    op: str          # 'equal' | 'replace' | 'insert' | 'delete'
    ocr: str         # slice of the OCR hypothesis
    gt: str          # slice of the ground truth


def edit_script(ocr: str, gt: str) -> list[EditOp]:
    sm = difflib.SequenceMatcher(None, ocr, gt, autojunk=False)
    return [
        EditOp(op=tag, ocr=ocr[i1:i2], gt=gt[j1:j2])
        for tag, i1, i2, j1, j2 in sm.get_opcodes()
    ]


def cer(ocr: str, gt: str) -> float:
    """Character error rate = levenshtein(ocr, gt) / len(gt)."""
    if not gt:
        return 0.0 if not ocr else 1.0
    dist = _levenshtein(ocr, gt)
    return dist / len(gt)


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# --------------------------------------------------------------------------
# Page-level alignment
# --------------------------------------------------------------------------

@dataclass
class Match:
    gt: Line
    prod: Line
    iou: float
    sim: float


@dataclass
class AlignmentResult:
    matches: list[Match] = field(default_factory=list)
    unmatched_gt: list[Line] = field(default_factory=list)      # missed zones?
    unmatched_prod: list[Line] = field(default_factory=list)    # over-segmentation / noise
    # GT lines whose best geometric match overlaps several production lines
    # strongly: candidate merges/splits, i.e. segmentation errors.
    split_merge_suspects: list[tuple[Line, list[Line]]] = field(default_factory=list)


def align_pages(
    gt_lines: list[Line],
    prod_lines: list[Line],
    min_iou: float = 0.30,
    min_sim: float = 0.55,
    scale: Optional[float] = None,
) -> AlignmentResult:
    """Greedy 1-1 matching, geometry first, text as fallback.

    `scale` rescales GT bboxes into the production frame when the two files
    reference images of different resolutions (ratio prod_width/gt_width).
    """
    if scale and scale != 1.0:
        gt_lines = [
            Line(
                id=l.id, text=l.text, confidence=l.confidence, block_id=l.block_id,
                bbox=tuple(int(v * scale) for v in l.bbox) if l.bbox else None,
            )
            for l in gt_lines
        ]

    result = AlignmentResult()
    used_prod: set[int] = set()

    # Full IoU matrix (page-level line counts are small; O(n*m) is fine).
    iou_matrix = [
        [bbox_iou(g.bbox, p.bbox) for p in prod_lines] for g in gt_lines
    ]

    # Direction 1 — one GT line covered by several production lines: a split.
    for gi, g in enumerate(gt_lines):
        strong_g = [i for i, v in enumerate(iou_matrix[gi]) if v >= min_iou]
        if len(strong_g) >= 2:
            result.split_merge_suspects.append(
                (g, [prod_lines[i] for i in strong_g[:4]])
            )
    # Direction 2 — one production line covering several GT lines: a merge
    # (the frequent case on multi-column press). One suspect entry per GT
    # line involved, all pointing at the offending production line.
    for pi, p in enumerate(prod_lines):
        strong_p = [gi for gi in range(len(gt_lines)) if iou_matrix[gi][pi] >= min_iou]
        if len(strong_p) >= 2:
            for gi in strong_p[:4]:
                result.split_merge_suspects.append((gt_lines[gi], [p]))

    for gi, g in enumerate(gt_lines):
        strong = sorted(
            ((i, v) for i, v in enumerate(iou_matrix[gi]) if v >= min_iou),
            key=lambda t: -t[1],
        )

        best_i, best_iou, best_sim = None, 0.0, 0.0
        for i, v in strong:
            if i in used_prod:
                continue
            s = text_similarity(g.text, prod_lines[i].text)
            # geometry dominates, text breaks ties
            if (v, s) > (best_iou, best_sim):
                best_i, best_iou, best_sim = i, v, s

        if best_i is None:
            # Text-only fallback (GT without usable coordinates, or frame mismatch).
            for i, p in enumerate(prod_lines):
                if i in used_prod:
                    continue
                s = text_similarity(g.text, p.text)
                if s >= min_sim and s > best_sim:
                    best_i, best_sim = i, s
            best_iou = 0.0

        if best_i is not None:
            used_prod.add(best_i)
            result.matches.append(
                Match(gt=g, prod=prod_lines[best_i], iou=best_iou, sim=best_sim)
            )
        else:
            result.unmatched_gt.append(g)

    result.unmatched_prod = [
        p for i, p in enumerate(prod_lines) if i not in used_prod
    ]
    return result
