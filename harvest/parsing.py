"""Namespace-agnostic parsing of ALTO (v2/v3/v4) and PAGE XML into a common Line model.

Gallica production OCR is delivered as ALTO; GT datasets come in ALTO
(HTR-United ecosystem, Europeana) or PAGE XML (NewsEye, Transkribus exports,
ENP). Both are reduced to the same dataclass so that the aligner does not
care about provenance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET


@dataclass
class Line:
    """A single text line with (optional) geometry."""
    id: str
    text: str
    # Bounding box in pixel coordinates of the source image: x, y, w, h.
    bbox: Optional[tuple[int, int, int, int]] = None
    # Mean word confidence if the source provides it (ALTO WC attribute).
    confidence: Optional[float] = None
    # Id of the enclosing block/region, useful for reading-order diagnostics.
    block_id: Optional[str] = None


@dataclass
class Page:
    source_path: str
    width: Optional[int] = None
    height: Optional[int] = None
    lines: list[Line] = field(default_factory=list)


def _local(tag: str) -> str:
    """Strip XML namespace: '{ns}TextLine' -> 'TextLine'."""
    return tag.rsplit("}", 1)[-1]


def _iter_local(root: ET.Element, name: str):
    for el in root.iter():
        if _local(el.tag) == name:
            yield el


# --------------------------------------------------------------------------
# ALTO
# --------------------------------------------------------------------------

def parse_alto(path: str | Path) -> Page:
    tree = ET.parse(str(path))
    root = tree.getroot()
    page = Page(source_path=str(path))

    for p in _iter_local(root, "Page"):
        page.width = _int_or_none(p.get("WIDTH"))
        page.height = _int_or_none(p.get("HEIGHT"))
        break

    for block in _iter_local(root, "TextBlock"):
        block_id = block.get("ID")
        for tl in _iter_local(block, "TextLine"):
            words, confs = [], []
            for el in tl:
                loc = _local(el.tag)
                if loc == "String":
                    # SUBS_CONTENT carries the full form of hyphenated words;
                    # we keep CONTENT (what is printed on this line) because
                    # the aligner works at the visual-line level.
                    words.append(el.get("CONTENT", ""))
                    wc = el.get("WC")
                    if wc is not None:
                        try:
                            confs.append(float(wc))
                        except ValueError:
                            pass
                elif loc == "SP":
                    pass  # spaces reconstructed by join below
            text = " ".join(w for w in words if w != "")
            bbox = _alto_bbox(tl)
            page.lines.append(
                Line(
                    id=tl.get("ID") or f"line_{len(page.lines)}",
                    text=text,
                    bbox=bbox,
                    confidence=(sum(confs) / len(confs)) if confs else None,
                    block_id=block_id,
                )
            )
    return page


def _alto_bbox(el: ET.Element) -> Optional[tuple[int, int, int, int]]:
    try:
        x = int(float(el.get("HPOS")))
        y = int(float(el.get("VPOS")))
        w = int(float(el.get("WIDTH")))
        h = int(float(el.get("HEIGHT")))
        return (x, y, w, h)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# PAGE XML
# --------------------------------------------------------------------------

def parse_page_xml(path: str | Path) -> Page:
    tree = ET.parse(str(path))
    root = tree.getroot()
    page = Page(source_path=str(path))

    for p in _iter_local(root, "Page"):
        page.width = _int_or_none(p.get("imageWidth"))
        page.height = _int_or_none(p.get("imageHeight"))
        break

    for region in _iter_local(root, "TextRegion"):
        region_id = region.get("id")
        for tl in _iter_local(region, "TextLine"):
            text = _page_line_text(tl)
            bbox = _page_bbox(tl)
            page.lines.append(
                Line(
                    id=tl.get("id") or f"line_{len(page.lines)}",
                    text=text,
                    bbox=bbox,
                    block_id=region_id,
                )
            )
    return page


def _page_line_text(tl: ET.Element) -> str:
    """First TextEquiv/Unicode directly under the line (not under Word)."""
    for child in tl:
        if _local(child.tag) == "TextEquiv":
            for u in child:
                if _local(u.tag) == "Unicode":
                    return u.text or ""
    # Fallback: concatenate word-level equivs.
    words = []
    for w in tl:
        if _local(w.tag) == "Word":
            for te in w:
                if _local(te.tag) == "TextEquiv":
                    for u in te:
                        if _local(u.tag) == "Unicode" and u.text:
                            words.append(u.text)
    return " ".join(words)


def _page_bbox(tl: ET.Element) -> Optional[tuple[int, int, int, int]]:
    for child in tl:
        if _local(child.tag) == "Coords":
            pts = child.get("points", "")
            coords = []
            for pair in pts.split():
                try:
                    x, y = pair.split(",")
                    coords.append((int(float(x)), int(float(y))))
                except ValueError:
                    continue
            if coords:
                xs = [c[0] for c in coords]
                ys = [c[1] for c in coords]
                return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
    return None


def parse_any(path: str | Path) -> Page:
    """Dispatch on root element name."""
    head = Path(path).read_text(encoding="utf-8", errors="replace")[:4000]
    if re.search(r"<\s*(\w+:)?alto[\s>]", head, re.IGNORECASE):
        return parse_alto(path)
    if re.search(r"<\s*(\w+:)?PcGts[\s>]", head):
        return parse_page_xml(path)
    raise ValueError(f"Unrecognised XML dialect: {path}")


def _int_or_none(v) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
