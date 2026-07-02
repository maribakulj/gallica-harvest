"""Stratified sampling for the phase-0 diagnostic.

Input: a CSV inventory with at least an `ark` column plus arbitrary stratum
columns (e.g. doctype, period, ocr_conf_bin). You produce that inventory from
whatever internal catalogue view you trust; this module only does the
allocation and the per-page draw.

Allocation: proportional to stratum size with a floor per stratum, then
largest-remainder rounding. Page draw: uniform over the document's views
(via the Pagination service), seeded, reproducible.
"""
from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

from .gallica import GallicaClient


def load_inventory(csv_path: str | Path, strata_cols: list[str]) -> list[dict]:
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["_stratum"] = "|".join(row.get(c, "?").strip() for c in strata_cols)
            rows.append(row)
    if not rows:
        raise SystemExit(f"Empty inventory: {csv_path}")
    return rows


def allocate(rows: list[dict], n_pages: int, floor: int = 10) -> dict[str, int]:
    by_stratum: dict[str, int] = defaultdict(int)
    for r in rows:
        by_stratum[r["_stratum"]] += 1
    total = sum(by_stratum.values())
    k = len(by_stratum)
    if n_pages < k * floor:
        raise SystemExit(
            f"n_pages={n_pages} < {k} strata x floor {floor}; lower the floor or merge strata."
        )
    budget = n_pages - k * floor
    quotas = {s: budget * c / total for s, c in by_stratum.items()}
    alloc = {s: floor + int(q) for s, q in quotas.items()}
    remainders = sorted(quotas.items(), key=lambda t: -(t[1] - int(t[1])))
    short = n_pages - sum(alloc.values())
    for s, _ in remainders[:short]:
        alloc[s] += 1
    return dict(alloc)


def draw_sample(
    rows: list[dict],
    alloc: dict[str, int],
    client: Optional[GallicaClient] = None,
    pages_per_doc: int = 2,
    seed: int = 20260702,
    page_count_fn: Optional[Callable[[str], int]] = None,
) -> list[dict]:
    """Pick documents per stratum, then pages within documents.

    If a GallicaClient is provided, real page counts are fetched and several
    pages may be drawn per document; otherwise page numbers are drawn later
    (entries get page=None placeholders, one row per document).

    page_count_fn overrides how a document's page count is resolved. Default is
    `client.page_count` (legacy Pagination on gallica.bnf.fr, Datadome-gated);
    pass `client.page_count_v3` to resolve via openapi.bnf.fr instead — the only
    route that works outside the BnF network.
    """
    rng = random.Random(seed)
    if client is not None and page_count_fn is None:
        page_count_fn = client.page_count
    by_stratum: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_stratum[r["_stratum"]].append(r)

    sample = []
    for stratum, n in alloc.items():
        docs = by_stratum[stratum][:]
        rng.shuffle(docs)
        need = n
        for doc in docs:
            if need <= 0:
                break
            take = min(pages_per_doc, need)
            pages: list[Optional[int]]
            if client is not None:
                try:
                    count = page_count_fn(doc["ark"])
                    pages = rng.sample(range(1, count + 1), min(take, count))
                except Exception as e:  # noqa: BLE001 - log and skip document
                    print(f"  ! {doc['ark']}: {e}")
                    continue
            else:
                # Offline: page numbers aren't known yet and will be resolved
                # at harvest/score time. Emit ONE row per document (not `take`
                # identical null-page rows, which would double-count).
                pages = [None]
            for p in pages:
                sample.append({**{k: v for k, v in doc.items() if k != "_stratum"},
                               "stratum": stratum, "page": p})
                need -= 1
        if need > 0:
            print(f"  ! stratum {stratum}: short by {need} pages (inventory too small)")
    return sample


def write_manifest(sample: list[dict], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in sample:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
