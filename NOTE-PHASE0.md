# Note de cadrage — Phase 0 : diagnostic segmentation vs reconnaissance

**Projet** gallica-harvest / ArGiMi — préalable à un correcteur OCR multimodal pour Gallica.
**Date** 5 juillet 2026 · **Échantillon** 145 pages annotées · **Statut** diagnostic complet, note de décision.

---

## 1. Objet et question

Estimer, sur Gallica, **quelle part de l'erreur OCR résiduelle relève de la segmentation**
(mise en page : colonnes, ordre de lecture, lignes fusionnées, blocs non extraits) par
opposition à la **reconnaissance** de caractères. Ce chiffre conditionne l'architecture du
futur correcteur — correction au niveau **ligne** (texte) ou au niveau **bloc** (image +
coordonnées) — et ouvre la note de cadrage à la direction.

**Règle d'imputation (contrefactuelle).** Une erreur est de *segmentation* si un reconnaisseur
**parfait**, recevant le crop tel que défini par la zone détectée en production, **ne pourrait
pas** produire le texte correct dans le bon ordre. Sinon elle est de *reconnaissance*.

---

## 2. Méthodologie

### 2.1 Corpus

- Inventaire de départ : 188 documents Gallica réels (presse 18ᵉ–20ᵉ, imprimés 15ᵉ–18ᵉ,
  tapuscrits), extraits des corpus de vérité terrain publics (Gallicorpora, OCR17+, FoNDUE,
  NewsEye, HTR-United).
- **Découverte de périmètre** : seuls **97 des 188 documents portent un OCR de production**.
  La disponibilité est très inégale par strate — presse récente 100 %, presse ancienne ~74 %,
  imprimés anciens 17–19 %, tapuscrits 0 %. *La post-correction ne concerne que les documents
  ayant un OCR* ; les imprimés anciens relèvent majoritairement de la production HTR, hors
  périmètre. Ce fait à lui seul cadre le produit.
- Échantillon annoté : **145 pages exploitables** (avec OCR, non vides), stratifiées
  doctype × période, tirage seedé reproductible.

### 2.2 Annotation par vision, à l'aveugle

