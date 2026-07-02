"""Command-line interface.

Subcommands
-----------
sample      Build the stratified phase-0 sample from an ARK inventory CSV.
harvest     Fetch production ALTO + page images (+ crop URLs) for a manifest.
gt-list     Show the GT source registry.
gt-fetch    Download one or all GT sources.
gt-arks     Recover Gallica ARKs from a fetched GT dataset.
triplets    Align a GT dataset against production ALTO and emit JSONL triplets
            (image crop IIIF URL, OCR hypothesis, GT text, CER, edit script).
stats       Quick corpus statistics over a triplets JSONL.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections import Counter
from pathlib import Path

from . import gt_sources, sampling
from .align import align_pages, cer, edit_script
from .gallica import GallicaClient
from .parsing import parse_any


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gallica-harvest")
    ap.add_argument("--cache", default="cache", help="HTTP cache directory")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between live requests")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("inventory")
    p.add_argument("--per-stratum", type=int, default=60,
                   help="documents to draw per stratum (default presets: 3 périodes x presse/monographie)")
    p.add_argument("--seed", type=int, default=20260702)
    p.add_argument("-o", "--out", default="inventory.csv")

    p = sub.add_parser("inventory-from-dump")
    p.add_argument("dump", help="CSV de métadonnées BnF (api.bnf.fr, ';'-séparé)")
    p.add_argument("--doctype", required=True)
    p.add_argument("--periods", default="1780-1820,1821-1880,1881-1945")
    p.add_argument("--per-stratum", type=int, default=80)
    p.add_argument("--lang", default="fre")
    p.add_argument("--no-require-ocr", action="store_true")
    p.add_argument("--col-ark", default="identifiant")
    p.add_argument("--col-date", default="date")
    p.add_argument("--col-title", default="titre")
    p.add_argument("--delimiter", default=";")
    p.add_argument("--seed", type=int, default=20260702)
    p.add_argument("-o", "--out", default="inventaire.csv")
    p.add_argument("--append", action="store_true",
                   help="ajouter au CSV existant (pour cumuler plusieurs dumps)")

    p = sub.add_parser("sample")
    p.add_argument("inventory", help="CSV with ark + stratum columns")
    p.add_argument("--strata", required=True, help="comma-separated stratum columns")
    p.add_argument("--n", type=int, default=400)
    p.add_argument("--floor", type=int, default=10)
    p.add_argument("--pages-per-doc", type=int, default=2)
    p.add_argument("--seed", type=int, default=20260702)
    p.add_argument("--offline", action="store_true",
                   help="don't resolve page counts now (page=None in manifest)")
    p.add_argument("-o", "--out", default="out/sample.jsonl")

    p = sub.add_parser("harvest")
    p.add_argument("manifest", help="sample.jsonl")
    p.add_argument("--dest", default="out/pages")
    p.add_argument("--images", action="store_true", help="also download page images")
    p.add_argument("--source", choices=["openapi", "legacy"], default="openapi",
                   help="openapi = IIIF v3 annotations (défaut, hors BnF) ; "
                        "legacy = RequestDigitalElement ALTO (réseau BnF)")

    sub.add_parser("gt-list")

    p = sub.add_parser("gt-fetch")
    p.add_argument("key", help="source key from gt-list, or 'all-git'")
    p.add_argument("--dest", default="gt")

    p = sub.add_parser("gt-arks")
    p.add_argument("dataset_dir")
    p.add_argument("-o", "--out", default=None)

    p = sub.add_parser("triplets")
    p.add_argument("mapping", help="JSONL from gt-arks (file, ark, page_hint), filtered/verified")
    p.add_argument("--gt-root", required=True)
    p.add_argument("--source", choices=["openapi", "legacy"], default="openapi")
    p.add_argument("--min-iou", type=float, default=0.30)
    p.add_argument("--min-sim", type=float, default=0.55)
    p.add_argument("-o", "--out", default="out/triplets.jsonl")

    p = sub.add_parser("group-preview",
                       help="reconstruire les lignes depuis un fichier d'annotations mot-à-mot et les afficher")
    p.add_argument("annotations_json")
    p.add_argument("--y-overlap", type=float, default=0.5)
    p.add_argument("--x-gap", type=float, default=2.5)

    p = sub.add_parser("seg-score",
                       help="scorer la complexité de segmentation (sans GT) sur un sample.jsonl via OpenAPI")
    p.add_argument("manifest", help="sample.jsonl (résolu: pages non nulles, sinon page f1)")
    p.add_argument("--dest", default="out/annos", help="cache des annotations téléchargées")
    p.add_argument("-o", "--out", default="out/seg-scores.csv")
    p.add_argument("--report", action="store_true", help="afficher la synthèse par strate")

    p = sub.add_parser("ocr-probe",
                       help="signal OCR document-level via le 'Taux OCR' du manifest openapi (sans Datadome)")
    p.add_argument("inventory", help="inventaire .csv ou .jsonl (colonnes ark/doctype/period)")
    p.add_argument("-o", "--out", default="out/ocr-probe.csv")
    p.add_argument("--report", action="store_true", help="afficher la synthèse par strate")

    p = sub.add_parser("annotation-sheet",
                       help="générer la feuille d'annotation HTML depuis un sample.jsonl")
    p.add_argument("manifest", help="sample.jsonl (ou resolved.jsonl)")
    p.add_argument("--title", default="Phase 0 — feuille d'annotation")
    p.add_argument("-o", "--out", default="feuille-annotation.html")

    p = sub.add_parser("stats")
    p.add_argument("triplets")

    args = ap.parse_args(argv)
    client = GallicaClient(cache_dir=args.cache, delay_s=args.delay)

    if args.cmd == "inventory":
        from . import inventory
        rows = inventory.build_inventory(
            client, per_stratum=args.per_stratum, seed=args.seed,
        )
        inventory.write_inventory(rows, args.out)
        print(f"{len(rows)} documents -> {args.out}")
        print("Relire le CSV (titres/dates) et élaguer avant l'échantillonnage.")

    elif args.cmd == "inventory-from-dump":
        from . import inventory, inventory_dump
        rows = inventory_dump.build_from_dump(
            args.dump, doctype=args.doctype,
            periods=inventory_dump.parse_periods(args.periods),
            per_stratum=args.per_stratum,
            col_ark=args.col_ark, col_date=args.col_date,
            col_title=args.col_title, lang=args.lang or None,
            require_ocr=not args.no_require_ocr,
            delimiter=args.delimiter, seed=args.seed,
        )
        if args.append and Path(args.out).exists():
            import csv as _csv
            with open(args.out, newline="", encoding="utf-8") as f:
                existing = list(_csv.DictReader(f))
            rows = existing + rows
        inventory.write_inventory(rows, args.out)
        print(f"{len(rows)} documents -> {args.out}")

    elif args.cmd == "sample":
        rows = sampling.load_inventory(args.inventory, args.strata.split(","))
        alloc = sampling.allocate(rows, args.n, floor=args.floor)
        print("Allocation:", json.dumps(alloc, indent=2, ensure_ascii=False))
        smp = sampling.draw_sample(
            rows, alloc,
            client=None if args.offline else client,
            pages_per_doc=args.pages_per_doc, seed=args.seed,
        )
        sampling.write_manifest(smp, args.out)
        print(f"{len(smp)} pages -> {args.out}")

    elif args.cmd == "harvest":
        from .iiif3 import crop_url_from_service, parse_manifest
        dest = Path(args.dest)
        n_ok = n_err = 0
        with open(args.manifest, encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                ark, page = row["ark"], row["page"]
                try:
                    if page is None:
                        # Resolve a page on the fly via the v3 manifest.
                        import random as _random
                        count = client.page_count_v3(ark) if args.source == "openapi" \
                            else client.page_count(ark)
                        page = _random.Random(f"{ark}").randint(1, count)
                    if args.source == "openapi":
                        out_path = client.fetch_annotations(
                            ark, page, dest / f"{ark}_f{page}.annotations.json")
                        if args.images:
                            man = parse_manifest(client.fetch_manifest(ark))
                            svc = next(
                                (c["image_service"] for c in man["canvases"]
                                 if c["index"] == page and c["image_service"]),
                                None,
                            )
                            if svc:
                                client.download_image(
                                    f"{svc.rstrip('/')}/full/full/0/default.jpg",
                                    dest / f"{ark}_f{page}.jpg",
                                )
                    else:
                        out_path = client.fetch_alto(
                            ark, page, dest / f"{ark}_f{page}.alto.xml")
                        if args.images:
                            client.download_image(
                                client.iiif_page_url(ark, page),
                                dest / f"{ark}_f{page}.jpg",
                            )
                    n_ok += 1
                    print(f"  ok {ark} f{page} -> {out_path.name}")
                except Exception as e:  # noqa: BLE001
                    n_err += 1
                    print(f"  ! {ark} f{page}: {e}")
        print(f"done: {n_ok} ok, {n_err} errors")

    elif args.cmd == "gt-list":
        for s in gt_sources.REGISTRY.values():
            tag = "GALLICA" if s.gallica_sourced else "       "
            print(f"{s.key:26s} [{s.kind:11s}] {tag}  {s.description}")

    elif args.cmd == "gt-fetch":
        keys = (
            [k for k, s in gt_sources.REGISTRY.items() if s.kind == "git"]
            if args.key == "all-git" else [args.key]
        )
        for k in keys:
            print(f"fetching {k} ...")
            gt_sources.fetch(k, args.dest)

    elif args.cmd == "gt-arks":
        entries = gt_sources.recover_arks(args.dataset_dir)
        found = sum(1 for e in entries if e["ark"])
        out = args.out or (Path(args.dataset_dir).name + ".arks.jsonl")
        with open(out, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        print(f"{found}/{len(entries)} XML files with an ARK -> {out}")
        print("Verify page_hints against the Pagination service before trusting them.")

    elif args.cmd == "triplets":
        gt_root = Path(args.gt_root)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        n_trip = n_pages = 0
        with open(args.mapping, encoding="utf-8") as f, \
             open(out, "w", encoding="utf-8") as w:
            for line in f:
                m = json.loads(line)
                if not m.get("ark") or not m.get("page_hint"):
                    continue
                gt_page = parse_any(gt_root / m["file"])
                try:
                    if args.source == "openapi":
                        from .iiif3 import group_tokens_into_lines, parse_annotation_page
                        prod_path = client.fetch_annotations(m["ark"], m["page_hint"])
                        prod_page = parse_annotation_page(
                            prod_path.read_bytes(), source_path=str(prod_path))
                        # OpenAPI serves word-level tokens; rebuild lines.
                        prod_page = group_tokens_into_lines(prod_page)
                    else:
                        prod_path = client.fetch_alto(m["ark"], m["page_hint"])
                        prod_page = parse_any(prod_path)
                except Exception as e:  # noqa: BLE001
                    print(f"  ! {m['ark']} f{m['page_hint']}: {e}")
                    continue
                scale = None
                if gt_page.width and prod_page.width and gt_page.width != prod_page.width:
                    scale = prod_page.width / gt_page.width
                res = align_pages(
                    gt_page.lines, prod_page.lines,
                    min_iou=args.min_iou, min_sim=args.min_sim, scale=scale,
                )
                n_pages += 1
                for match in res.matches:
                    ops = edit_script(match.prod.text, match.gt.text)
                    rec = {
                        "ark": m["ark"],
                        "page": m["page_hint"],
                        "gt_file": m["file"],
                        "gt_line_id": match.gt.id,
                        "prod_line_id": match.prod.id,
                        "bbox": match.prod.bbox,
                        "crop_url": (
                            client.iiif_crop_url(m["ark"], m["page_hint"], match.prod.bbox)
                            if match.prod.bbox else None
                        ),
                        "ocr": match.prod.text,
                        "gt": match.gt.text,
                        "cer": round(cer(match.prod.text, match.gt.text), 4),
                        "iou": round(match.iou, 3),
                        "sim": round(match.sim, 3),
                        "noop": match.prod.text == match.gt.text,
                        "ops": [dataclasses.asdict(o) for o in ops],
                    }
                    w.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n_trip += 1
                for g in res.unmatched_gt:
                    w.write(json.dumps({
                        "ark": m["ark"], "page": m["page_hint"], "gt_file": m["file"],
                        "gt_line_id": g.id, "gt": g.text, "unmatched": "gt",
                    }, ensure_ascii=False) + "\n")
                for sus, prods in res.split_merge_suspects:
                    w.write(json.dumps({
                        "ark": m["ark"], "page": m["page_hint"], "gt_file": m["file"],
                        "gt_line_id": sus.id, "suspect": "split_merge",
                        "prod_line_ids": [p.id for p in prods],
                    }, ensure_ascii=False) + "\n")
        print(f"{n_pages} pages aligned -> {n_trip} triplets in {out}")

    elif args.cmd == "group-preview":
        from .iiif3 import group_tokens_into_lines, parse_annotation_page
        page = parse_annotation_page(
            Path(args.annotations_json).read_bytes(),
            source_path=args.annotations_json)
        n_tokens = len(page.lines)
        grouped = group_tokens_into_lines(
            page, y_overlap_ratio=args.y_overlap, x_gap_factor=args.x_gap)
        print(f"{n_tokens} tokens -> {len(grouped.lines)} lignes reconstruites\n")
        for l in grouped.lines:
            bb = l.bbox
            coord = f"y={bb[1]:>5} x={bb[0]:>5}" if bb else "no-geo"
            print(f"  [{coord}] {l.text}")

    elif args.cmd == "seg-score":
        from .segcomplexity import score_annotation_file, PageComplexity
        import csv as _csv, dataclasses as _dc, random as _random
        from collections import defaultdict as _dd
        dest = Path(args.dest)
        rows = [json.loads(l) for l in open(args.manifest, encoding="utf-8")]
        seen_keys, deduped = set(), []
        for r in rows:
            key = (r["ark"], r.get("page"))
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(r)
        rows = deduped
        results = []
        for r in rows:
            ark, page = r["ark"], r.get("page")
            try:
                if page is None:
                    try:
                        count = client.page_count_v3(ark)
                        page = _random.Random(ark).randint(1, count)
                    except Exception:
                        page = 1
                anno_path = client.fetch_annotations(ark, page, dest / f"{ark}_f{page}.annotations.json")
                pc = score_annotation_file(
                    anno_path, ark=ark, page=page,
                    doctype=r.get("doctype",""), period=r.get("period",""))
                results.append(pc)
                if not pc.has_ocr:
                    flag = " [SANS OCR]"
                elif pc.empty:
                    flag = " [trop peu de tokens]"
                else:
                    flag = ""
                print(f"  {ark} f{page}: complexité={pc.complexity:.3f} "
                      f"cols={pc.n_columns} désordre={pc.flow_disorder:.2f} "
                      f"tokens={pc.n_tokens}{flag}")
            except Exception as e:  # noqa: BLE001
                print(f"  ! {ark} f{page}: {e}")
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=[f.name for f in _dc.fields(PageComplexity)])
            w.writeheader()
            for pc in results:
                w.writerow(_dc.asdict(pc))
        print(f"\n{len(results)} pages scorées -> {args.out}")
        if args.report:
            by = _dd(list)
            for pc in results:
                by[(pc.doctype, pc.period)].append(pc)
            # --- OCR coverage table ---
            print("\n=== Couverture OCR de production par strate ===")
            print(f"{'strate':<28} {'pages':>6} {'avec OCR':>9} {'%':>6}")
            for (dt, per), pcs in sorted(by.items()):
                n = len(pcs)
                with_ocr = sum(1 for p in pcs if p.has_ocr)
                print(f"{dt+'|'+per:<28} {n:>6} {with_ocr:>9} {100*with_ocr/n:>5.0f}%")
            # --- complexity, on OCR-bearing pages only ---
            scored = [pc for pc in results if pc.has_ocr and not pc.empty]
            print(f"\n=== Complexité de segmentation "
                  f"({len(scored)} pages avec OCR exploitable) ===")
            if scored:
                by2 = _dd(list)
                for pc in scored:
                    by2[(pc.doctype, pc.period)].append(pc)
                print(f"{'strate':<28} {'n':>3} {'complexité':>11} {'désordre':>9} "
                      f"{'saut_col':>9} {'cols':>5} {'gap':>6}")
                for (dt, per), pcs in sorted(by2.items()):
                    n = len(pcs)
                    cx = sum(p.complexity for p in pcs)/n
                    di = sum(p.flow_disorder for p in pcs)/n
                    cj = sum(p.column_jump_rate for p in pcs)/n
                    co = sum(p.n_columns for p in pcs)/n
                    ga = sum(p.coverage_gap for p in pcs)/n
                    print(f"{dt+'|'+per:<28} {n:>3} {cx:>11.3f} {di:>9.3f} "
                          f"{cj:>9.3f} {co:>5.1f} {ga:>6.3f}")
            print("\nComplexité élevée = segmentation probablement dominante.")
            print("À CALIBRER: annoter ~25 pages à la main, vérifier la corrélation avec 'complexity'.")

    elif args.cmd == "ocr-probe":
        import csv as _csv
        from collections import defaultdict as _dd
        # Accept both the CSV inventory and the JSONL sample as input.
        path = args.inventory
        if path.endswith(".jsonl"):
            docs = [json.loads(l) for l in open(path, encoding="utf-8")]
        else:
            with open(path, newline="", encoding="utf-8") as f:
                docs = list(_csv.DictReader(f))
        seen, uniq = set(), []
        for d in docs:
            if d["ark"] not in seen:
                seen.add(d["ark"])
                uniq.append(d)
        rows = []
        for d in uniq:
            ark = d["ark"]
            doctype = d.get("doctype", "")
            period = d.get("period", "")
            try:
                rate = client.ocr_rate(ark)
                rows.append({"ark": ark, "doctype": doctype, "period": period,
                             "has_ocr": rate is not None,
                             "ocr_rate": "" if rate is None else round(rate, 4)})
                tag = f"{rate*100:.1f}%" if rate is not None else "— (sans OCR)"
                print(f"  {ark:<18} {doctype}|{period:<10} Taux OCR = {tag}")
            except Exception as e:  # noqa: BLE001
                print(f"  ! {ark}: {e}")
                rows.append({"ark": ark, "doctype": doctype, "period": period,
                             "has_ocr": "", "ocr_rate": ""})
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=["ark", "doctype", "period",
                                               "has_ocr", "ocr_rate"])
            w.writeheader()
            w.writerows(rows)
        print(f"\n{len(rows)} documents sondés -> {args.out}")
        if args.report:
            by = _dd(list)
            for r in rows:
                by[(r["doctype"], r["period"])].append(r)
            n_err = sum(1 for r in rows if r["has_ocr"] == "")
            print("\n=== Disponibilité OCR par strate (manifest openapi) ===")
            print(f"{'strate':<28} {'sondés':>6} {'avec OCR':>9} {'%':>6} {'taux OCR moy.':>14}")
            for (dt, per), rs in sorted(by.items()):
                resolved = [r for r in rs if r["has_ocr"] != ""]
                n = len(resolved)  # % over resolved docs only; errors excluded
                with_ocr = [r for r in resolved if r["has_ocr"] is True]
                rates = [r["ocr_rate"] for r in with_ocr if r["ocr_rate"] != ""]
                mean = f"{100*sum(rates)/len(rates):.1f}%" if rates else "—"
                pct = f"{100*len(with_ocr)/n:>4.0f}%" if n else "   —"
                print(f"{dt+'|'+per:<28} {n:>6} {len(with_ocr):>9} "
                      f"{pct:>6} {mean:>14}")
            if n_err:
                print(f"\n⚠ {n_err} documents non résolus (erreur réseau) — "
                      f"exclus des %, relancer pour compléter (cache = reprise).")
            print("\nLe 'Taux OCR' est document-level (une page précise peut être creuse).")
            print("Filtrer has_ocr=True donne l'inventaire ciblé 'AVEC OCR' (étape 3).")

    elif args.cmd == "annotation-sheet":
        from .sheet import generate_sheet
        rows = [json.loads(l) for l in open(args.manifest, encoding="utf-8")]
        out = generate_sheet(rows, args.out, title=args.title)
        print(f"{len(rows)} pages -> {out}")

    elif args.cmd == "stats":
        n = noop = unmatched = suspects = 0
        cers = []
        strata = Counter()
        with open(args.triplets, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r.get("unmatched"):
                    unmatched += 1
                    continue
                if r.get("suspect"):
                    suspects += 1
                    continue
                n += 1
                cers.append(r["cer"])
                noop += r.get("noop", False)
                strata[r.get("ark", "?")] += 1
        if n:
            cers.sort()
            print(f"triplets            : {n}")
            print(f"  noop (already OK) : {noop} ({100*noop/n:.1f}%)")
            print(f"  CER mean/median   : {sum(cers)/n:.4f} / {cers[n//2]:.4f}")
        print(f"unmatched GT lines  : {unmatched}  (candidate missed zones)")
        print(f"split/merge suspects: {suspects}  (candidate segmentation errors)")
        print(f"documents           : {len(strata)}")


if __name__ == "__main__":
    sys.exit(main())
