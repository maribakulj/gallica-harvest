import json

from harvest.align import align_pages
from harvest.iiif3 import crop_url_from_service, parse_annotation_page, parse_manifest
from harvest.inventory_dump import build_from_dump, parse_periods
from harvest.parsing import parse_any
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"

ANNO_PAGE = {
    "@context": "http://iiif.io/api/presentation/3/context.json",
    "id": "https://openapi.bnf.fr/iiif/presentation/v3/ark:/12148/bpt6ktest/f3/annotationpage/supplementing.json",
    "type": "AnnotationPage",
    "items": [
        {   # target as string with xywh fragment
            "id": "anno1", "type": "Annotation", "motivation": "supplementing",
            "body": {"type": "TextualBody", "value": "Le gouvernernent a décidé",
                     "format": "text/plain"},
            "target": "https://.../canvas/f3#xywh=100,200,1200,40",
        },
        {   # target as object with FragmentSelector, motivation as list
            "id": "anno2", "type": "Annotation",
            "motivation": ["supplementing"],
            "body": [{"type": "TextualBody", "value": "de convoquer les charnbres pour le 15"}],
            "target": {
                "source": "https://.../canvas/f3",
                "selector": {"type": "FragmentSelector",
                             "value": "xywh=pixel:100,250,1200,90"},
            },
        },
        {   # painting annotation must be ignored
            "id": "anno3", "type": "Annotation", "motivation": "painting",
            "body": {"type": "Image", "id": "https://.../image.jpg"},
            "target": "https://.../canvas/f3",
        },
    ],
}

MANIFEST = {
    "type": "Manifest",
    "items": [
        {
            "type": "Canvas", "width": 2000, "height": 3000,
            "items": [{
                "type": "AnnotationPage",
                "items": [{
                    "type": "Annotation", "motivation": "painting",
                    "body": {
                        "type": "Image",
                        "service": [{
                            "id": "https://openapi.bnf.fr/iiif/image/v3/ark:/12148/bpt6ktest/f1",
                            "type": "ImageService3",
                        }],
                    },
                }],
            }],
        },
        {"type": "Canvas", "width": 2000, "height": 3000, "items": []},
    ],
}


def test_parse_annotation_page_shapes():
    page = parse_annotation_page(json.dumps(ANNO_PAGE))
    assert len(page.lines) == 2  # painting ignored
    assert page.lines[0].bbox == (100, 200, 1200, 40)
    assert page.lines[1].bbox == (100, 250, 1200, 90)  # pixel: prefix handled
    assert page.lines[1].text.startswith("de convoquer")


def test_annotations_align_with_page_gt():
    """OpenAPI annotations must slot into the same alignment path as ALTO."""
    prod = parse_annotation_page(json.dumps(ANNO_PAGE))
    gt = parse_any(FIX / "gt_f3.page.xml")
    res = align_pages(gt.lines, prod.lines)
    assert any(m.gt.id == "gt_l1" and m.iou > 0.5 for m in res.matches)
    assert any(g.id == "gt_marginal" for g in res.unmatched_gt)


def test_parse_manifest_and_crop_url():
    man = parse_manifest(json.dumps(MANIFEST))
    assert man["n_pages"] == 2
    assert man["ocr_rate"] is None  # no OCR metadata in this fixture
    svc = man["canvases"][0]["image_service"]
    assert svc.endswith("/f1")
    url = crop_url_from_service(svc, (100, 200, 1200, 40))
    assert url.endswith("/92,192,1216,56/max/0/default.jpg")


def test_manifest_ocr_rate():
    from harvest.iiif3 import manifest_ocr_rate
    # Present: language-map metadata entry as served by openapi.bnf.fr.
    with_ocr = {"type": "Manifest", "items": [], "metadata": [
        {"label": {"fr": ["Langue"], "en": ["Language"]},
         "value": {"fr": ["Français"]}},
        {"label": {"fr": ["Taux OCR"], "en": ["OCR rate"]},
         "value": {"fr": ["9,12 %"]}},  # comma decimal, as BnF serves it
    ]}
    assert abs(manifest_ocr_rate(json.dumps(with_ocr)) - 0.0912) < 1e-9
    assert abs(parse_manifest(with_ocr)["ocr_rate"] - 0.0912) < 1e-9
    # Absent -> None (documents without production OCR omit the field).
    without = {"type": "Manifest", "items": [], "metadata": [
        {"label": {"fr": ["Langue"]}, "value": {"fr": ["Français"]}}]}
    assert manifest_ocr_rate(json.dumps(without)) is None


