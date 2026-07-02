import json

from harvest.parsing import Line, Page
from harvest.segcomplexity import (
    coverage_gap, detect_columns, flow_disorder, height_dispersion,
    score_page, wide_box_rate,
)


def _tok(text, x, y, w=40, h=20):
    return Line(id=text, text=text, bbox=(x, y, w, h))


def _clean_single_column_page():
    """Dense single column, perfect top-to-bottom reading order (realistic:
    ~8 closely-spaced words per line)."""
    page = Page(source_path="clean", width=1200, height=2000)
    toks = []
    for row in range(12):
        y = 100 + row * 60
        x = 100
        for word in range(8):
            w = 60
            toks.append(_tok(f"w{row}_{word}", x, y, w=w))
            x += w + 15  # small inter-word gap
    page.lines = toks  # already in reading order
    return page


def _messy_two_column_page_out_of_order():
    """Two dense columns, but stream order interleaves them and jumps upward —
    the pathological reading-order case (rats page)."""
    page = Page(source_path="messy", width=2000, height=2000)
    def line(prefix, x0, y):
        return [_tok(f"{prefix}{y}_{i}", x0 + i * 75, y, w=60) for i in range(6)]
    left = [line("L", 150, 100 + r * 60) for r in range(10)]
    right = [line("R", 1150, 100 + r * 60) for r in range(10)]
    # interleave rows with upward jumps: Lrow0 Rrow5 Lrow1 Rrow0 ...
    stream = []
    for i in range(10):
        stream += left[i]
        stream += right[(i * 5) % 10]
    page.lines = stream
    return page


def test_detect_columns_single_vs_double():
    single = _clean_single_column_page()
    bands = detect_columns(single.lines, single.width)
    assert len(bands) == 1  # dense single column stays one column

    messy = _messy_two_column_page_out_of_order()
    bands2 = detect_columns(messy.lines, messy.width)
    assert len(bands2) == 2  # clear gutter between the two columns


def test_flow_disorder_low_for_clean_high_for_messy():
    clean = _clean_single_column_page()
    bc = detect_columns(clean.lines, clean.width)
    d_clean = flow_disorder(clean.lines, bc)

    messy = _messy_two_column_page_out_of_order()
    bm = detect_columns(messy.lines, messy.width)
    d_messy = flow_disorder(messy.lines, bm)

    assert d_clean < 0.05
    assert d_messy > d_clean
    assert d_messy > 3 * d_clean  # interleaving materially raises disorder


def test_wide_box_rate_flags_merged_lines():
    page = Page(source_path="w", width=1200)
    toks = [_tok(f"n{i}", 100 + i * 50, 100, w=40) for i in range(20)]
    toks.append(_tok("MERGED", 100, 200, w=1000))  # one very wide box
    page.lines = toks
    assert wide_box_rate(page.lines) > 0
    assert wide_box_rate(page.lines) < 0.1  # just one out of 21


def test_height_dispersion_low_uniform_high_mixed():
    uniform = Page(source_path="u")
    uniform.lines = [_tok(f"n{i}", 100 + i * 50, 100, h=20) for i in range(20)]
    assert height_dispersion(uniform.lines) < 0.1

    mixed = Page(source_path="m")
    mixed.lines = ([_tok(f"n{i}", 100 + i * 50, 100, h=20) for i in range(10)] +
                   [_tok(f"T{i}", 100 + i * 50, 300, h=200) for i in range(10)])
    assert height_dispersion(mixed.lines) > 0.5


def test_coverage_gap_dense_vs_sparse():
    dense = Page(source_path="d", width=1000, height=1000)
    dense.lines = [_tok(f"n{i}", 100 + (i % 10) * 40, 100 + (i // 10) * 30, w=38, h=28)
                   for i in range(100)]
    sparse = Page(source_path="s", width=1000, height=1000)
    sparse.lines = [_tok("a", 100, 100), _tok("b", 900, 100),
                    _tok("c", 100, 900), _tok("d", 900, 900), _tok("e", 500, 500)]
    assert coverage_gap(dense.lines, 1000, 1000) < coverage_gap(sparse.lines, 1000, 1000)


def test_score_page_orders_clean_below_messy():
    clean = score_page(_clean_single_column_page(), ark="a", doctype="mono")
    messy = score_page(_messy_two_column_page_out_of_order(), ark="b", doctype="presse")
    assert not clean.empty and not messy.empty
    assert 0.0 <= clean.complexity <= 1.0
    assert 0.0 <= messy.complexity <= 1.0
    assert messy.complexity > clean.complexity


def test_empty_page_flagged():
    page = Page(source_path="e", width=1000)
    page.lines = [_tok("x", 10, 10)]  # < 4 tokens
    pc = score_page(page)
    assert pc.empty
    assert pc.complexity == 0.0


def test_no_ocr_page_flagged_has_ocr_false():
    """An AnnotationPage with zero items = no production OCR (the frequent case
    for old prints and manuscripts on Gallica)."""
    from harvest.parsing import Page
    from harvest.segcomplexity import score_page
    empty_page = Page(source_path="noocr", width=2000, height=3000)
    empty_page.lines = []
    pc = score_page(empty_page, ark="bpt6kX", doctype="monographie")
    assert pc.empty
    assert pc.has_ocr is False
    assert pc.complexity == 0.0


def test_sparse_but_present_ocr_distinguished():
    from harvest.parsing import Line, Page
    from harvest.segcomplexity import score_page
    page = Page(source_path="sparse", width=2000)
    page.lines = [Line(id="a", text="x", bbox=(10, 10, 20, 20))]  # 1 token
    pc = score_page(page)
    assert pc.empty          # too few tokens to score
    assert pc.has_ocr is True  # but OCR *is* present, just sparse


def test_projection_columns_robust_to_mixed_widths():
    """The real-page failure: vertical title tokens (very wide/tall) mixed with
    body text must NOT explode into hundreds of columns."""
    from harvest.parsing import Line, Page
    from harvest.segcomplexity import detect_columns
    page = Page(source_path="mixed", width=2400)
    toks = []
    # a vertical title: a few very tall/wide boxes on the left
    for i, y in enumerate([200, 500, 900, 1300]):
        toks.append(Line(id=f"T{i}", text="TITRE", bbox=(560, y, 60, 250)))
    # dense body text in a single right-hand column
    for r in range(15):
        x = 1000
        for w in range(10):
            toks.append(Line(id=f"b{r}_{w}", text="mot", bbox=(x, 300 + r * 40, 70, 20)))
            x += 85
    page.lines = toks
    bands = detect_columns(page.lines, page.width)
    assert len(bands) <= 3   # title column + body column, NOT hundreds
