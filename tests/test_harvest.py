import json
from pathlib import Path

import pytest

from harvest.align import align_pages, cer, edit_script, text_similarity
from harvest.gallica import GallicaClient, ark_name
from harvest.gt_sources import recover_arks
from harvest.parsing import parse_any

FIX = Path(__file__).parent / "fixtures"


def test_parse_alto_and_page():
    prod = parse_any(FIX / "prod_f3.alto.xml")
    gt = parse_any(FIX / "gt_f3.page.xml")
    assert prod.width == 2000 and gt.width == 2000
    assert [l.text for l in prod.lines] == [
        "Le gouvernernent a décidé",
        "de convoquer les charnbres pour le 15",
    ]
    assert len(gt.lines) == 4
    assert gt.lines[0].bbox == (100, 200, 1200, 40)
    assert prod.lines[0].confidence == pytest.approx((0.98 + 0.61 + 0.99 + 0.87) / 4)


def test_alignment_detects_recognition_merge_and_missed_zone():
    prod = parse_any(FIX / "prod_f3.alto.xml")
    gt = parse_any(FIX / "gt_f3.page.xml")
    res = align_pages(gt.lines, prod.lines)

    # gt_l1 <-> TL_1 : pure recognition error (rn/m confusion).
    m1 = next(m for m in res.matches if m.gt.id == "gt_l1")
    assert m1.prod.id == "TL_1"
    assert m1.iou > 0.5
    assert cer(m1.prod.text, m1.gt.text) > 0

    # TL_2 spans gt_l2 + gt_l3: the merge must be flagged.
    assert any(s.id in ("gt_l2", "gt_l3") for s, _ in res.split_merge_suspects) or \
        any(len(p) >= 1 for _, p in res.split_merge_suspects)

    # The marginal GT zone has no production counterpart: missed zone.
    assert any(g.id == "gt_marginal" for g in res.unmatched_gt)


def test_edit_script_roundtrip_and_cer():
    ocr = "de convoquer les charnbres"
    gt = "de convoquer les chambres"
    ops = edit_script(ocr, gt)
    rebuilt_gt = "".join(o.gt for o in ops)
    rebuilt_ocr = "".join(o.ocr for o in ops)
    assert rebuilt_gt == gt and rebuilt_ocr == ocr
    assert any(o.op == "replace" for o in ops)
    assert 0 < cer(ocr, gt) < 0.15
    assert cer(gt, gt) == 0.0


def test_similarity_tolerates_long_s_and_case():
    assert text_similarity("Les Eſpèces", "les espèces") > 0.9


def test_ark_name_and_crop_url(tmp_path):
    assert ark_name("ark:/12148/bpt6k5530456s") == "bpt6k5530456s"
    assert ark_name("bpt6k5530456s") == "bpt6k5530456s"
    c = GallicaClient(cache_dir=tmp_path)
    url = c.iiif_crop_url("bpt6k5530456s", 3, (100, 200, 1200, 40))
    assert "/f3/92,192,1216,56/" in url  # 8px padding applied


def test_recover_arks_from_filenames(tmp_path):
    d = tmp_path / "ds"
    d.mkdir()
    (d / "bpt6kabc123_f12.xml").write_text("<alto/>", encoding="utf-8")
    (d / "notes.xml").write_text(
        "<PcGts><!-- source: ark:/12148/btv1bxyz --></PcGts>", encoding="utf-8"
    )
    entries = recover_arks(d)
    by_file = {e["file"]: e for e in entries}
    assert by_file["bpt6kabc123_f12.xml"]["ark"] == "bpt6kabc123"
    assert by_file["bpt6kabc123_f12.xml"]["page_hint"] == 12
    assert by_file["notes.xml"]["ark"] == "btv1bxyz"


def test_sampling_allocation_and_draw(tmp_path):
    from harvest.sampling import allocate, draw_sample, load_inventory

    inv = tmp_path / "inv.csv"
    rows = ["ark,doctype,period"]
    rows += [f"bpt6kpress{i},presse,1850-1880" for i in range(70)]
    rows += [f"bpt6kmono{i},monographie,1820-1850" for i in range(30)]
    inv.write_text("\n".join(rows), encoding="utf-8")

    loaded = load_inventory(inv, ["doctype", "period"])
    alloc = allocate(loaded, n_pages=100, floor=10)
    assert sum(alloc.values()) == 100
    assert alloc["presse|1850-1880"] > alloc["monographie|1820-1850"] >= 10

    # Offline mode now emits ONE row per document (page resolved later),
    # so the sample size is capped by the number of available documents.
    sample = draw_sample(loaded, alloc, client=None, pages_per_doc=2, seed=1)
    assert all(s["page"] is None for s in sample)          # offline
    arks = [s["ark"] for s in sample]
    assert len(arks) == len(set(arks))                     # no duplicate docs
    assert len(sample) <= 100                              # capped by inventory
    assert len(sample) >= 80                               # most docs used
    # Reproducibility
    sample2 = draw_sample(loaded, alloc, client=None, pages_per_doc=2, seed=1)
    assert json.dumps(sample) == json.dumps(sample2)
