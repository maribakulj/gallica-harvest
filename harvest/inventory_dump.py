"""Build the inventory from the official BnF collection-metadata CSV dumps
(api.bnf.fr, "Gallica : métadonnées de la collection numérique" and related
per-doctype datasets, Licence ouverte de l'État).

These dumps are the robust external path when SRU is unreachable: static
files, semicolon-separated, with columns such as
  identifiant;titre;date;auteur;langue;...;ocr;...;#pages
Exact schemas vary per dump, so column names are configurable.

Usage:
  python3 -m harvest.cli inventory-from-dump dump-presse.csv \
      --doctype presse --periods 1780-1820,1821-1880,1881-1945 \
      --per-stratum 80 --lang fre -o inventaire.csv
Runs fully offline once the dump is downloaded.
"""
from __future__ import annotations

import csv
import random
import re
import sys
from pathlib import Path

from .gallica import ARK_RE

YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20[0-2]\d)\b")


def parse_periods(spec: str) -> list[tuple[int, int, str]]:
    """'1780-1820,1821-1880' -> [(1780,1820,'1780-1820'), ...]"""
    out = []
    for chunk in spec.split(","):
        a, b = chunk.split("-")
        out.append((int(a), int(b), chunk.strip()))
    return out


def year_of(datefield: str) -> int | None:
    m = YEAR_RE.search(datefield or "")
    return int(m.group(1)) if m else None


def build_from_dump(
    dump_path: str | Path,
    doctype: str,
    periods: list[tuple[int, int, str]],
    per_stratum: int = 80,
    col_ark: str = "identifiant",
    col_date: str = "date",
    col_title: str = "titre",
    col_lang: str = "langue",
    lang: str | None = "fre",
    col_ocr: str = "ocr",
    require_ocr: bool = True,
    delimiter: str = ";",
    seed: int = 20260702,
) -> list[dict]:
    buckets: dict[str, list[dict]] = {label: [] for _, _, label in periods}

    with open(dump_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        if reader.fieldnames is None or col_ark not in reader.fieldnames:
            sys.exit(
                f"Colonne '{col_ark}' absente. Colonnes du dump: {reader.fieldnames}\n"
                f"Ajuster --col-ark/--col-date/--delimiter."
            )
        has_lang = col_lang in reader.fieldnames
        has_ocr = col_ocr in reader.fieldnames

        for row in reader:
            if lang and has_lang and lang not in (row.get(col_lang) or ""):
                continue
            if require_ocr and has_ocr:
                ocr_val = (row.get(col_ocr) or "").strip().lower()
                if ocr_val in ("", "0", "false", "non", "no"):
                    continue
            m = ARK_RE.search(row.get(col_ark) or "")
            if not m:
                continue
            y = year_of(row.get(col_date) or "")
            if y is None:
                continue
            for lo, hi, label in periods:
                if lo <= y <= hi:
                    buckets[label].append({
                        "ark": m.group(1),
                        "doctype": doctype,
                        "period": label,
                        "date": str(y),
                        "title": (row.get(col_title) or "")[:200],
                    })
                    break

    rng = random.Random(seed)
    rows = []
    for label, docs in buckets.items():
        if not docs:
            print(f"  ! strate {doctype}|{label}: 0 document dans le dump")
            continue
        rng.shuffle(docs)
        picked = docs[:per_stratum]
        print(f"  {doctype}|{label}: {len(picked)} tirés / {len(docs)} disponibles")
        rows.extend(picked)
    return rows
