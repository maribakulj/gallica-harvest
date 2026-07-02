"""Registry of public GT sources and utilities to fetch them and to recover
Gallica ARKs from their files.

Fetching strategy:
  - GitHub repos     -> shallow git clone
  - Zenodo records   -> REST API (https://zenodo.org/api/records/{id}) to list
                        files, then direct download
  - HuggingFace      -> left to `datasets`/`huggingface_hub` (see README);
                        listed here for inventory purposes only.

ARK recovery scans XML content (sourceImageInformation/fileName, comments,
custom metadata) and file/directory names for the ark:/12148/... pattern or
bare Gallica identifiers (bpt6k..., btv1b..., cb...).
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

ARK_PATTERNS = [
    re.compile(r"ark:/12148/([a-z0-9]+)", re.IGNORECASE),
    # Bare Gallica document ids as they appear in Gallicorpora/OCR17 filenames.
    re.compile(r"(?<![a-z0-9])(bpt6k[a-z0-9]+)", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])(btv1b[a-z0-9]+)", re.IGNORECASE),
]
PAGE_HINT = re.compile(r"[_\-\.]f?(\d{1,4})(?:[_\-\.]|$)")


@dataclass
class Source:
    key: str
    kind: str          # 'git' | 'zenodo' | 'huggingface' | 'manual'
    locator: str       # clone URL, zenodo record id, HF dataset id, or URL
    description: str
    formats: str
    gallica_sourced: bool  # can we expect to recover ARKs and pair with prod ALTO?


REGISTRY: dict[str, Source] = {
    s.key: s for s in [
        Source("hipe-ocrepair-2026", "git",
               "https://github.com/hipe-eval/HIPE-OCRepair-2026-data.git",
               "HIPE-OCRepair benchmark (overproof, icdar17, impresso-nzz, impresso-snippets, dta19)",
               "aligned text pairs", False),
        Source("icdar2019-postocr", "zenodo", "3515403",
               "ICDAR 2019 post-OCR competition corpus, 22M chars, 10 languages incl. FR (BnF)",
               "aligned text pairs", False),
        Source("newseye-fr", "zenodo", "4293602",
               "NewsEye/READ French newspapers GT, 135 pages, images BnF",
               "PAGE XML + images", True),
        Source("gallicorpora-imprime-18e", "git",
               "https://github.com/Gallicorpora/HTR-imprime-18e-siecle.git",
               "Gallicorpora 18th c. prints (one repo among several; see org)",
               "ALTO + images", True),
        Source("gallicorpora-imprime-17e", "git",
               "https://github.com/Gallicorpora/HTR-imprime-17e-siecle.git",
               "Gallicorpora 17th c. prints", "ALTO + images", True),
        Source("ocr17plus", "git",
               "https://github.com/Heresta/OCR17plus.git",
               "OCR17+ 17th c. French prints, ALTO", "ALTO + images", True),
        Source("fondue-fr-print-16", "git",
               "https://github.com/FoNDUE-HTR/FONDUE-FR-PRINT-16.git",
               "16th c. French prints (FoNDUE)", "ALTO/PAGE + images", True),
        Source("timeus", "git",
               "https://github.com/HTR-United/timeuscorpus.git",
               "TIME-US 18th/19th c. French", "ALTO/PAGE + images", False),
        Source("tapuscorpus", "git",
               "https://github.com/HTR-United/tapuscorpus.git",
               "French 20th c. typewritten (Gallica/Europeana)", "PAGE + images", True),
        Source("dahncorpus", "git",
               "https://github.com/HTR-United/dahncorpus.git",
               "French 20th c. typewritten letters", "ALTO/PAGE + images", False),
        Source("nzz-blackletter", "git",
               "https://github.com/impresso/NZZ-black-letter-ground-truth.git",
               "NZZ 1780-1947, original ABBYY OCR + corrected GT (native pairs)",
               "XML + images", False),
        Source("finlam", "huggingface", "Teklia/Newspapers-finlam",
               "FINLAM 149 issues, zones/classes/reading order (BnF partner project)",
               "HF parquet (images + polygons + text)", True),
        Source("finlam-la-liberte", "huggingface", "Teklia/Newspapers-finlam-La-Liberte",
               "FINLAM La Liberte 1500 issues 1925-1928", "HF parquet", True),
        Source("enp-prima", "manual", "https://www.primaresearch.org/datasets/ENP",
               "Europeana ENP layout+text GT (registration required)",
               "PAGE XML + images", False),
    ]
}


def fetch(key: str, dest_root: str | Path) -> Path:
    src = REGISTRY[key]
    dest = Path(dest_root) / key
    if dest.exists() and any(dest.iterdir()):
        return dest
    dest.mkdir(parents=True, exist_ok=True)

    if src.kind == "git":
        subprocess.run(
            ["git", "clone", "--depth", "1", src.locator, str(dest)],
            check=True,
        )
    elif src.kind == "zenodo":
        api = f"https://zenodo.org/api/records/{src.locator}"
        with urllib.request.urlopen(api, timeout=60) as r:
            record = json.load(r)
        for f in record.get("files", []):
            url = f["links"]["self"]
            name = f.get("key") or f.get("filename")
            print(f"  downloading {name} ...")
            urllib.request.urlretrieve(url, dest / name)
    elif src.kind == "huggingface":
        raise SystemExit(
            f"{key}: use `huggingface_hub`:\n"
            f"  from huggingface_hub import snapshot_download\n"
            f"  snapshot_download('{src.locator}', repo_type='dataset', local_dir='{dest}')"
        )
    else:
        raise SystemExit(f"{key}: manual download required -> {src.locator}")
    return dest


def recover_arks(dataset_dir: str | Path) -> list[dict]:
    """Scan a GT dataset for Gallica ARKs; returns one entry per XML file.

    Each entry: {'file': ..., 'ark': ... or None, 'page_hint': ... or None}.
    Page hints come from filename conventions (f12, _0012, -12) and MUST be
    verified against the Pagination service before trusting them.
    """
    out = []
    root = Path(dataset_dir)
    for path in sorted(root.rglob("*.xml")):
        entry = {"file": str(path.relative_to(root)), "ark": None, "page_hint": None}
        haystacks = [path.name, str(path.parent.relative_to(root))]
        try:
            haystacks.append(path.read_text(encoding="utf-8", errors="replace")[:20000])
        except OSError:
            pass
        for hay in haystacks:
            for pat in ARK_PATTERNS:
                m = pat.search(hay)
                if m:
                    entry["ark"] = m.group(1)
                    break
            if entry["ark"]:
                break
        m = PAGE_HINT.search(path.stem)
        if m:
            entry["page_hint"] = int(m.group(1))
        out.append(entry)
    return out
