import csv

from harvest.calibrate import (
    correlate,
    load_annotations,
    load_scores,
    spearman,
)


def test_spearman_monotonic():
    xs = [1, 2, 3, 4, 5]
    assert abs(spearman(xs, [10, 20, 30, 40, 50]) - 1.0) < 1e-9   # perfect
    assert abs(spearman(xs, [50, 40, 30, 20, 10]) + 1.0) < 1e-9   # inverse
    # non-linear but monotonic -> Spearman still 1 (rank-based)
    assert abs(spearman(xs, [1, 4, 9, 16, 25]) - 1.0) < 1e-9
    assert spearman([1, 1, 1], [1, 2, 3]) is None                 # constant -> undefined
    assert spearman([1], [1]) is None                             # n<2


def test_spearman_handles_ties():
    # tied human ranks must not crash and stay in [-1, 1]
    rho = spearman([1, 1, 2, 2, 3], [0.1, 0.2, 0.2, 0.3, 0.4])
    assert rho is not None and -1.0 <= rho <= 1.0 and rho > 0


def test_load_annotations_and_correlate(tmp_path):
    # Emulate the sheet export: ';'-separated, BOM, '1'/'' checkboxes.
    path = tmp_path / "annot.csv"
    header = ["ark", "page", "doctype", "period", "title", "source",
              "seg_fusion", "seg_scission", "seg_colonnes", "seg_ordre",
              "seg_zone_manquee", "seg_typage_bloc",
              "rec_substitution", "rec_diacritiques", "rec_ligatures",
              "rec_casse", "rec_ponctuation", "rec_mots", "indecidable",
              "_gravite", "_note", "_fait"]
    def row(ark, page, segs, gravite, done):
        d = {c: "" for c in header}
        d.update(ark=ark, page=page)
        for s in segs:
            d[s] = "1"
        d["_gravite"] = gravite
        d["_fait"] = "1" if done else ""
        return [d[c] for c in header]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(header)
        w.writerow(row("arkA", "1", ["seg_fusion"], "mineure", True))          # seg_count 1
        w.writerow(row("arkB", "2", ["seg_fusion", "seg_ordre"], "notable", True))   # 2
        w.writerow(row("arkC", "3", ["seg_fusion", "seg_ordre", "seg_colonnes"], "severe", True))  # 3
        w.writerow(row("arkD", "4", [], "ras", False))                          # not done -> dropped

    ann = load_annotations(path)
    assert ann[("arkA", "1")]["seg_count"] == 1
    assert ann[("arkC", "3")]["seg_count"] == 3
    assert ann[("arkC", "3")]["gravite"] == 3       # severe
    assert ann[("arkD", "4")]["done"] is False

    scores = {("arkA", "1"): 0.10, ("arkB", "2"): 0.20,
              ("arkC", "3"): 0.30, ("arkD", "4"): 0.05}
    res = correlate(ann, scores, signal="seg_count")
    assert res["n"] == 3                              # arkD dropped (not done)
    assert abs(res["spearman"] - 1.0) < 1e-9          # seg_count tracks complexity


def test_load_scores_filters_unusable(tmp_path):
    path = tmp_path / "scores.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ark", "page", "complexity", "empty", "has_ocr"])
        w.writeheader()
        w.writerow({"ark": "a", "page": "1", "complexity": "0.2", "empty": "False", "has_ocr": "True"})
        w.writerow({"ark": "b", "page": "2", "complexity": "0.0", "empty": "True", "has_ocr": "True"})
        w.writerow({"ark": "c", "page": "3", "complexity": "0.0", "empty": "False", "has_ocr": "False"})
    scores = load_scores(path)
    assert set(scores) == {("a", "1")}               # only OCR-bearing, non-empty
