# gallica-harvest

Outillage de moissonnage et d'alignement pour le diagnostic de segmentation vs reconnaissance et la constitution des triplets
(crop image, hypothèse OCR de production, vérité terrain) pour la
post-correction OCR.

Zéro dépendance hors bibliothèque standard (pytest pour les tests).
Python ≥ 3.10.

## Installation

```bash
pip install pytest            # tests uniquement
python3 -m pytest tests/ -q   # 35 tests, tout doit passer
```

## Workflow phase 0 — diagnostic

1. **Inventaire.** Trois options, par ordre de robustesse hors réseau BnF :
   - **Dumps officiels (recommandé hors BnF)** — télécharger les jeux
     « métadonnées de la collection numérique » sur api.bnf.fr (CSV
     ';'-séparés, Licence ouverte), puis :

```bash
python3 -m harvest.cli inventory-from-dump dump-presse.csv \
    --doctype presse --per-stratum 80 -o inventaire.csv
python3 -m harvest.cli inventory-from-dump dump-monographies.csv \
    --doctype monographie --per-stratum 80 -o inventaire.csv --append
```

     Filtre langue (`--lang fre`) et OCR disponible par défaut ; colonnes
     ajustables (`--col-ark`, `--col-date`, `--delimiter`). Tirage seedé.
   - **SRU** (`inventory`) — fonctionne depuis le réseau BnF ; bloqué par la
     protection anti-bot (Datadome) depuis l'extérieur.
   - **Manuelle** — exporter un CSV `ark,doctype,period[,...]` depuis une vue
     catalogue interne.
   Dans tous les cas, **relire et élaguer le CSV** avant échantillonnage.

2. **Échantillon stratifié** :

```bash
python3 -m harvest.cli sample inventaire.csv \
    --strata doctype,period --n 200 --floor 8 --pages-per-doc 3 \
    --source openapi -o out/sample.jsonl
```

   Deux façons de résoudre les numéros de page, toutes deux hors réseau BnF :
   `--source openapi` (défaut) résout les comptes de pages via le manifeste
   IIIF v3 et permet **plusieurs pages par document** ; `--offline` diffère la
   résolution au moissonnage (une page/doc, page=None). Éviter `--source
   legacy` (Pagination gallica.bnf.fr, bloqué par Datadome hors BnF).

3. **Moissonnage** — par défaut via **openapi.bnf.fr** (IIIF Presentation
   v3, non soumis à la protection anti-bot) : texte OCR en AnnotationPages
   `supplementing`, images via les services Image du manifeste :

```bash
python3 -m harvest.cli harvest out/sample.jsonl --dest out/pages --images
```

   Depuis le réseau BnF, `--source legacy` bascule sur RequestDigitalElement
   (ALTO natif, avec confiances WC — absentes des annotations v3).

## Workflow phases 1+ — triplets d'entraînement

```bash
python -m harvest.cli gt-list                      # registre des sources
python -m harvest.cli gt-fetch newseye-fr --dest gt
python -m harvest.cli gt-fetch all-git --dest gt   # tous les dépôts git
python -m harvest.cli gt-arks gt/newseye-fr -o newseye.arks.jsonl
# vérifier/filtrer newseye.arks.jsonl à la main (page_hint notamment) puis :
python -m harvest.cli triplets newseye.arks.jsonl --gt-root gt/newseye-fr \
    -o out/triplets.jsonl
python -m harvest.cli stats out/triplets.jsonl
```

Le JSONL de sortie contient, par ligne appariée : l'URL de crop IIIF,
l'hypothèse de production, la GT, le CER, le script d'édition
(equal/replace/insert/delete) et le drapeau `noop` (pour contrôler le ratio
d'exemples déjà corrects à l'entraînement). Les lignes GT non appariées
(zones manquées) et les suspects de fusion/scission sont émis dans le même
fichier avec les champs `unmatched` / `suspect` — ce sont les entrées du
diagnostic de segmentation.



## Estimation SANS vérité terrain : score de complexité de segmentation

Les erreurs de reconnaissance exigent une vérité terrain pour être détectées ;
les erreurs de **segmentation**, non — elles laissent des signatures
géométriques intrinsèques dans les annotations mot-à-mot d'OpenAPI. Le module
`segcomplexity` les calcule par page, sur 100 % des pages, sans aucun modèle :

- `flow_disorder` : taux de transitions du flux violant l'ordre de lecture
  (haut-bas, gauche-droite, une colonne à la fois) — le symptôme de la page
  des rats (y qui saute 999→279).
