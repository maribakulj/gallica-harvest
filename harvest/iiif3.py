"""IIIF Presentation v3 support (openapi.bnf.fr).

Gallica's legacy endpoints sit behind bot protection; the OpenAPI gateway is
the programmatic door. Two resources matter here:

  - the *supplementing* AnnotationPage of a canvas, which carries the OCR
    text anchored to image regions:
      https://openapi.bnf.fr/iiif/presentation/v3/ark:/12148/{ark}/f{n}/annotationpage/supplementing.json
  - the manifest, for page counts and Image API service URLs:
      https://openapi.bnf.fr/iiif/presentation/v3/ark:/12148/{ark}/manifest.json

The annotation parser is deliberately defensive about the W3C Web Annotation
shapes: `target` as a string with a #xywh= fragment, or as an object with a
FragmentSelector; `body` as a single TextualBody or a list; `motivation` as
a string or a list. Granularity (line vs block vs word) is whatever the
server provides — inspect a real response before assuming line-level.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from .parsing import Line, Page

XYWH_RE = re.compile(r"xywh=(?:pixel:)?(\d+),(\d+),(\d+),(\d+)")
OCR_RATE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


def _as_list(x: Any) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _target_bbox(target: Any) -> Optional[tuple[int, int, int, int]]:
    """Extract x,y,w,h from a Web Annotation target (string or object)."""
    if isinstance(target, str):
        m = XYWH_RE.search(target)
        return tuple(map(int, m.groups())) if m else None
    if isinstance(target, dict):
        sel = target.get("selector")
        for s in _as_list(sel):
            if isinstance(s, dict):
                val = s.get("value") or ""
                m = XYWH_RE.search(val)
                if m:
                    return tuple(map(int, m.groups()))
            elif isinstance(s, str):
                m = XYWH_RE.search(s)
                if m:
                    return tuple(map(int, m.groups()))
        src = target.get("source")
        if src is not None:
            return _target_bbox(src)
    return None


def _body_text(body: Any) -> str:
    parts = []
    for b in _as_list(body):
        if isinstance(b, dict):
            v = b.get("value")
            if v:
                parts.append(str(v))
        elif isinstance(b, str):
            parts.append(b)
    return " ".join(parts).strip()


def parse_annotation_page(data: bytes | str | dict, source_path: str = "") -> Page:
    """AnnotationPage JSON -> Page of Lines (text + optional bbox)."""
    if isinstance(data, (bytes, str)):
        data = json.loads(data)
    page = Page(source_path=source_path or str(data.get("id", "")))

    items = data.get("items", [])
    # Some servers nest AnnotationPages inside a collection-ish wrapper.
    if items and isinstance(items[0], dict) and items[0].get("type") == "AnnotationPage":
        items = [a for ap in items for a in ap.get("items", [])]

    for i, anno in enumerate(items):
        if not isinstance(anno, dict):
            continue
        motivations = _as_list(anno.get("motivation"))
        if motivations and not any("supplementing" in str(m) for m in motivations):
            continue
        text = _body_text(anno.get("body"))
        if not text:
            continue
        page.lines.append(
            Line(
                id=str(anno.get("id") or f"anno_{i}"),
                text=text,
                bbox=_target_bbox(anno.get("target")),
            )
        )
    return page


def _label_values(entry: Any) -> tuple[list[str], list[str]]:
    """A manifest metadata entry -> (all label strings, all value strings),
    flattened across language maps ({'fr': [...], 'en': [...]}) or plain lists."""
    def flat(x: Any) -> list[str]:
        if isinstance(x, dict):
            return [s for v in x.values() for s in flat(v)]
        if isinstance(x, list):
            return [s for v in x for s in flat(v)]
        return [str(x)] if x is not None else []
    if not isinstance(entry, dict):
        return [], []
    return flat(entry.get("label")), flat(entry.get("value"))


def manifest_ocr_rate(data: bytes | str | dict) -> Optional[float]:
    """Document-level OCR rate from the manifest 'Taux OCR' / 'OCR rate'
    metadata field, as a fraction in [0,1] (e.g. '9.12 %' -> 0.0912).

    Returns None when the field is absent — and on openapi.bnf.fr the field is
    absent exactly when the document has no production OCR, so presence doubles
    as an OCR-availability flag. This is the reachable stand-in for the
    OAIRecord `nqa_score` (which sits behind Datadome on gallica.bnf.fr).
    """
    if isinstance(data, (bytes, str)):
        data = json.loads(data)
    for entry in _as_list(data.get("metadata")):
        labels, values = _label_values(entry)
        if any("Taux OCR" in l or "OCR rate" in l for l in labels):
            for v in values:
                m = OCR_RATE_RE.search(v)
                if m:
                    return float(m.group(1).replace(",", ".")) / 100.0
    return None


def parse_manifest(data: bytes | str | dict) -> dict:
    """Manifest v3 -> {'n_pages', 'ocr_rate', 'canvases': [{'index','width',
    'height','image_service'}...]}.

    image_service is the Image API base (canvas -> AnnotationPage(painting)
    -> Annotation -> body -> service[].id|@id), from which crops derive as
    {service}/{x},{y},{w},{h}/full/0/default.jpg

    ocr_rate is the document-level OCR fraction (see manifest_ocr_rate) or None.
    """
    if isinstance(data, (bytes, str)):
        data = json.loads(data)
    canvases = []
    for idx, canvas in enumerate(data.get("items", []), start=1):
        if not isinstance(canvas, dict) or canvas.get("type") != "Canvas":
            continue
        service_id = None
        for ap in _as_list(canvas.get("items")):
            for anno in _as_list(ap.get("items") if isinstance(ap, dict) else None):
                body = anno.get("body") if isinstance(anno, dict) else None
                for b in _as_list(body):
                    if not isinstance(b, dict):
                        continue
                    for svc in _as_list(b.get("service")):
                        if isinstance(svc, dict):
                            service_id = svc.get("id") or svc.get("@id")
                            if service_id:
                                break
        canvases.append({
            "index": idx,
            "width": canvas.get("width"),
            "height": canvas.get("height"),
            "image_service": service_id,
        })
    return {"n_pages": len(canvases), "ocr_rate": manifest_ocr_rate(data),
            "canvases": canvases}


def crop_url_from_service(service_id: str, bbox: tuple[int, int, int, int],
                          pad: int = 8) -> str:
    x, y, w, h = bbox
    region = f"{max(0, x - pad)},{max(0, y - pad)},{w + 2 * pad},{h + 2 * pad}"
    return f"{service_id.rstrip('/')}/{region}/max/0/default.jpg"


def group_tokens_into_lines(
    page: "Page",
    y_overlap_ratio: float = 0.5,
    x_gap_factor: float = 2.5,
) -> "Page":
    """Reconstruct lines from word-level annotations, purely geometrically.

    The OpenAPI supplementing endpoint serves one annotation per WORD, each
    with its own xywh box. Line/block/column structure is therefore implicit
    in the geometry and must be rebuilt — crucially WITHOUT trusting the item
    order, since diagnosing reading-order errors is one goal of phase 0.

    Algorithm:
      1. sort tokens by y (top), then x (left);
      2. greedily assign each token to an existing line-cluster whose
         vertical band overlaps the token by >= y_overlap_ratio of the
         smaller height; else open a new cluster;
      3. within each cluster, sort by x and join with spaces;
      4. split a cluster where the horizontal gap between consecutive tokens
         exceeds x_gap_factor * median_gap — catches two columns sharing a
         y-band (frequent press layout), surfacing them as separate lines
         rather than concatenating across the gutter.

    The reconstructed line bbox is the union of its token boxes. The result
    plugs straight into align_pages() like any ALTO/PAGE page.
    """
    from .parsing import Line, Page  # local import avoids cycle at load

    toks = [l for l in page.lines if l.bbox is not None]
    no_geo = [l for l in page.lines if l.bbox is None]
    toks.sort(key=lambda l: (l.bbox[1], l.bbox[0]))

    clusters: list[list] = []
    cluster_bands: list[list[float]] = []  # [y_top, y_bottom], mutable

    for tok in toks:
        _, y, _, h = tok.bbox
        y_bottom = y + h
        placed = False
        for ci, band in enumerate(cluster_bands):
            overlap = min(y_bottom, band[1]) - max(y, band[0])
            smaller = min(h, band[1] - band[0])
            if smaller > 0 and overlap / smaller >= y_overlap_ratio:
                clusters[ci].append(tok)
                band[0] = min(band[0], y)
                band[1] = max(band[1], y_bottom)
                placed = True
                break
        if not placed:
            clusters.append([tok])
            cluster_bands.append([y, y_bottom])

    out = Page(source_path=page.source_path, width=page.width, height=page.height)
    idx = 0
    # emit lines top-to-bottom, left-to-right for a stable reading order
    order = sorted(range(len(clusters)),
                   key=lambda i: (cluster_bands[i][0],
                                  min(t.bbox[0] for t in clusters[i])))
    for ci in order:
        cluster = sorted(clusters[ci], key=lambda l: l.bbox[0])
        for sub in _split_on_large_gaps(cluster, x_gap_factor):
            xs = [t.bbox[0] for t in sub]
            ys = [t.bbox[1] for t in sub]
            xe = [t.bbox[0] + t.bbox[2] for t in sub]
            ye = [t.bbox[1] + t.bbox[3] for t in sub]
            out.lines.append(Line(
                id=f"line_{idx}",
                text=" ".join(t.text for t in sub),
                bbox=(min(xs), min(ys), max(xe) - min(xs), max(ye) - min(ys)),
            ))
            idx += 1
    out.lines.extend(no_geo)
    return out


def _split_on_large_gaps(tokens: list, x_gap_factor: float) -> list[list]:
    """Split an x-sorted token run where a horizontal gap is anomalously wide."""
    if len(tokens) < 4:  # too short to estimate a gutter reliably
        return [tokens]
    gaps = [b.bbox[0] - (a.bbox[0] + a.bbox[2]) for a, b in zip(tokens, tokens[1:])]
    positive = sorted(g for g in gaps if g > 0)
    if not positive:
        return [tokens]
    median = positive[len(positive) // 2]
    threshold = max(median * x_gap_factor, median + 1)
    runs, cur = [], [tokens[0]]
    for tok, gap in zip(tokens[1:], gaps):
        if gap > threshold:
            runs.append(cur)
            cur = [tok]
        else:
            cur.append(tok)
    runs.append(cur)
    return runs
