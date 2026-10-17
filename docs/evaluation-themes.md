# Theme classifier: hand-checked evaluation (2026-10-17)

The seven theme pages (`/theme/<slug>/`) list notices whose **title or lot names** match the rules of
[`veille/taxonomy.py`](../veille/taxonomy.py). Before any theme page was published, we measured the **precision** of
those rules (of the notices a rule puts in a theme, how many really belong there) on hand-checked samples. The gate
was **at least 90 % on a sample the rules were not tuned on**; a theme below the gate is not published
(`Theme.published = False`, enforced by `tests/test_datapages.py`).

All seven themes passed. The labels are committed in
[`tests/fixtures/theme_labels.json`](../tests/fixtures/theme_labels.json) (337 notices: id, round, title, lots, buyer,
labels), and a test re-checks the current rules against them at every build.

## Result

| Theme | Final rules on a held-out sample | Round | Final rules on all labelled notices* |
|---|---|---|---|
| Sécurité au travail, SST, incendie | **24 / 24 = 100 %** | 2 | 59 / 59 = 100 % |
| Conduite : CACES, engins, permis | **23 / 23 = 100 %** | 2 | 45 / 45 = 100 % |
| Management et RH | **33 / 35 = 94.3 %** | 3 | 65 / 68 = 95.6 % |
| Numérique, bureautique, IA | **16 / 16 = 100 %** | 2 | 44 / 44 = 100 % |
| Langues | **17 / 18 = 94.4 %** | 2 | 37 / 39 = 94.9 % |
| Santé et médico-social | **31 / 34 = 91.2 %** | 3 | 65 / 69 = 94.2 % |
| Insertion et emploi (POEC, PRF, compétences clés) | **35 / 37 = 94.6 %** | 4 | 98 / 103 = 95.1 % |

\*Includes the rounds used to tune the rules, so it is optimistic; the held-out column is the gate. Figures on the
public pages (« X des Y avis… ») quote the held-out column (`taxonomy.PRECISION`).

These are precision figures only. We did not measure recall (notices of a theme that the rules miss); the labelling
showed some misses, e.g. lots with no theme word (« Pouponnière », « Dépistage des troubles visuels »), « P.S.C.1 »
written with dots, or « Système d'information et numérique » in a regional training programme.

## Data and protocol

- **Population**: the 1,413 procedures (one record per procedure after BOAMP/TED de-duplication, cancelled ones
  excluded) published 2025-10-17 → 2026-10-17, after a 12-month backfill (`python -m veille update --since 2025-10-01`).
- **What the rules see**: title and lot names only (accent-free, `<br/>` tags removed, typographic apostrophes
  normalised). Never the free description; the buyer name only to exclude buyers that train their own staff from the
  insertion theme (regional associations of the Missions Locales, CNFPT).
- **What the labeller saw**: title, all lot names, buyer and the first 300 characters of the description.
- **Labeller**: Octave, READ FRANCE's AI assistant, reading each notice and deciding for each of the 7 themes; no
  second human labeller, so the labels carry one person's judgement. Ambiguous cases were labelled **negative**
  (the conservative choice for precision), e.g. Toulouse Métropole « Dispositif de formation sur les savoirs de base »
  (trainees unknown) or OPCO Santé « MEDICAMBO » (content not stated).
- **Sampling**: random with a fixed seed, stratified on the classifier's positives so that every theme gets enough
  predicted notices; every notice of rounds 1–3 was labelled for all 7 themes.

| Round | Seed | Notices | Rules evaluated | Drawn from |
|---|---|---|---|---|
| 1 | 20261017 | 100 (≈ 14 per theme) | first tightened draft | all predicted positives |
| 2 | 20261018 | 100 (≈ 14 per theme) | after fixing round-1 errors | positives not seen in round 1 |
| 3 | 20261019 | 100 (34 / 33 / 33) | management, santé, insertion retightened | positives not seen in rounds 1–2 |
| 4 | 20261020 | 37 | insertion retightened | **all** insertion positives not seen in rounds 1–3 |

### Labelling criteria

A notice belongs to a theme when it buys, in its object or in one of its lots, a training (or a test, certification
or insertion service bought as training) whose **subject** is the theme:

- **Sécurité** : workplace health and safety — SST and first aid, fire, electrical clearance, work at height, AIPR,
  asbestos, confined spaces, PRAP, psychosocial risks, workers' radiation protection. Not cybersecurity, not food
  safety, not private-security (guarding) qualifications.
- **Conduite** : driving and machine operation — CACES and R48x recommendations, driving authorisations, driving
  licences (B, C, CE, BE, boat), FIMO/FCO, eco-driving and road risk.
- **Management et RH** : managing people, teams and projects (management, leadership, coaching of staff,
  codevelopment, project management) and HR practice (GPEC, appraisal interviews, QVCT). Not quality or
  environmental management systems.
