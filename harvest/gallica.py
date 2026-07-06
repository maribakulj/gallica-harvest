"""Minimal, polite Gallica client.

Endpoints (override any of them in config.yaml if you use internal variants):
  - Pagination service : number of views for an ARK
  - RequestDigitalElement : per-page production ALTO
  - IIIF Image API     : full pages and line crops
  - OAIRecord service  : Dublin Core metadata (type, date) for stratification

All responses are cached on disk under cache_dir so the sampling/diagnostic
work never re-hits Gallica for the same object twice. A configurable delay is
applied between live requests; keep it >= 1s on the public endpoints.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

# Base host for the BnF IIIF Presentation v3 API. Configurable via the
# GALLICA_IIIF_BASE environment variable so the concrete gateway is not
# hard-coded; defaults to the public Gallica host.
IIIF_V3_BASE = os.environ.get("GALLICA_IIIF_BASE", "https://gallica.bnf.fr").rstrip("/")

DEFAULT_ENDPOINTS = {
    "pagination": "https://gallica.bnf.fr/services/Pagination?ark={ark_name}",
    "alto": "https://gallica.bnf.fr/RequestDigitalElement?O={ark_name}&E=ALTO&Deb={page}",
    "oai": "https://gallica.bnf.fr/services/OAIRecord?ark={ark_name}",
    # {region} is either 'full' or 'x,y,w,h' in pixel coordinates.
    "iiif_image": "https://gallica.bnf.fr/iiif/ark:/12148/{ark_name}/f{page}/{region}/{size}/0/native.jpg",
    "iiif_manifest": "https://gallica.bnf.fr/iiif/ark:/12148/{ark_name}/manifest.json",
    # IIIF Presentation v3 gateway (not bot-protected; preferred outside the
    # BnF network). Host comes from IIIF_V3_BASE above.
    "iiif_v3_annotations": IIIF_V3_BASE + "/iiif/presentation/v3/ark:/12148/{ark_name}/f{page}/annotationpage/supplementing.json",
    "iiif_v3_manifest": IIIF_V3_BASE + "/iiif/presentation/v3/ark:/12148/{ark_name}/manifest.json",
}

ARK_RE = re.compile(r"ark:/12148/([a-z0-9]+)", re.IGNORECASE)
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def ark_name(ark: str) -> str:
    """'ark:/12148/bpt6k5530456s' -> 'bpt6k5530456s'; tolerant of bare names."""
    m = ARK_RE.search(ark)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-z0-9]+", ark, re.IGNORECASE):
        return ark
    raise ValueError(f"Cannot parse ARK: {ark!r}")


class GallicaClient:
    def __init__(
        self,
        cache_dir: str | Path = "cache",
        delay_s: float = 1.0,
        endpoints: Optional[dict] = None,
        user_agent: str = USER_AGENT,
        max_retries: int = 4,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay_s = delay_s
        self.endpoints = {**DEFAULT_ENDPOINTS, **(endpoints or {})}
        self.user_agent = user_agent
        self.max_retries = max_retries
        self._last_request = 0.0

    # -- low level ---------------------------------------------------------

    def _get(self, url: str, binary: bool = False) -> bytes:
        key = hashlib.sha256(url.encode()).hexdigest()
        ext = ".bin" if binary else ".xml"
        cached = self.cache_dir / (key + ext)
        if cached.exists():
            return cached.read_bytes()

        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        # The IIIF gateway rate-limits bursts with HTTP 429; back off politely
        # and retry rather than dropping the document. Only 200s reach the cache.
        for attempt in range(self.max_retries + 1):
            wait = self.delay_s - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                self._last_request = time.monotonic()
                cached.write_bytes(data)
                return data
            except urllib.error.HTTPError as e:
                self._last_request = time.monotonic()
                if e.code in (429, 503) and attempt < self.max_retries:
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    try:
                        backoff = float(retry_after)
                    except (TypeError, ValueError):
                        backoff = self.delay_s * (2 ** attempt)
                    time.sleep(min(backoff, 30.0))
                    continue
                raise
        raise RuntimeError(f"exhausted retries for {url}")

    # -- services ----------------------------------------------------------

    def page_count(self, ark: str) -> int:
        url = self.endpoints["pagination"].format(ark_name=ark_name(ark))
        root = ET.fromstring(self._get(url))
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] in ("nbVueImages", "nombreVue", "TotalVues"):
                try:
                    return int(el.text)
                except (TypeError, ValueError):
                    pass
        raise RuntimeError(f"Could not read page count for {ark}")

    def fetch_alto(self, ark: str, page: int, dest: Optional[Path] = None) -> Path:
        url = self.endpoints["alto"].format(ark_name=ark_name(ark), page=page)
        data = self._get(url)
        if dest is None:
            dest = self.cache_dir / f"{ark_name(ark)}_f{page}.alto.xml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    def oai_metadata(self, ark: str) -> dict:
        """Return {'type': ..., 'date': ..., 'title': ...} from the OAI record."""
        url = self.endpoints["oai"].format(ark_name=ark_name(ark))
        root = ET.fromstring(self._get(url))
        out = {}
        for el in root.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag in ("type", "date", "title") and el.text and tag not in out:
                out[tag] = el.text.strip()
        return out

    # -- IIIF Presentation v3 ------------------------------------------------

    def fetch_annotations(self, ark: str, page: int,
                          dest: Optional[Path] = None) -> Path:
        """Per-page OCR text as a supplementing AnnotationPage (JSON)."""
        url = self.endpoints["iiif_v3_annotations"].format(
            ark_name=ark_name(ark), page=page)
        data = self._get(url, binary=True)
        if dest is None:
            dest = self.cache_dir / f"{ark_name(ark)}_f{page}.annotations.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    def fetch_manifest(self, ark: str) -> bytes:
        url = self.endpoints["iiif_v3_manifest"].format(ark_name=ark_name(ark))
        return self._get(url, binary=True)

    def page_count_v3(self, ark: str) -> int:
        from .iiif3 import parse_manifest
        return parse_manifest(self.fetch_manifest(ark))["n_pages"]

    def ocr_rate(self, ark: str) -> Optional[float]:
        """Document-level OCR rate in [0,1] from the IIIF manifest, or None
        when the document carries no production OCR. Reachable stand-in for the
        Datadome-gated OAIRecord `nqa_score`; presence also flags OCR-available.
        """
        from .iiif3 import manifest_ocr_rate
        return manifest_ocr_rate(self.fetch_manifest(ark))

    # -- IIIF --------------------------------------------------------------

    def iiif_page_url(self, ark: str, page: int, size: str = "full") -> str:
        return self.endpoints["iiif_image"].format(
            ark_name=ark_name(ark), page=page, region="full", size=size
        )

    def iiif_crop_url(
        self, ark: str, page: int, bbox: tuple[int, int, int, int],
        pad: int = 8, size: str = "full",
    ) -> str:
        x, y, w, h = bbox
        region = f"{max(0, x - pad)},{max(0, y - pad)},{w + 2 * pad},{h + 2 * pad}"
        return self.endpoints["iiif_image"].format(
            ark_name=ark_name(ark), page=page, region=region, size=size
        )

    def download_image(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._get(url, binary=True))
        return dest