- `n_columns` : nombre de colonnes détectées (clustering densité-conscient
  des centres x, robuste à l'espacement inter-mots normal).
- `column_jump_rate` : taux de sauts inter-colonnes dans le flux (contamination).
- `wide_box_rate` : boîtes anormalement larges (lignes fusionnées / multi-colonnes).
- `height_dispersion` : dispersion robuste des hauteurs (titres, mise en page mixte).
- `coverage_gap` : fraction de la zone texte non couverte (zones ratées).
- `complexity` : somme pondérée normalisée dans [0,1] (poids déclarés dans le
  module, **à recalibrer**).

```bash
python3 -m harvest.cli seg-score sample-principal.jsonl --report
```

Produit un CSV par page + une synthèse par strate. Complexité élevée =
segmentation probablement dominante dans l'erreur.

**Calibration (indispensable pour la fiabilité).** Le score brut ne dit pas
s'il sur- ou sous-estime. Annoter ~25 pages à la main, vérifier la corrélation
entre `complexity` et le jugement humain, et n'extrapoler aux centaines de pages
qu'une fois cette corrélation établie. La corrélation elle-même est un résultat
à publier. Boucle outillée :

```bash
# 1. feuille d'annotation sur un sous-ensemble étalé sur la plage de complexité
python3 -m harvest.cli annotation-sheet sample-calibration.jsonl \
    -o feuille-calibration.html
# 2. annoter dans le navigateur, cliquer « Exporter CSV » -> phase0-annotations.csv
# 3. corréler le jugement humain au score (Spearman, stdlib)
python3 -m harvest.cli calibrate phase0-annotations.csv out/seg-scores.csv
#    --signal seg_count (défaut, nb de catégories segmentation) | gravite
```

**Limite honnête.** Ceci mesure la *segmentation* sans GT. Le partage exact
segmentation/reconnaissance *en caractères* sur la part reconnaissance exige
de la vérité terrain (phase 1, corpus alignés). Aucun miracle possible là-dessus.

## Signal de disponibilité/qualité OCR (document-level)

Le manifest IIIF openapi expose un champ **« Taux OCR »** par document (le
`nqa_score` officiel de l'OAIRecord étant, lui, bloqué par Datadome). Sa
présence vaut flag « OCR disponible », sa valeur est le taux OCR BnF.

```bash
python3 -m harvest.cli --delay 2 ocr-probe inventaire-pilote.csv --report
```

Accepte un `.csv` (inventaire) ou un `.jsonl` (sample). Produit
`out/ocr-probe.csv` (`ark,doctype,period,has_ocr,ocr_rate`) + une synthèse de
disponibilité OCR par strate. Filtrer `has_ocr=True` donne un inventaire ciblé
« AVEC OCR », pour que le scoring ne soit pas dominé par des pages vides.

⚠️ openapi rate-limite les rafales (HTTP 429) ; le client réessaie avec backoff,
mais lancer les gros lots avec `--delay 2`. ⚠️ Le taux est *document-level* :
une page précise peut être creuse même si le doc affiche 90 %.

## Note importante : granularité des annotations OpenAPI

L'endpoint `supplementing` d'openapi.bnf.fr sert le texte OCR **au mot**
(une annotation = un token, avec sa boîte `xywh`), pas à la ligne. Le
toolkit reconstruit les lignes géométriquement via
`iiif3.group_tokens_into_lines` (clustering par bande verticale + découpe
sur les gouttières inter-colonnes), sans se fier à l'ordre des items —
condition nécessaire pour pouvoir *diagnostiquer* les erreurs d'ordre de
lecture. La commande `triplets --source openapi` applique ce regroupement
automatiquement avant l'alignement.

Pour inspecter la reconstruction sur une page réelle avant de lancer en
masse :

```bash
python3 -m harvest.cli group-preview out/pages/ARK_f7.annotations.json
# ajuster --y-overlap (défaut 0.5) et --x-gap (défaut 2.5) si besoin
```

Depuis le réseau BnF, `--source legacy` (ALTO natif) fournit directement des
lignes *et* les confiances WC par mot, absentes des annotations v3 — à
préférer pour l'entraînement final si l'accès intra-muros est disponible.

## Points de vigilance (à lire avant la première vraie exécution)

- **Endpoints.** Les URLs Gallica dans `harvest/gallica.py` (Pagination,
  RequestDigitalElement, IIIF, OAIRecord) sont les endpoints publics tels
  que je les connais ; **non testés en live depuis cet environnement**
  (réseau restreint). Vérifier sur 2–3 ARKs et ajuster
  `GallicaClient(endpoints={...})` si besoin — notamment si vous ciblez
  l'IIIF v3 en interne.
- **User-Agent.** Renseigner un contact réel dans `USER_AGENT`/config avant
  toute campagne, et rester ≥ 1 s de délai sur les endpoints publics.
- **page_hint.** La récupération de numéros de page depuis les noms de
  fichiers GT est heuristique. Toujours contrôler un échantillon à la main :
  la numérotation des vues Gallica ne coïncide pas toujours avec la
  foliotation des fichiers GT.
- **Cadres de coordonnées.** L'aligneur rescale automatiquement quand les
  largeurs d'image déclarées diffèrent, mais certains jeux GT recadrent ou
  redressent les images : dans ce cas l'IoU s'effondre et le repli textuel
  prend le relais (champ `iou` à 0 dans les triplets — à surveiller dans
  `stats`).
- **Conventions de transcription.** La normalisation dans `align.normalise`
  ne sert qu'à l'appariement, jamais au script d'édition. L'harmonisation
  des conventions (s long, ligatures) entre jeux GT reste une décision de
  groupe d'invariance à déclarer explicitement avant fusion des corpus.
- **Licences.** Vérifier les conditions de réutilisation hors compétition
  des données OCRepair, et l'inscription PRImA pour ENP.

## Structure

```
harvest/parsing.py     ALTO v2/v3/v4 + PAGE XML -> modèle Line commun
harvest/gallica.py     client Gallica (cache disque, rate limit, IIIF crops)
harvest/align.py       appariement géométrie+texte, suspects fusion/scission,
                       scripts d'édition, CER
harvest/gt_sources.py  registre des sources GT + récupération d'ARKs
harvest/sampling.py    échantillonnage stratifié reproductible
harvest/cli.py         orchestration
tests/                 fixtures ALTO/PAGE synthétiques + 35 tests
```
