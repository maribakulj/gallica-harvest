"""Generate the phase-0 annotation sheet: a self-contained HTML file.

For each sampled page it renders links to the Gallica viewer and to the raw
OCR text view, plus one checkbox per error category from the protocol, a
severity selector and a notes field. State persists in the browser's
localStorage (the file runs locally); an "Exporter CSV" button downloads the
current annotations. No dependency, no server.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

SEG_CATS = [
    ("seg_fusion", "Fusion de lignes"),
    ("seg_scission", "Scission de ligne"),
    ("seg_colonnes", "Contamination inter-colonnes"),
    ("seg_ordre", "Ordre de lecture erroné"),
    ("seg_zone_manquee", "Zone manquée"),
    ("seg_typage_bloc", "Mauvais typage de bloc"),
]
REC_CATS = [
    ("rec_substitution", "Substitutions de caractères"),
    ("rec_diacritiques", "Diacritiques"),
    ("rec_ligatures", "Ligatures / s long"),
    ("rec_casse", "Casse"),
    ("rec_ponctuation", "Ponctuation"),
    ("rec_mots", "Segmentation en mots (intra-ligne)"),
]
ALL_CATS = SEG_CATS + REC_CATS + [("indecidable", "Indécidable")]


def gallica_urls(ark: str, page) -> dict:
    p = page or 1
    base = f"https://gallica.bnf.fr/ark:/12148/{ark}"
    return {
        "viewer": f"{base}/f{p}.item",
        "zoom": f"{base}/f{p}.zoom",
        "texte": f"{base}/f{p}.texteBrut",
    }


def generate_sheet(sample_rows: list[dict], out_path: str | Path,
                   title: str = "Phase 0 — feuille d'annotation") -> Path:
    rows_html = []
    for i, r in enumerate(sample_rows):
        urls = gallica_urls(r["ark"], r.get("page"))
        page_label = f"f{r['page']}" if r.get("page") else "f1 (défaut)"
        cats = "".join(
            f'<label class="cat {key.split("_")[0]}">'
            f'<input type="checkbox" data-row="{i}" data-cat="{key}">{label}</label>'
            for key, label in ALL_CATS
        )
        meta = html.escape(
            f"{r.get('doctype','?')} · {r.get('period','?')} · "
            f"{r.get('title','')} {('· ' + str(r.get('date'))) if r.get('date') else ''}"
        )
        rows_html.append(f"""
<div class="page" id="row{i}">
  <div class="head">
    <span class="num">{i + 1}</span>
    <code>{html.escape(r['ark'])}</code> <b>{page_label}</b>
    <span class="meta">{meta}</span>
    <span class="links">
      <a href="{urls['viewer']}" target="_blank">image</a>
      <a href="{urls['zoom']}" target="_blank">zoom</a>
      <a href="{urls['texte']}" target="_blank">texte OCR</a>
    </span>
  </div>
  <div class="cats">{cats}</div>
  <div class="extra">
    <select data-row="{i}" data-cat="_gravite">
      <option value="">— gravité globale —</option>
      <option value="ras">RAS / négligeable</option>
      <option value="mineure">Erreurs mineures</option>
      <option value="notable">Erreurs notables</option>
      <option value="severe">Page inutilisable</option>
    </select>
    <input type="text" data-row="{i}" data-cat="_note" placeholder="notes libres"/>
    <label><input type="checkbox" data-row="{i}" data-cat="_fait"> page traitée</label>
  </div>