def test_inventory_from_dump(tmp_path):
    dump = tmp_path / "dump.csv"
    lines = ["identifiant;titre;date;langue;ocr"]
    for i in range(20):
        lines.append(f"https://gallica.bnf.fr/ark:/12148/bpt6kaa{i};Journal A;{1830+i};fre;1")
    lines.append("https://gallica.bnf.fr/ark:/12148/bpt6knoocr;Sans OCR;1840;fre;0")
    lines.append("https://gallica.bnf.fr/ark:/12148/bpt6kdeutsch;Zeitung;1840;ger;1")
    lines.append("https://gallica.bnf.fr/ark:/12148/bpt6knodate;Sans date;;fre;1")
    dump.write_text("\n".join(lines), encoding="utf-8")

    rows = build_from_dump(
        dump, doctype="presse",
        periods=parse_periods("1821-1880"),
        per_stratum=10, seed=7,
    )
    assert len(rows) == 10
    arks = {r["ark"] for r in rows}
    assert "bpt6knoocr" not in arks       # ocr=0 filtered
    assert "bpt6kdeutsch" not in arks     # lang filtered
    assert all(r["period"] == "1821-1880" for r in rows)
    # Reproducible draw
    rows2 = build_from_dump(dump, doctype="presse",
                            periods=parse_periods("1821-1880"),
                            per_stratum=10, seed=7)
    assert [r["ark"] for r in rows] == [r["ark"] for r in rows2]


def test_group_tokens_into_lines_two_columns():
    """Word-level tokens on two columns sharing y-bands must yield 4 lines,
    not 2 lines concatenated across the gutter."""
    from harvest.iiif3 import group_tokens_into_lines
    from harvest.parsing import Line, Page

    # Left column x in [100..300], right column x in [900..1100], big gutter.
    def tok(text, x, y, w=40, h=30):
        return Line(id=text, text=text, bbox=(x, y, w, h))

    page = Page(source_path="t", width=1200, height=800)
    page.lines = [
        # row 1
        tok("Le", 100, 100), tok("chat", 150, 100),
        tok("Le", 900, 100), tok("chien", 950, 100),
        # row 2
        tok("dort", 100, 140), tok("ici", 150, 140),
        tok("court", 900, 140), tok("là", 960, 140),
    ]
    grouped = group_tokens_into_lines(page)
    texts = sorted(l.text for l in grouped.lines)
    assert len(grouped.lines) == 4, texts
    assert "Le chat" in texts and "Le chien" in texts
    assert "dort ici" in texts and "court là" in texts


def test_group_tokens_single_line_no_false_split():
    from harvest.iiif3 import group_tokens_into_lines
    from harvest.parsing import Line, Page

    page = Page(source_path="t")
    page.lines = [
        Line(id=str(i), text=w, bbox=(100 + i * 60, 200, 50, 30))
        for i, w in enumerate(["RAPPORT", "SUR", "DIFFÉRENTS", "PROCÉDÉS"])
    ]
    grouped = group_tokens_into_lines(page)
    assert len(grouped.lines) == 1
    assert grouped.lines[0].text == "RAPPORT SUR DIFFÉRENTS PROCÉDÉS"


def test_group_preserves_reading_order_top_to_bottom():
    from harvest.iiif3 import group_tokens_into_lines
    from harvest.parsing import Line, Page

    page = Page(source_path="t")
    page.lines = [
        Line(id="b", text="second", bbox=(100, 300, 80, 30)),
        Line(id="a", text="first", bbox=(100, 100, 80, 30)),
        Line(id="c", text="third", bbox=(100, 500, 80, 30)),
    ]
    grouped = group_tokens_into_lines(page)
    assert [l.text for l in grouped.lines] == ["first", "second", "third"]


def test_annotation_sheet_generation(tmp_path):
    from harvest.sheet import generate_sheet
    rows = [
        {"ark": "bpt6kaaa", "page": 3, "doctype": "presse",
         "period": "1821-1880", "title": "La Presse", "source": "x"},
        {"ark": "btv1bbbb", "page": None, "doctype": "tapuscrit",
         "period": "1881-1945", "title": "tapuscrit", "source": "y"},
    ]
    out = generate_sheet(rows, tmp_path / "sheet.html")
    html_text = out.read_text(encoding="utf-8")
    assert "gallica.bnf.fr/ark:/12148/bpt6kaaa/f3.item" in html_text
    assert "f3.texteBrut" in html_text
    assert "btv1bbbb/f1.item" in html_text          # page None -> f1 défaut
    assert "Fusion de lignes" in html_text
    assert "exportCSV" in html_text
    assert "2" in html_text and "ROWS = [" in html_text
