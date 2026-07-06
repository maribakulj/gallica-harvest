# CONTEXTE — projet gallica-harvest (phase 0 : diagnostic OCR)

Ce fichier résume l'état d'avancement, les décisions, les pièges connus et les
prochaines étapes, pour reprise par Claude Code ou par une session future.
Dernière mise à jour : 3 juillet 2026.

---

## But du projet

Estimer, sur Gallica, **quelle part de l'erreur OCR résiduelle vient de la
segmentation** (mise en page : colonnes, ordre de lecture, lignes fusionnées)
par opposition à la **reconnaissance** de caractères. Ce chiffre conditionne
l'architecture d'un futur correcteur OCR par VLM (niveau ligne vs niveau bloc)
et sert de socle à une note de cadrage à la direction de la BnF, dans le cadre
du projet ArGiMi.

Contexte plus large : le projet vise à terme un correcteur OCR multimodal
(VLM) pour Gallica, avec une **tête d'édition typée** (keep/substitute/insert/
delete) dont l'espace de sortie coïncide avec une métrique d'évaluation à loi
de conservation. La phase 0 (ce toolkit) est le préalable : mesurer le
problème avant de construire la solution.

---

## Ce qui est fait

Un toolkit Python (`harvest/`, zéro dépendance hors stdlib, 35 tests) qui :

1. **Constitue un inventaire d'ARKs** — deux voies :
   - `inventory-from-dump` : depuis les dumps CSV de métadonnées api.bnf.fr.
   - `inventory` : via SRU (bloqué hors réseau BnF, voir Datadome ci-dessous).
   - Un inventaire pilote de **188 ARKs Gallica réels** est déjà constitué
     (`inventaire-pilote.csv`), extrait des corpus GT publics (Gallicorpora,
     OCR17+, FoNDUE, HTR-United, NewsEye/altomator). 90 fascicules de presse,
     67 imprimés 15e–18e, 31 tapuscrits 20e.

2. **Échantillonne** (`sample`) de façon stratifiée (doctype × période),
   reproductible (seed). Fichiers `sample-pilote.jsonl` (24) et
   `sample-principal.jsonl` (149) déjà générés.

3. **Moissonne** (`harvest`) le texte OCR + images via l'API IIIF de la BnF
   (IIIF Presentation v3).

4. **Score la complexité de segmentation SANS vérité terrain**
   (`seg-score`) — le cœur de l'approche. Voir section dédiée.

5. **Génère une feuille d'annotation HTML** (`annotation-sheet`) pour l'audit
   manuel (liens Gallica image/texte, cases à cocher, export CSV, sauvegarde
   navigateur).

6. Prépare la **phase 1** (triplets d'entraînement image/OCR/GT via alignement
   ALTO, tête d'édition) — modules `align.py`, `gt_sources.py`, mais c'est
   hors périmètre phase 0.

Documents : `PROTOCOLE-PHASE0.md` (règle contrefactuelle, grille, procédure),
`README.md` (usage complet).

---

## Décisions et faits établis (ne pas re-découvrir)

### Accès réseau : Datadome bloque gallica.bnf.fr (CORRIGÉ le 3 juil. 2026)
- `gallica.bnf.fr` (SRU, RequestDigitalElement, **OAIRecord**) est protégé par
  **Datadome**. Il renvoie 403 à tout client non-navigateur : curl, urllib,
  le sandbox d'exécution de Claude, ET les serveurs MCP tiers (alien.club ET
  le MCP Gallica claude.ai — tous deux 403 sur OAIRecord).
- ⚠️ **RECTIFICATION** : contrairement à ce qui était écrit ici, un User-Agent
  de navigateur **ne suffit PAS** — un `urllib` depuis cette machine avec l'UA
  Chrome complet reçoit quand même **403** sur `gallica.bnf.fr/services/
  OAIRecord` (testé 3 juil. 2026, sandbox activé ET désactivé). Datadome
  exige un cookie de session issu d'un vrai navigateur, hors de portée d'urllib.
  Donc **OAIRecord (et donc `nqa_score`) n'est joignable par AUCUNE route
  programmatique testée.**
- Le patch `USER_AGENT` dans `harvest/gallica.py` reste utile pour l'API IIIF
  (poli), mais n'ouvre pas Datadome. ⚠️ Le RE-APPLIQUER si code réextrait.
- **Conséquence** : on travaille exclusivement via l'**API IIIF de la BnF**, qui
  n'est PAS derrière Datadome (voir section suivante).

### API à utiliser : l'API IIIF de la BnF, pas les endpoints legacy
- L'**API IIIF Presentation v3** de la BnF N'EST PAS derrière Datadome et
  fonctionne depuis cette machine avec le bon User-Agent. L'hôte concret est
  configurable via `GALLICA_IIIF_BASE` (voir `harvest/gallica.py`).
