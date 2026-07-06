"""Download page images + assemble OCR streams for the calibration worklist.
Reuses annotations already cached by seg-score (out/anno-full). Images via the
BnF IIIF Image API. Polite: small delay + 429 retry. Idempotent (skips files
already present).
"""
import json, csv, os, sys, time, subprocess
from harvest.gallica import GallicaClient
from harvest.iiif3 import parse_manifest

SCR = sys.argv[1] if len(sys.argv) > 1 else "/tmp/full"
ANNO = "out/anno-full"
os.makedirs(SCR, exist_ok=True)
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

rows = [r for r in csv.DictReader(open("out/seg-scores-full.csv"))
        if r["has_ocr"] == "True" and r["empty"] == "False"]

# meta per (ark,page) from the sample jsonl
meta = {}
for l in open("sample-avec-ocr.jsonl"):
    d = json.loads(l)
    meta[(d["ark"], str(d.get("page")))] = d

c = GallicaClient()
svc_cache = {}
def services(ark):
    if ark not in svc_cache:
        man = parse_manifest(c.fetch_manifest(ark))
        svc_cache[ark] = {str(cv["index"]): cv["image_service"] for cv in man["canvases"]}
    return svc_cache[ark]

def dl(url, path):
    for size in (url, url.replace("/full/1100,/", "/max/")):
        r = subprocess.run(["curl", "-sS", "-A", UA, "--max-time", "60",
                            "-o", path, "-w", "%{http_code}", size],
                           capture_output=True, text=True)
        if (r.stdout or "")[-3:] == "200" and os.path.exists(path) and os.path.getsize(path) > 2000:
            return True
        time.sleep(1.5)
    return False

work = []
for r in rows:
    ark, page = r["ark"], str(r["page"])
    base = f"{SCR}/{ark}_f{page}"
    # OCR stream from cached annotations
    if not os.path.exists(base + ".ocr.txt"):
        toks = []
        try:
            j = json.load(open(f"{ANNO}/{ark}_f{page}.annotations.json"))
            for it in (j.get("items") or []):
                b = it.get("body") or {}
                v = b.get("value") if isinstance(b, dict) else None
                if v:
                    toks.append(v)
        except Exception:
            pass
        open(base + ".ocr.txt", "w").write(" ".join(toks))
    # image
    if not (os.path.exists(base + ".jpg") and os.path.getsize(base + ".jpg") > 2000):
        try:
            svc = services(ark).get(page)
        except Exception as e:
            print("  ! manifest", ark, e); svc = None
        if svc and dl(f"{svc}/full/1100,/0/default.jpg", base + ".jpg"):
            time.sleep(0.8)
        else:
            print("  ! image fail", ark, page); continue
    m = meta.get((ark, page), {})
    work.append({"ark": ark, "page": int(page),
                 "doctype": m.get("doctype", ""), "period": m.get("period", ""),
                 "complexity": float(r["complexity"]), "n_columns": int(r["n_columns"]),
                 "flow_disorder": float(r["flow_disorder"]), "n_tokens": int(r["n_tokens"]),
                 "img": base + ".jpg", "ocr": base + ".ocr.txt"})

json.dump(work, open(f"{SCR}/annotate-worklist.json", "w"), ensure_ascii=False)
print(f"worklist: {len(work)} pages prêtes -> {SCR}/annotate-worklist.json")