</div>""")

    data_json = json.dumps(
        [{"ark": r["ark"], "page": r.get("page"), "doctype": r.get("doctype"),
          "period": r.get("period"), "title": r.get("title"),
          "source": r.get("source")} for r in sample_rows],
        ensure_ascii=False,
    )
    cat_keys = json.dumps([k for k, _ in ALL_CATS] + ["_gravite", "_note", "_fait"])

    page = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
 body {{ font: 14px/1.45 -apple-system, sans-serif; margin: 2em auto; max-width: 1100px; color:#222; }}
 h1 {{ font-size: 1.3em; }}
 .toolbar {{ position: sticky; top:0; background:#fff; padding:.6em 0; border-bottom:1px solid #ddd; z-index:2; }}
 .toolbar button {{ margin-right:.6em; }}
 .page {{ border:1px solid #ddd; border-radius:8px; padding:.7em .9em; margin:.8em 0; }}
 .page.done {{ background:#f3faf3; border-color:#bcd9bc; }}
 .head {{ display:flex; gap:.7em; align-items:baseline; flex-wrap:wrap; }}
 .num {{ background:#333; color:#fff; border-radius:1em; padding:0 .55em; font-size:.85em; }}
 .meta {{ color:#777; font-size:.9em; }}
 .links a {{ margin-right:.6em; }}
 .cats {{ margin:.5em 0; display:flex; flex-wrap:wrap; gap:.25em .9em; }}
 .cat {{ font-size:.92em; white-space:nowrap; }}
 .cat.seg {{ color:#8a2b2b; }} .cat.rec {{ color:#1f4e79; }}
 .extra {{ display:flex; gap:.8em; align-items:center; }}
 .extra input[type=text] {{ flex:1; }}
 #stats {{ color:#555; font-size:.9em; margin-left:.8em; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p>Pour chaque page : ouvrir <b>image</b> et <b>texte OCR</b> côte à côte, appliquer la
règle contrefactuelle du protocole, cocher les catégories observées
(<span style="color:#8a2b2b">segmentation</span> /
<span style="color:#1f4e79">reconnaissance</span>), noter la gravité, cocher « page traitée ».
Tout est sauvegardé localement dans le navigateur.</p>
<div class="toolbar">
  <button onclick="exportCSV()">Exporter CSV</button>
  <button onclick="if(confirm('Tout effacer ?')) {{ localStorage.removeItem(KEY); location.reload(); }}">Réinitialiser</button>
  <span id="stats"></span>
</div>
{''.join(rows_html)}
<script>
const ROWS = {data_json};
const CATS = {cat_keys};
const KEY = "phase0-annotations-v1";
let state = JSON.parse(localStorage.getItem(KEY) || "{{}}");

function save() {{ localStorage.setItem(KEY, JSON.stringify(state)); refresh(); }}
function refresh() {{
  let done = 0;
  ROWS.forEach((r, i) => {{
    const s = state[i] || {{}};
    const el = document.getElementById("row"+i);
    if (s._fait) {{ el.classList.add("done"); done++; }} else el.classList.remove("done");
  }});
  document.getElementById("stats").textContent = done + " / " + ROWS.length + " pages traitées";
}}
document.querySelectorAll("input[type=checkbox],select,input[type=text]").forEach(el => {{
  const i = el.dataset.row, cat = el.dataset.cat;
  if (i === undefined) return;
  const s = state[i] || {{}};
  if (el.type === "checkbox") el.checked = !!s[cat];
  else el.value = s[cat] || "";
  const ev = el.type === "text" ? "input" : "change";
  el.addEventListener(ev, () => {{
    state[i] = state[i] || {{}};
    state[i][cat] = el.type === "checkbox" ? el.checked : el.value;
    save();
  }});
}});
function exportCSV() {{
  const cols = ["ark","page","doctype","period","title","source"].concat(CATS);
  const lines = [cols.join(";")];
  ROWS.forEach((r, i) => {{
    const s = state[i] || {{}};
    const vals = [r.ark, r.page ?? "", r.doctype ?? "", r.period ?? "",
                  (r.title||"").replaceAll(";",","), r.source ?? ""]
      .concat(CATS.map(c => {{
        const v = s[c];
        if (v === true) return "1";
        if (v === false || v === undefined) return "";
        return String(v).replaceAll(";",",");
      }}));
    lines.push(vals.join(";"));
  }});
  const blob = new Blob(["\\ufeff" + lines.join("\\n")], {{type:"text/csv;charset=utf-8"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "phase0-annotations.csv";
  a.click();
}}
refresh();
</script>
</body></html>"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out