- OCR par page : endpoint `annotationpage/supplementing.json`.
- ⚠️ **L'OCR est servi AU MOT** (une annotation W3C = un token, avec sa boîte
  `#xywh`), pas à la ligne. Le module `iiif3.group_tokens_into_lines`
  reconstruit les lignes géométriquement (nécessaire pour la phase 1 ; la
  phase 0 travaille directement sur les tokens).
- ⚠️ Image API v3 : la taille `full` est SUPPRIMÉE, utiliser `max`
  (`.../max/0/default.jpg`). Un serveur v3 strict renvoie 400 sur `full`.
- ⚠️ **Rate-limit** : l'API IIIF renvoie **HTTP 429** sur des rafales (~1 req/s
  sur 188 docs → 134 échecs). Le client a désormais un **retry/backoff**
  (respecte `Retry-After`, `max_retries=4`) et il faut lancer les gros lots
  avec `--delay 2`. Corrigé le 3 juil. 2026.

### Découverte de phase 0 (résultat en soi) : couverture OCR très inégale
- Beaucoup de documents anciens (imprimés 16e–17e) et de manuscrits/tapuscrits
  (`btv1b...`) **n'ont PAS d'OCR de production** : l'AnnotationPage revient
  vide (`items` absent). Ce n'est pas un bug — l'OCR classique échoue sur ces
  documents, la BnF ne le publie pas.
- Le champ `has_ocr` (dans `PageComplexity`) distingue "sans OCR" de "OCR
  présent mais peu de tokens". Le rapport `seg-score --report` affiche un
  **tableau de couverture OCR par strate**.
- **Implication de périmètre** : la post-correction ne concerne QUE les
  documents ayant un OCR. Les imprimés anciens relèvent de la production HTR
  (le domaine de Gallicorpora), pas de la correction. L'échantillon pilote est
  donc dominé par des pages sans OCR ; un inventaire ciblé sur des documents
  AVEC OCR est nécessaire pour un vrai signal (voir Prochaines étapes).

### Signal qualité OCR document-level : « Taux OCR » du manifest IIIF (FAIT)
- `nqa_score` (OAIRecord) existe mais est **injoignable** (Datadome, voir
  ci-dessus — ni urllib, ni MCP). Route abandonnée.
- **Trouvé mieux (3 juil. 2026)** : le manifest IIIF de la BnF expose un champ de
  métadonnée **« Taux OCR » / « OCR rate »** (ex. `"92.79 %"`) au niveau
  document. Servi par l'API IIIF → **pas de Datadome, joignable sur les
  188 docs**. C'est l'équivalent atteignable du `nqa_score`.
- Câblé dans le toolkit : `iiif3.manifest_ocr_rate()` (fraction [0,1] ou None),
  `parse_manifest()['ocr_rate']`, `GallicaClient.ocr_rate(ark)`, et une
  commande **`ocr-probe <inventaire> --report`** qui sonde toute une liste et
  sort un CSV `ark,doctype,period,has_ocr,ocr_rate` + synthèse par strate.