Chaque page est jugée en comparant son **image** à son **flux OCR de production**, en
appliquant la règle contrefactuelle. L'annotateur ne voit **pas** le score automatique
(annotation à l'aveugle). Catégories segmentation : fusion de lignes, scission, contamination
inter-colonnes, ordre de lecture, **zone manquée** (texte visible absent de l'OCR), mauvais
typage de bloc.

**Annotateur = modèle de vision (Sonnet).** Choix validé empiriquement :

| Comparaison | Accord binaire « segmentation présente » | Gravité ±1 cran |
|---|---|---|
| Sonnet ↔ Opus (pages pilote) | **92 %** (11/12) | 10/12 |
| Haiku ↔ Opus (mêmes pages) | 77 % | — |

Haiku a été écarté : il commet **systématiquement** l'erreur « multicolonne ⇒ segmentation »
(il présume la contamination dès qu'il voit des colonnes), ce qui biaiserait le chiffre-titre.
Sonnet ne tombe pas dans ce piège et suit l'annotation Opus de référence à 92 %.

### 2.3 Calibration du score géométrique automatique

Un module (`segcomplexity`) calcule, sans vérité terrain, un score de « complexité de
segmentation » à partir de la seule géométrie des tokens (colonnes, désordre de flux…).
On mesure sa corrélation de rang (Spearman) avec le jugement humain-vision.

---

## 3. Résultats

### 3.1 Le score géométrique automatique n'est pas un proxy valide

| Signal humain | Spearman ρ (vs complexité géo.) | n |
|---|---|---|
| Nombre de catégories segmentation | **+0.11** (faible) | 145 |
| Gravité globale | **+0.21** (faible) | 145 |

Pire : la strate la plus « complexe » selon le score (imprimé 1450-1600, 0.200) porte la
**plus faible** part de segmentation réelle (3 %). **Conclusion : le score géométrique ne
traque pas la segmentation ; on ne peut pas l'utiliser pour extrapoler.** C'est le jugement
vision qui fait foi. (Le détecteur de colonnes a aussi un défaut connu : une page à 7 colonnes
est comptée `cols=1`.)

### 3.2 Ventilation par strate (le chiffre)

| Strate | n | Part segmentation | % pages avec segmentation |
|---|---|---|---|
| imprimé 1450-1600 | 15 | 3 % | 13 % |
| imprimé 1600-1820 | 19 | 1 % | 42 % |
| presse 1600-1820 | 23 | 4 % | 26 % |
| presse 1821-1880 | 32 | 10 % | 22 % |
| presse 1881-1945 | 56 | 11 % | 61 % |
| **Global** | **145** | **~7 %** | 39 % |

### 3.3 La segmentation se concentre sur la presse — mais pas là où on l'attendait

| | n | Part segmentation | % pages seg. |
|---|---|---|---|
| Imprimés | 34 | **2 %** | 29 % |
| Presse | 111 | **9 %** | 42 % |

La presse concentre bien la segmentation (9 % vs 2 %). **Mais ce n'est PAS la contamination
inter-colonnes.** Répartition des erreurs de segmentation :

| Catégorie | Occurrences |
|---|---|
| **zone manquée** | **30** |
| ordre de lecture | 22 |
| fusion de lignes | 12 |
| contamination colonnes | 10 |
| typage de bloc | 8 |
| scission | 1 |

**L'OCR de production lit correctement les colonnes.** Le problème de la presse est qu'il
**oublie des blocs entiers** — encarts publicitaires, bandeaux-titres décoratifs, légendes de
photos — surtout sur la presse moderne (11 %, pages denses et illustrées). Le mode d'erreur
dominant n'est donc pas géométrique (démêler des colonnes) mais **de complétude** (rattraper le
texte que l'OCR n'a jamais extrait).

---

## 4. Conséquence architecturale

### 4.1 Les zones manquées sont incorrigibles par un correcteur texte

Un post-correcteur qui édite des tokens OCR existants **ne peut rien** pour un bloc absent de
l'OCR : il n'y a aucune entrée à corriger. **Les ~7 % de segmentation (dominés par les zones
manquées) sont un plancher incorrigible pour tout modèle qui ne voit que le texte.** Les
atteindre exige de **voir l'image** — c'est-à-dire une capacité de (ré)OCR, pas seulement de
correction.

### 4.2 Un modèle unifié « copier-ou-générer », pas deux modes

La direction est un **correcteur multimodal (VLM)** conditionné sur : l'**image**, les tokens
**ALTO** et leurs **confiances de mot (WC)**, et les **coordonnées** des boîtes. Plutôt qu'une
tête d'édition typée (keep/substitute/insert/delete) — qui suppose une hypothèse alignée et ne
peut donc rien émettre là où l'OCR est absent, forçant une architecture à deux modes — on
recommande une **tête unique copier-ou-générer** (*pointer-generator*) : à chaque pas, le
modèle **pointe** vers un token ALTO (copier) ou **génère**. Cette décision unique subsume :

- *copier* → le « keep » (lignes propres, majorité des cas) ;
- *générer* → substitution/insertion/suppression **et** transcription d'un bloc absent.

La distinction « éditer vs compléter » disparaît en une seule décision next-token. Rôle des
signaux :

- **WC** : biaise vers *copier* sur un token à haute confiance, vers *corriger* sur un token à
  basse confiance. (À noter : la WC est **aveugle aux zones manquées** — pas de mot, pas de
  confiance — elle ne suffit donc pas à elle seule.)
- **Coordonnées ALTO** : donnent gratuitement la **carte de couverture**. « Image porteuse de
  texte » moins « zones couvertes par une boîte » = **candidats zones manquées** → le signal
  qui déclenche la génération. La détection redevient du conditionnement, pas un second réseau.

### 4.3 Fidélité (l'enjeu pour une bibliothèque)

La tête d'édition typée offrait une garantie *dure* (sortie = edit borné de l'entrée). Une tête
copier-ou-générer n'offre qu'un biais *souple* et *peut* générer faux. On rachète la sûreté sans
re-scinder en modes : biais de copie par défaut, conditionnement WC, **loi de conservation
gardée comme objectif/métrique** (non comme vocabulaire rigide), et **garde-fou par confiance**
(confiance propre basse → on retombe sur l'OCR d'origine plutôt que d'halluciner).

---

## 5. Preuve de faisabilité (la branche risquée est démontrée)

Test sur 4 pages de presse portant des zones manquées : un VLM voyant l'image doit **régénérer**
le bloc absent, sans halluciner.

| Page | Bloc manqué | Résultat |
|---|---|---|
| f12 | encart publicitaire « crochet & tricot » entier | verbatim exact ; `[?]` sur 1 lettre tachée |
| f6 | bandeaux « PALAIS DE LA NOUVEAUTÉ / BLANC / OCCASIONS » | exact (1 micro-doublon) |
| f17 | encart Q/R + 9 légendes chiffrées, **sur fond rouge** (OCR mort : 159 tokens) | **métrages chiffrés exacts**, 0 invention |
| f8 | titre + 16 légendes photo avec noms propres | noms de créateurs/photographes exacts ; `[?]` honnête |

**Résultat : 4/4.** Le comportement décisif pour une bibliothèque — le modèle **marque son
incertitude (`[?]`) plutôt que d'halluciner** — est observé jusque dans le cas piège (fine print
chiffré sur fond coloré). La branche « générer » du modèle unifié n'est plus une hypothèse.

La figure `figures/f17-zones-manquees.jpg` illustre la boucle complète sur un cas réel :
détecter les zones non couvertes (coordonnées) → générer leur texte (image) → sans hallucination.

---

## 6. Décision phase 4

Critère du protocole : segmentation < ~10 % → correcteur ligne suffit ; > ~25 % → brique bloc
justifiée ; entre les deux → décision par strate.

- **Global ~7 % (< 10 %)** → une brique de segmentation *générale* n'est pas justifiée
  empiriquement ; le correcteur ligne-à-ligne couvre l'essentiel de l'erreur (~93 %,
  reconnaissance).
- **Nuance décisive** : ces 7 % sont **incorrigibles sans capacité image**, et se concentrent
  sur la **presse moderne (11 %)**. Si les blocs manqués comptent (pubs, titres, légendes —
  importants pour l'indexation et la recherche plein-texte), une **brique de complétude de
  zones** est requise — non pour démêler des colonnes, mais pour **rattraper le texte largué**,
  et **restreinte à la presse**.
- Cette brique s'incarne naturellement dans le modèle unifié **copier-ou-générer** (§4), et sa
  faisabilité est démontrée (§5) — elle n'ajoute pas un second réseau, seulement un
  conditionnement (coordonnées + image).

**Recommandation.** Correcteur multimodal unifié copier-ou-générer ; la « phase 4 » n'est pas un
module séparé mais la branche *générer* du même modèle, prioritaire sur la presse.

---

## 7. Limites et biais (déclarés)

- **n = 145**, avec des strates faibles (15–19 pages sur les imprimés) → **intervalles de
  confiance larges par strate** ; le 7 % global est plus ferme que les chiffres par strate.
- **Annotateur = modèle de vision** (Sonnet), validé à 92 % contre Opus, mais **pas encore
  scellé par un accord humain** — un contrôle humain d'une planche (~6–15 pages) reste la
  garantie finale.
- **`seg_share` = estimation déclarée** par page, pas comptée rigoureusement au caractère
  (le protocole vise une pondération en caractères, non atteinte ici).
- **Imprimés anciens avec OCR : rares pour de vrai** (~25 documents dans tous les corpus GT
  réunis) — strate structurellement mince, hors périmètre de correction de toute façon.
- **Contraintes d'environnement** : diagnostic mené via l'API IIIF de la BnF (sans WC) ;
  l'ALTO natif avec WC, cible de la production, n'était pas joignable hors réseau BnF. Le
  correcteur, lui, lira l'ALTO+WC.
- Preuve de faisabilité sur **4 pages presse/mode**, annotateur amorcé à chercher des blocs
  manqués ; la détection autonome repose en production sur la carte de couverture des
  coordonnées.

---

## 8. Traçabilité

- Inventaire OCR : `inventaire-avec-ocr.csv` (97 docs) · échantillon : `sample-avec-ocr.jsonl`.
- Score géométrique : `harvest/segcomplexity.py`, sortie `out/seg-scores-full.csv`.
- Annotations vision : `phase0-annotations-full.csv` (145 pages) · calibration :
  `harvest/calibrate.py` (`python3 -m harvest.cli calibrate …`).
- Pipeline d'annotation à l'échelle : `scripts_calib_download.py`, `scripts_annotate_workflow.js`.
- Figure : `figures/f17-zones-manquees.jpg`.

## 9. Prochaines étapes

1. **Sceau humain** : faire relire une planche (~6–15 pages, dont les cas `zone_manquee`) pour
   confirmer l'accord humain ↔ vision.
2. **Élargir la presse moderne** (monographies mono-colonne en témoin) via la recherche Gallica
   côté serveur, pour resserrer les IC par strate.
3. **Prototyper la branche générer** sur un lot ALTO+WC réel avec carte de couverture, et
   mesurer le CER des blocs régénérés contre vérité terrain.
