"""Build the ARK inventory by querying Gallica's SRU API, stratum by stratum.

Reference: https://api.bnf.fr/fr/api-gallica-de-recherche
  - endpoint: https://gallica.bnf.fr/SRU?version=1.2&operation=searchRetrieve&query=<CQL>
  - useful indexes: dc.type (monographie|fascicule|...), dc.language,
    gallicapublication_date, ocr.quality ("Texte disponible" filters to
    documents that actually have OCR).
  - pagination: startRecord (1-based) + maximumRecords; total in
    srw:numberOfRecords.

Diversification: SRU returns results in engine order, so taking the first N
of each stratum would bias the sample towards whatever the engine favours.
We read numberOfRecords first, then draw random startRecord offsets
(seeded, reproducible) across the whole result set.
"""
from __future__ import annotations

import csv
import random
import re
import urllib.parse
from pathlib import Path
from xml.etree import ElementTree as ET

from .gallica import ARK_RE, GallicaClient

SRU_ENDPOINT = "https://gallica.bnf.fr/SRU"

# (doctype, period, CQL) — adjust freely; ocr.quality clause restricts to
# documents with available OCR text, which is what post-correction targets.
DEFAULT_STRATA: list[tuple[str, str, str]] = [
    ("presse", "1780-1820",
     '(dc.language all "fre") and (dc.type all "fascicule") and '
     '(gallicapublication_date>="1780") and (gallicapublication_date<="1820") and '
     '(ocr.quality all "Texte disponible")'),
    ("presse", "1821-1880",
     '(dc.language all "fre") and (dc.type all "fascicule") and '
     '(gallicapublication_date>="1821") and (gallicapublication_date<="1880") and '
     '(ocr.quality all "Texte disponible")'),
    ("presse", "1881-1945",
     '(dc.language all "fre") and (dc.type all "fascicule") and '
     '(gallicapublication_date>="1881") and (gallicapublication_date<="1945") and '
     '(ocr.quality all "Texte disponible")'),
    ("monographie", "1600-1820",
     '(dc.language all "fre") and (dc.type all "monographie") and '
     '(gallicapublication_date>="1600") and (gallicapublication_date<="1820") and '
     '(ocr.quality all "Texte disponible")'),
    ("monographie", "1821-1880",
     '(dc.language all "fre") and (dc.type all "monographie") and '
     '(gallicapublication_date>="1821") and (gallicapublication_date<="1880") and '
     '(ocr.quality all "Texte disponible")'),
    ("monographie", "1881-1945",
     '(dc.language all "fre") and (dc.type all "monographie") and '
     '(gallicapublication_date>="1881") and (gallicapublication_date<="1945") and '
     '(ocr.quality all "Texte disponible")'),
]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def sru_url(query: str, start: int = 1, maximum: int = 50,
            endpoint: str = SRU_ENDPOINT) -> str:
    params = {
        "version": "1.2",
        "operation": "searchRetrieve",
        "collapsing": "false",
        "query": query,
        "startRecord": str(start),
        "maximumRecords": str(maximum),
    }
    return endpoint + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def parse_sru_response(xml_bytes: bytes) -> tuple[int, list[dict]]:
    """Return (numberOfRecords, [{'ark', 'title', 'date', 'type'}...])."""
    root = ET.fromstring(xml_bytes)
    total = 0
    for el in root.iter():
        if _local(el.tag) == "numberOfRecords":
            try:
                total = int(el.text)
            except (TypeError, ValueError):
                pass
            break

    records = []
    for rec in root.iter():
        if _local(rec.tag) != "record":
            continue
        entry = {"ark": None, "title": None, "date": None, "type": None}
        for el in rec.iter():
            tag = _local(el.tag)
            text = (el.text or "").strip()
            if not text:
                continue
            if tag == "identifier" and entry["ark"] is None:
                m = ARK_RE.search(text)
                if m:
                    entry["ark"] = m.group(1)
            elif tag == "title" and entry["title"] is None:
                entry["title"] = text[:200]
            elif tag == "date" and entry["date"] is None:
                entry["date"] = text
            elif tag == "type" and entry["type"] is None:
                entry["type"] = text
        if entry["ark"]:
            records.append(entry)
    return total, records


def harvest_stratum(
    client: GallicaClient,
    query: str,
    n_docs: int,
    seed: int = 20260702,
    page_size: int = 50,
    endpoint: str = SRU_ENDPOINT,
) -> list[dict]:
    """Draw ~n_docs records at random offsets across the stratum's result set."""
    try:
        first = client._get(sru_url(query, start=1, maximum=1, endpoint=endpoint))
    except Exception as e:
        print(f"  ! SRU error: {e}")
        print(f"     query: {query[:100]}...")
        return []
    total, _ = parse_sru_response(first)
    if total == 0:
        print(f"  ! 0 résultats pour: {query[:80]}...")
        return []

    rng = random.Random(seed + hash(query) % 10_000)
    n_batches = max(1, -(-n_docs // page_size))  # ceil
    max_start = max(1, total - page_size + 1)
    starts = sorted(rng.sample(range(1, max_start + 1),
                               min(n_batches, max_start)))

    out, seen = [], set()
    for s in starts:
        data = client._get(sru_url(query, start=s, maximum=page_size,
                                   endpoint=endpoint))
        _, records = parse_sru_response(data)
        for r in records:
            if r["ark"] not in seen:
                seen.add(r["ark"])
                out.append(r)
        if len(out) >= n_docs:
            break
    rng.shuffle(out)
    return out[:n_docs]


def build_inventory(
    client: GallicaClient,
    strata: list[tuple[str, str, str]] | None = None,
    per_stratum: int = 60,
    seed: int = 20260702,
    endpoint: str = SRU_ENDPOINT,
) -> list[dict]:
    strata = strata or DEFAULT_STRATA
    rows = []
    for doctype, period, query in strata:
        print(f"stratum {doctype}|{period} ...")
        recs = harvest_stratum(client, query, per_stratum, seed=seed,
                               endpoint=endpoint)
        print(f"  -> {len(recs)} documents")
        for r in recs:
            rows.append({
                "ark": r["ark"], "doctype": doctype, "period": period,
                "date": r.get("date") or "", "title": r.get("title") or "",
            })
    return rows


def write_inventory(rows: list[dict], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ark", "doctype", "period", "date", "title"])
        w.writeheader()
        w.writerows(rows)