- **Numérique** : office software, IT, AI, cybersecurity, GIS/CAD, data, digital skills and inclusion. Not a contract
  that buys a platform or software for managing training.
- **Langues** : foreign and regional languages, French as a foreign language, sign language, language tests and
  certifications.
- **Santé et médico-social** : health care, medical and medico-social practice, social work, early childhood, care
  qualifications (aide-soignant, auxiliaire de puériculture, DEAES…), mental health first aid. The **audience** alone
  (« pour les personnels médicaux », « agents du centre hospitalier ») is not enough.
- **Insertion et emploi** : training or insertion services whose trainees are jobseekers or people in insertion
  (RSA, PLIE, POEC, regional training programmes, prisoners, EPIDE/SMA/SMV volunteers, Erasmus+ mobility of
  jobseekers). Not training for the professionals who support them, not school pupils or students.

## What each round found and changed

**Round 1** (draft rules): sécurité 32/34 (94.1 %), conduite 19/19, management 18/21 (85.7 %), numérique 24/24,
langues 17/18 (94.4 %), santé 15/18 (83.3 %), insertion 18/19 (94.7 %). Two bugs explained most errors:
- `<br/>` left in BOAMP lot names folds to the word « br », which matched the electrical-clearance code BR
  (coaching and crane-driving notices put in sécurité). Fixed: tags removed, clearance codes only after
  « habilitation ».
- `fold()` drops the typographic apostrophe, so « l’intelligence artificielle » became « lintelligence » and
  « d’emploi » never matched. Fixed: apostrophes normalised before folding.
- Other fixes: segments describing works design, consulting (AMO) or software development are ignored (an IFSI
  building's design contract, an AI tool for social-work training); the audience phrase « professionnels médicaux et
  non médicaux » no longer counts for santé; « techniques de coaching dans ses pratiques » (training counsellors)
  no longer counts for management.

**Round 2**: sécurité 24/24, conduite 23/23, numérique 16/16, langues 17/18 (94.4 %) **pass**. Management 14/17
(82.4 %: « prise de poste » of energy managers, « accompagnement au changement » of eco-behaviour, coaching
techniques for Mission Locale counsellors), santé 20/24 (83.3 %: « aides à domicile » as audience, « gaz médicaux »,
« parentalité » in basic-skills courses, MEDICAMBO) and insertion 17/20 (85 %: « insertion professionnelle » of
engineering students, training of RSA referents, CNFPT literacy courses for civil servants) fail. Rules retightened:
« prise de poste » only for managers; « accompagnement au changement » removed; `medic\w+` narrowed; audience phrases
and « gaz médicaux » excluded; students and RSA referents excluded; CNFPT excluded from insertion.

**Round 3**: management 33/35 (94.3 %) and santé 31/34 (91.2 %) **pass**. Insertion 29/34 (85.3 %) fails again: the
bare word « insertion » (a department's « Direction action sociale emploi insertion »), basic skills bought by an
employer for its own agents, EPIDE's board coaching, industry discovery for school pupils. Rules retightened:
« insertion » only in phrases (« parcours d'insertion », « insertion professionnelle », « publics en insertion »…),
basic skills excluded when the segment names agents or employees, segments about boards or pupils dropped.

**Round 4** (all 37 remaining insertion positives): 35/37 (94.6 %) **pass**. The two errors: a training programme
for the professionals of training and insertion in Mayotte, and a regional programme for agricultural employees.

**After the evaluation** (page review, 2026-10-17): one sécurité notice matched only because the buyer's name was in
its title (« … du service départemental d'incendie et de secours ») and was a driving-licence contract. That phrase is
now ignored; the change only removes matches, so it cannot lower precision.

## Volumes after tightening (365 days to 2026-10-17)

| Theme | Notices | Open on a typical day (median, last 180 days) | First-pass regex of the SEO plan (2026-10-12) |
|---|---|---|---|
| Santé et médico-social | 203 | 19 | 251 |
| Sécurité, SST, incendie | 197 | 21.5 | 187 |
| Management et RH | 161 | 16 | 199 |
| Conduite, CACES, permis | 124 | 13 | 94 |
| Numérique, bureautique, IA | 116 | 14 | 178 |
| Insertion et emploi | 103 | 10 | 216 |
| Langues | 71 | 6 | 74 |

All pass the index rule (≥ 40 notices a year and ≥ 4 open on a typical day; `stats.index_decision`). Santé,
management, numérique and insertion shrank (insertion by half) as their rules were tightened; sécurité and conduite
grew a little because the final rules read every lot name and cover more terms (AIPR, CATEC, FIMO, FCO…).

## Re-running it

```bash
python -m veille update --since 2025-10-01   # 12-month backfill (network)
python -m pytest -q tests/test_datapages.py   # re-checks the rules against the 337 labels
```

When a rule changes, label a fresh random sample of the theme's positives (not the committed ones) before trusting
the new precision, and update `taxonomy.PRECISION`.