- **Fait discriminant** : le champ est ABSENT ⇔ pas d'OCR de production. La
  présence/absence concorde à 100 % avec le `has_ocr` par-page de seg-score
  (validation croisée sur les 24 pages du pilote). Le manifest est plus léger
  (1 requête/doc, pas d'annotations mot-à-mot).
- ⚠️ **Doc-level ≠ page-level** : un doc à 92 % peut avoir une page échantillon
  quasi vide (ex. `bpt6k4701189w` : 92.8 % doc / 12 tokens sur f8), et un doc à
  9.1 % (`bpt6k5216400`) a quand même des milliers de tokens. Utiliser le taux
  comme filtre d'inventaire, pas comme vérité page.

### Résultat ocr-probe sur les 188 docs (3 juil. 2026) → `inventaire-avec-ocr.csv`
- **97 docs sur 188 ont de l'OCR** (`out/ocr-probe-188.csv`). Disponibilité par
  strate (% sur docs résolus) :
  - presse 1881-1945 : 100 % (taux moy. 90 %) · presse 1821-1880 : 100 % (72 %)
  - presse 1600-1820 : 74 % (87 %) · imprimé 1600-1820 : 19 % (88 %)
  - imprimé 1450-1600 : 17 % (40 %) · tapuscrit : 0 %
- Surprise vs pilote (24) : des **imprimés anciens ONT de l'OCR** (17–19 %),
  invisibles sur le petit échantillon. 1 doc en erreur persistante :
  `bpt6k6127905` (manifest introuvable, écarté).
- `inventaire-avec-ocr.csv` = les 97 docs AVEC OCR, enrichis d'`ocr_rate`, triés
  par taux décroissant → base pour l'échantillonnage/scoring à l'échelle.

### Scoring à l'échelle (étape 4) — 199 pages, 3 juil. 2026
- **Sampler réparé pour l'API IIIF** : `draw_sample` prend un `page_count_fn` et la
  commande `sample` une option `--source iiif|legacy` (défaut iiif). Avant,
  le mode online n'utilisait que la Pagination legacy (gallica.bnf.fr, Datadome →
  mort d'ici) ; désormais les nombres de pages viennent du manifest v3. C'est ce
  qui permet **plusieurs pages par doc** hors réseau BnF.
- Échantillon : `sample-avec-ocr.jsonl` (199 pages, 5 strates, seed défaut,
  `--n 200 --floor 8 --pages-per-doc 3` sur `inventaire-avec-ocr.csv`).
- `seg-score` : **190/199 pages exploitables (95 %)** — vs 9/24 sur le pilote.
  Quelques pages restent vides malgré un doc "avec OCR" (planches/pages blanches
  tirées au hasard) : le doc-level ≠ page-level, encore.
- Complexité moyenne par strate (`out/seg-scores-avec-ocr.csv`) :
  - imprimé 1450-1600 : **0.200** (cols 2.6, gap 0.43) — les plus complexes
  - presse 1600-1820 : 0.150 (cols 2.8) · presse 1881-1945 : 0.150 (cols 1.3)
  - imprimé 1600-1820 : 0.145 (cols 1.6) · presse 1821-1880 : 0.140 (cols 1.4)
  - Colonnes réalistes (1.3–2.8), presse multi-colonnes, imprimés anciens en tête.
- ⚠️ Ces chiffres restent **NON calibrés** (poids déclarés, pas validés). C'est
  l'étape suivante : annoter ~25 pages (feuille HTML) et corréler.

---

## Le scoreur de complexité de segmentation (cœur méthodologique)

Principe : les erreurs de *reconnaissance* exigent une vérité terrain pour être
détectées ; les erreurs de *segmentation* laissent des **signatures
géométriques intrinsèques** dans les tokens mot-à-mot, calculables sans jamais
connaître le texte correct. Donc on peut estimer la part segmentation sur 100 %
des pages, automatiquement. (Le partage exact en *caractères* sur la part
reconnaissance, lui, exigera de la GT — phase 1. Pas de miracle là-dessus.)

Métriques par page (`harvest/segcomplexity.py`) :
- `flow_disorder` : taux de transitions du flux violant l'ordre de lecture.
- `n_columns` : colonnes détectées par **profil de projection** (robuste aux
  tailles de tokens mixtes — la version par espacement inter-tokens explosait
  à 228 colonnes sur du réel, c'est CORRIGÉ).
- `column_jump_rate`, `wide_box_rate`, `height_dispersion`, `coverage_gap`.
- `complexity` : somme pondérée normalisée [0,1] (poids DÉCLARÉS dans le
  module, **à recalibrer**).

⚠️ **Calibration indispensable** : le score brut ne dit pas s'il sur/sous-
estime. Il faut annoter ~25 pages à la main (la feuille HTML), vérifier la
corrélation entre `complexity` et le jugement humain, et n'extrapoler qu'ensuite
(avec IC). La corrélation elle-même est un résultat publiable.

---

## Bugs corrigés récemment (ne pas réintroduire)
1. Détection de colonnes qui explosait (228 colonnes) → réécrite en profil de
   projection. Signature : `detect_columns(tokens, page_width)` prend
   désormais les tokens (extents), pas les centres.
2. Double-itération du `seg-score` → venait du sampler offline qui émettait
   deux lignes `page:null` identiques par doc. Corrigé : une ligne par doc en
   mode offline, page résolue au momissonnage.
3. `full` → `max` dans les URLs d'image IIIF v3.
4. HTTP 429 sur l'API IIIF en rafale → retry/backoff dans `GallicaClient._get`
   (respecte `Retry-After`). Lancer les gros lots avec `--delay 2`.

---

## Prochaines étapes (ordre suggéré)

1. ✅ **FAIT** — `seg-score` tourne proprement sur le pilote, colonnes
   réalistes (1–2), couverture OCR affichée.
2. ✅ **FAIT (autrement)** — `nqa_score` injoignable (Datadome) ; remplacé par
   le « Taux OCR » du manifest IIIF via la commande `ocr-probe`. Signal
   qualité gratuit sur les 188 docs, validé croisé avec `has_ocr`.
3. ✅ **FAIT** — `inventaire-avec-ocr.csv` produit (97 docs AVEC OCR sur 188,
   avec leur `ocr_rate`). Pour élargir : viser presse et monographies récentes
   (19e–20e), et si besoin dépasser les 188 (l'échantillon reste petit par
   strate côté imprimés).
4. ✅ **FAIT** — scoring à l'échelle : `sample-avec-ocr.jsonl` (199 pages),
   `seg-score` → `out/seg-scores-avec-ocr.csv`, synthèse par strate ci-dessus.
   190/199 pages exploitables.
5. 🟢 **PILOTE VISION FAIT (5 juil. 2026), à confirmer à l'échelle** — calibration :
   - **Déblocage réseau** : l'hôte de l'API IIIF de la BnF ajouté à l'allowlist
     egress de l'environnement (Custom domains). Conséquence majeure : le bac à
     sable atteint désormais l'API IIIF en direct (images IIIF + OCR + manifest),
     et Claude peut **lire les pixels d'une page** (`curl` image → outil Read).
     L'annotation « pleine vision » ne dépend donc plus d'un humain ni du MCP.
     `gallica.bnf.fr` reste bloqué (Datadome) — sans importance, tout passe par
     l'API IIIF. ⚠️ Garder cet hôte dans l'allowlist (policy relue au démarrage).
   - **Pilote 13 pages** (`sample-calib-pilot.jsonl`, 5 strates) : chaque page
     annotée à l'aveugle par un sous-agent vision (image+OCR, règle
     contrefactuelle) → `phase0-annotations-pilot.csv` ; corrélé au score
     géométrique (`out/seg-calib-pilot.csv`) via `calibrate`.
   - **Résultats** : Spearman rho = **+0.06** (seg_count) / **+0.45** (gravité),
     n=13 → illustratif, PAS significatif. Le score géométrique traque mal la
     segmentation (et `n_columns` est faux : page 7 colonnes notée cols=1).
   - **Surprise (bouscule l'intuition « presse multicolonne = segmentation »)** :
     les pages les plus colonnées (7, 6, 3, 2 col.) sont jugées PROPRES sur
     l'ordre de lecture — l'OCR gère bien les colonnes. L'erreur résiduelle y
     est de la **reconnaissance** (petit corps, italiques, accents). Quand la
     segmentation apparaît (6/13 pages), c'est un AUTRE mode : **zones manquées**
     (titres décoratifs, blocs pub) et micro-désordres, sur presse ET
     monographies. Le plus gros cas seg (60 %) est sur une **monographie**
     mono-colonne (note de bas de page à 2 col. entremêlée). Part segmentation
     moyenne déclarée ~14 %. → une éventuelle brique phase 4 servirait la
     **complétude des zones**, pas le démêlage de colonnes.
   - **À faire** : monter à ~50 pages (rho crédible) ; spot-check humain de 5-6
     pages (accord humain↔vision) ; réparer `n_columns` ou remplacer le signal
     segmentation par le jugement vision directement.
   - Ancien plan (annotation humaine via feuille HTML), toujours valable :
   - Sous-ensemble `sample-calibration.jsonl` : **25 pages étalées sur toute la
     plage de complexité** (0.103→0.306, échantillonnage systématique sur la
     liste triée), toutes strates, **mélangées (seed 20260703)** pour annotation
     à l'aveugle.
   - Feuille `feuille-calibration.html` générée → à annoter dans le navigateur
     (cocher les catégories segmentation/reconnaissance, gravité), puis
     « Exporter CSV ».
   - Nouvelle commande **`calibrate <annotations.csv> <seg-scores.csv>`** :
     joint sur (ark, page), calcule **Spearman rho** entre le signal humain
     (`seg_count` = nb de catégories segmentation cochées, ou `--signal gravite`)
     et `complexity`. Stdlib pure (module `harvest/calibrate.py`).
   - **Action MarceL** : annoter les 25 pages, exporter le CSV, puis lancer
     `python3 -m harvest.cli calibrate phase0-annotations.csv
     out/seg-scores-avec-ocr.csv`. Un rho positif/élevé légitime l'extrapolation.
6. **Rédiger la note** de phase 0 (3–4 p.) : couverture OCR par strate,
   décomposition segmentation/reconnaissance, décision sur la nécessité d'une
   brique "segmentation" (phase 4) dans le correcteur.

---

## Piège d'environnement (macOS)
- Ne PAS travailler dans `~/Downloads` : le TCC de macOS bloque l'accès du
  terminal (erreurs "Operation not permitted"). Le projet est dans
  `~/gallica-harvest`.
- Un ancien zip contenait un dossier parasite `{harvest,tests` (accolade non
  développée par un shell) ; il a été nettoyé. S'il réapparaît, l'ignorer/
  supprimer.
