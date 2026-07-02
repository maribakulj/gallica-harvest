# Protocole — Phase 0 : diagnostic segmentation vs reconnaissance

**Objet.** Estimer, sur un échantillon de pages Gallica, la part de l'erreur
OCR résiduelle imputable à la *segmentation* (mise en page, lignes, colonnes,
ordre de lecture) par opposition à la *reconnaissance* (caractères). Ce chiffre
conditionne l'architecture du correcteur (niveau ligne vs niveau bloc) et
ouvre la note de cadrage à la direction.

**Livrable.** Une note de 3–4 pages : méthodologie, décomposition globale
pondérée en caractères affectés, ventilation par strate, 10–15 exemples
commentés, décision phase 4 (oui/non).

---

## 1. Règle d'imputation (contrefactuelle)

> Une erreur est imputable à la **segmentation** si un reconnaisseur
> **parfait**, recevant le crop tel que défini par la zone/ligne détectée en
> production, **ne pourrait pas** produire le texte correct dans le bon ordre.
> Sinon elle est imputable à la **reconnaissance**.

Conséquences pratiques :

- Une ligne **fusionnée** ou **contaminée par la colonne voisine** produit des
  erreurs en cascade → tout compte en *segmentation* (le reconnaisseur parfait
  ne peut pas savoir où couper ce qu'on lui a mal donné).
- Un « m » lu « rn » sur une ligne bien détourée → *reconnaissance*.
- Une **zone non détectée** (texte absent de l'OCR) → *segmentation*
  (sous-type : zone manquée).
- L'**ordre de lecture** faux avec lignes individuellement correctes →
  *segmentation* (sous-type : ordre).
- Une **césure** mal recollée mais lignes correctes → *reconnaissance*
  (intra-ligne) si la coupe est sur la ligne ; *segmentation* si elle résulte
  d'un mauvais chaînage de lignes.
- En cas de doute persistant après application de la règle : **indécidable**
  (classe à compter, pas à cacher).

## 2. Grille de catégories

**Segmentation** — fusion de lignes ; scission de ligne ; contamination
inter-colonnes ; ordre de lecture erroné ; zone manquée (texte non extrait) ;
mauvais typage de bloc (légende, pub, tableau, marginalia pris pour du corps —
ou l'inverse).

**Reconnaissance** — substitutions de caractères ; diacritiques ;
ligatures / s long ; casse ; ponctuation ; segmentation en mots *intra*-ligne
(espaces insérés/manquants).

**Indécidable.**

Deux niveaux d'annotation :

1. **Niveau page** (toutes les pages de l'échantillon) : présence/absence de
   chaque catégorie + gravité globale (RAS / mineure / notable / page
   inutilisable). Rapide, en balayage image ↔ texte OCR : 3–4 min/page.
2. **Niveau ligne** (sous-échantillon ~800–1 000 lignes sur les mêmes pages,
   phase finale seulement) : classification fine, pondérée en caractères
   affectés. IC ±3 pts sur la proportion — largement suffisant pour trancher
   « 5 % » vs « 30 % ».

**Pondération.** Le chiffre final se pondère en **caractères affectés**, pas
en occurrences : une fusion de lignes détruit ~80 caractères, un accent en
touche un.

## 3. Échantillon

- **Inventaire** : `inventaire-pilote.csv` — 188 documents Gallica réels
  (90 fascicules de presse : La Presse, Le Matin, Le Gaulois, L'Œuvre,
  La Fronde, presse féminine 18e–20e ; 67 imprimés 15e–18e ; 31 tapuscrits
  20e), extraits des corpus publics NewsEye/altomator, Gallicorpora, OCR17+,
  FoNDUE, HTR-United.
- **Strates** : doctype × période (1450-1600, 1600-1820, 1821-1880,
  1881-1945).
- **Tirage** : allocation proportionnelle avec plancher, seedé
  (`harvest.cli sample`), reproductible.

**Biais assumés, à déclarer dans la note.** (a) L'inventaire provient de
corpus de vérité terrain existants : il sur-représente ce que des projets ont
choisi de transcrire — acceptable pour le **pilote**, à remplacer pour
l'échantillon final par un tirage catalogue interne ou le dump api.bnf.fr.
(b) Tant que les pages ne sont pas résolues par le moissonneur, la feuille
pointe sur **f1** : pour la presse, la une est le cas *difficile*
(multi-colonnes) — le pilote est donc un stress-test, pas une estimation non
biaisée. L'échantillon final tire les pages aléatoirement dans chaque
document.

## 4. Procédure

**Pilote (cette semaine).**
1. Annoter ~20 pages avec la feuille HTML (image + texte OCR côte à côte).
2. Laisser reposer 3 jours ; réannoter 10 pages ; mesurer l'auto-accord par
   catégorie. Toute catégorie < 80 % d'accord → fusionner ou préciser la
   règle. Consigner les cas litigieux et la décision prise.
3. Figer la grille v1.

**Audit (semaines 2–3).** 300–400 pages au niveau page, 25–30 pages/jour,
export CSV quotidien (bouton de la feuille), sauvegarde du CSV dans le dépôt.

**Analyse.** Décomposition pondérée globale ; ventilation par strate
(attendu : presse multi-colonnes ≫ monographies) ; planche d'exemples ;
recommandation phase 4.

## 5. Critère de décision

- Part segmentation **< ~10 %** des caractères affectés → le correcteur
  ligne-à-ligne (phases 1–3) suffit ; la phase 4 est du sur-engineering.
- **> ~25 %** → la phase 4 (correction bloc + coordonnées) est justifiée
  empiriquement ; le chiffre ouvre le papier.
- Entre les deux → décision par strate (phase 4 restreinte à la presse).

## 6. Traçabilité

Conserver : le CSV d'inventaire, le `sample.jsonl` (seed inclus), les exports
CSV d'annotation datés, et ce protocole versionné. L'auto-accord du pilote
figure dans la section méthode de la note et du futur papier.
