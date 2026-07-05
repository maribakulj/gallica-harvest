export const meta = {
  name: 'annotate-calibration',
  description: 'Annotate Gallica OCR pages by vision (image vs OCR) for phase-0 segmentation calibration — blind counterfactual imputation, one Sonnet agent per page',
  phases: [{ title: 'Annotate', detail: 'one Sonnet agent per page writes a verdict JSON' }],
}

let a = args
if (typeof a === "string") { try { a = JSON.parse(a) } catch (e) { a = {} } }
const base = (a && a.base) || ""
const items = Array.isArray(a)
  ? a
  : ((a && a.pages) || []).map(([ark, page]) => ({
      ark, page,
      img: `${base}/${ark}_f${page}.jpg`,
      ocr: `${base}/${ark}_f${page}.ocr.txt`,
    }))
log(`Annotation vision de ${items.length} pages (Sonnet, à l'aveugle)`)

const RULE = [
  "RÈGLE D'IMPUTATION (contrefactuelle), stricte : une erreur est de SEGMENTATION si un reconnaisseur PARFAIT,",
  "recevant le crop tel que défini par la zone/ligne détectée, ne pourrait PAS produire le texte correct dans le bon ordre.",
  "Sinon elle est de RECONNAISSANCE.",
  "- lignes fusionnées, contamination inter-colonnes, ordre de lecture faux, zone de texte visible sur l'image mais ABSENTE de l'OCR,",
  "  bloc mal typé (légende/pub/tableau/marginalia pris pour du corps ou l'inverse) => SEGMENTATION.",
  "- substitutions de caractères, accents, ligatures/s long, casse, ponctuation, espaces intra-ligne => RECONNAISSANCE.",
  "Si la page a plusieurs colonnes : vérifie dans le flux OCR si chaque colonne est lue haut->bas avant la suivante,",
  "ou si le texte saute entre colonnes. Le multicolonne N'EST PAS en soi une erreur : ne coche 'colonnes'/'ordre'",
  "que si le flux OCR est RÉELLEMENT désordonné. Pour 'zone_manquee' : repère les blocs de texte de l'image et",
  "vérifie s'ils apparaissent dans le flux OCR (un titre purement décoratif non-textuel ne compte pas).",
  "Ne coche une catégorie QUE si tu en as la preuve visuelle.",
].join("\n")

function prompt(it) {
  const verdict = it.img.replace(/\.jpg$/, ".verdict.json")
  return [
    "Tu es annotateur pour un diagnostic OCR (phase 0, Gallica). Tu juges UNE page en comparant son image à l'OCR de production.",
    "MÉTHODE IMPÉRATIVE : utilise UNIQUEMENT l'outil Read (pour l'image puis pour le texte), puis l'outil Write. N'utilise JAMAIS Bash ni aucune commande shell. Fais UNE SEULE passe d'analyse : lis l'image une fois, lis l'OCR une fois, juge, écris. Ne relis pas les fichiers, ne boucle pas.",
    `Lis avec Read l'image : ${it.img}`,
    `Lis avec Read le flux OCR : ${it.ocr}  (ce peut être une seule très longue ligne — c'est NORMAL, lis-la telle quelle ; elle peut finir par […TRONQUÉ], ignore ce marqueur).`,
    "",
    RULE,
    "",
    `Puis écris avec l'outil Write le fichier ${verdict} contenant EXACTEMENT cet objet JSON (0 ou 1 pour les cases) :`,
    `{"ark":"${it.ark}","page":${it.page},"n_cols_image":<entier colonnes visibles>,"seg_fusion":0,"seg_scission":0,"seg_colonnes":0,"seg_ordre":0,"seg_zone_manquee":0,"seg_typage_bloc":0,"rec_categories":[],"gravite":"ras|mineure|notable|severe","seg_share_pct":<0-100 estimation part caractères affectés due à la segmentation>,"evidence":"1 phrase citant l'image vs OCR"}`,
    `Renvoie comme message final UNIQUEMENT : "${it.ark} f${it.page}: ok".`,
  ].join("\n")
}

const results = await parallel(items.map((it) => () =>
  agent(prompt(it), { label: `${it.ark} f${it.page}`, phase: "Annotate", model: "sonnet" })
))

const ok = results.filter(Boolean).length
log(`terminé : ${ok}/${items.length} agents ont répondu`)
return { requested: items.length, ok }
