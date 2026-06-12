# BMS Pipeline — 5-Year Longitudinal History (2021 Q1 – 2026 Q2)

Reconstructed from **22 quarterly Wayback captures** of bms.com's pipeline page → standard Wayback
replay (zero live-domain contamination) → Opus 4.8 vision extraction → chronological replay (§3a)
with asset identity resolution (269 raw names → 181 deterministic → 150 after
rename-merge via `curated_aliases.json`) + phase normalization.

## ⚠️ Headline finding: 'disappeared from page' ≠ 'discontinued'

A drug leaving the pipeline page is **three-way ambiguous** — it may have been **approved & graduated**
off the page (BMS's page shows only investigational assets), **discontinued**, or **renamed**. The page
alone cannot disambiguate. Known BMS approvals (SOTYKTU '22, CAMZYOS '22, AUGTYRO '23, COBENFY '24) do
**not** appear as advances to *Approved* — they simply vanish, because approved indications leave the page.
**Implication for the feed:** additions and in-pipeline phase advances are trustworthy from this source;
exits require an external approval/press signal to classify (generalizes plan §4). Below, exits are
split by exit phase as the best available proxy.

## Trustworthy signals

- **New assets entering pipeline:** 73
- **In-pipeline phase advances:** 38
- **Partner changes:** 31

## Ambiguous signal — assets that LEFT the pipeline (by exit phase)

- **Reached Filed → left (almost certainly APPROVED & graduated):** 0
- **Left from Phase 3 (approval OR failure — needs external signal):** 21
- **Left from Phase 1/2 (usually discontinued/deprioritized):** 78

## Noise reduction (why the identity + normalization layers matter)

- Naive churn (raw names, no identity/phase-norm): **656** add/remove events
- After identity resolution + rename-merge + phase-norm: **260** events
- **~60% of naive churn was name-variant / phase-wording noise.**

> The rename-merge map is hand-curated (`curated_aliases.json`) as a stand-in for the LLM clustering
> step (`merge_assets.py`), which was blocked tonight by an API usage limit. Residual rename noise remains.

## Data-quality guard (bad-capture)

2 of 22 captures were quarantined (anomalously low
extraction vs trailing median) and carried forward — no false events emitted:
- **2022Q2**: 13 assets vs trailing median 80
- **2025Q3**: 10 assets vs trailing median 49

## Pipeline size over time
| Quarter | raw assets | canonical (merged) |
|---|---|---|
| 2021Q1 | 85 | 76 |
| 2021Q2 | 86 | 77 |
| 2021Q3 | 89 | 80 |
| 2021Q4 | 93 | 84 |
| 2022Q1 | 88 | 79 |
| 2022Q2 | 15 | 13 *(quarantined)* |
| 2022Q3 | 78 | 72 |
| 2022Q4 | 76 | 69 |
| 2023Q1 | 72 | 65 |
| 2023Q2 | 67 | 63 |
| 2023Q3 | 65 | 61 |
| 2023Q4 | 61 | 57 |
| 2024Q1 | 62 | 57 |
| 2024Q2 | 65 | 60 |
| 2024Q3 | 57 | 54 |
| 2024Q4 | 54 | 50 |
| 2025Q1 | 51 | 48 |
| 2025Q2 | 50 | 48 |
| 2025Q3 | 10 | 10 *(quarantined)* |
| 2025Q4 | 48 | 47 |
| 2026Q1 | 50 | 49 |
| 2026Q2 | 51 | 50 |

> The 2021 chart-format captures over-segment combination/variant rows vs BMS's stated '50+ compounds';
> the 2021→2025 decline is partly real streamlining, partly extraction-format normalization.

## In-pipeline phase advances (trustworthy)

- 2021Q3: **BET Inhibitor (BMS-986158)** Phase 1 → Phase 2
- 2021Q3: **Anti-Fucosyl GM1** Phase 1 → Phase 2
- 2021Q3: **Anti-TIGIT** Phase 1 → Phase 2
- 2021Q3: **golcadomide** Phase 1 → Phase 2
- 2021Q3: **cendakimab** Phase 2 → Phase 3
- 2021Q4: **FA-Relaxin** Phase 1 → Phase 2
- 2021Q4: **SARS-CoV-2 mAb Duo** Phase 1 → Phase 2
- 2022Q1: **BMS-986465 (TYK2 Inhibitor)** Phase 1 → Phase 2
- 2022Q1: **S1PR1 Modulator** Phase 1 → Phase 2
- 2022Q1: **MK2 Inhibitor** Phase 1 → Phase 2
- 2022Q3: **afimetoran** Phase 1 → Phase 2
- 2022Q4: **Anti-IL-8** Phase 1 → Phase 2
- 2022Q4: **iberdomide** Phase 2 → Phase 3
- 2022Q4: **CD19 NEX T** Phase 1 → Phase 2
- 2023Q1: **golcadomide** Phase 2 → Phase 3
- 2023Q1: **MYK-224** Phase 1 → Phase 2
- 2023Q2: **BCMA ADC** None → Phase 1
- 2023Q2: **milvexian** Phase 2 → Phase 3
- 2023Q3: **BCMA NKE** Phase 1 → Phase 2
- 2023Q3: **BREYANZI (liso-cel)** Phase 2 → Phase 3
- 2023Q3: **BMS-986465 (TYK2 Inhibitor)** Phase 1 → Phase 2
- 2023Q4: **ABECMA (ide-cel)** Phase 2 → Phase 3
- 2023Q4: **admilparant** Phase 2 → Phase 3
- 2024Q1: **Anti-NKG2A** Phase 1 → Phase 2
- 2024Q2: **alnuctamab** Phase 1 → Phase 3
- 2024Q3: **arlo-cel** Phase 1 → Phase 2
- 2024Q3: **Anti-Tau (Prothena)** Phase 1 → Phase 2
- 2024Q4: **Anti-Fucosyl GM1** Phase 1 → Phase 2
- 2025Q1: **arlo-cel** Phase 2 → Phase 3
- 2025Q1: **admilparant** Phase 2 → Phase 3
- 2025Q2: **AR-LDD** Phase 1 → Phase 3
- 2025Q2: **FAAH/MGLL Dual Inhibitor** Phase 1 → Phase 2
- 2025Q4: **iza-bren** Phase 1 → Phase 2
- 2025Q4: **navlimetostat** Phase 1 → Phase 2
- 2025Q4: **CD19 NEX T** Phase 1 → Phase 2
- 2026Q1: **BREYANZI (liso-cel)** Phase 2 → Filed / Registration
- 2026Q1: **SOTYKTU (deucravacitinib)** Phase 3 → Filed / Registration
- 2026Q2: **OPDIVO® (nivolumab)** Phase 3 → Filed / Registration

## New assets entering the pipeline (trustworthy)

- 2021Q2: **CD33 NKE** @ Phase 1 (1 indication(s))
- 2021Q2: **CD47xCD20** @ Phase 1 (1 indication(s))
- 2021Q2: **IL2-CD25** @ Phase 1 (1 indication(s))
- 2021Q2: **Anti-CD40** @ Phase 1 (1 indication(s))
- 2021Q2: **FA-Relaxin** @ Phase 2 (1 indication(s))
- 2021Q2: **SARS-CoV-2 mAb Duo** @ Phase 1 (1 indication(s))
- 2021Q3: **Anti-CCR8** @ Phase 1 (1 indication(s))
- 2021Q3: **CK1α Degrader** @ Phase 1 (1 indication(s))
- 2021Q3: **ROMK Inhibitor** @ Phase 1 (1 indication(s))
- 2021Q4: **subcutaneous nivolumab + rHuPH20** @ Phase 3 (2 indication(s))
- 2021Q4: **TIGIT Bispecific** @ Phase 1 (1 indication(s))
- 2021Q4: **farletuzumab ecteribulin** @ Phase 2 (1 indication(s))
- 2021Q4: **BCMA NKE** @ Phase 1 (1 indication(s))
- 2021Q4: **ROR1 CAR T** @ Phase 1 (1 indication(s))
- 2021Q4: **Anti-Tau (Prothena)** @ Phase 1 (1 indication(s))
- 2022Q1: **eIF2B Activator** @ Phase 1 (1 indication(s))
- 2022Q3: **Opdualag (nivolumab + relatlimab)** @ Phase 3 (4 indication(s))
- 2022Q3: **MAGE A4/8 TCER** @ Phase 1 (1 indication(s))
- 2022Q3: **Anti-ILT4** @ Phase 1 (1 indication(s))
- 2022Q3: **alnuctamab** @ Phase 1 (1 indication(s))
- 2022Q4: **DGK Inhibitor** @ Phase 1 (1 indication(s))
- 2022Q4: **Anti-RIPK1 Inhibitor** @ Phase 1 (1 indication(s))
- 2023Q1: **AUGTYRO (repotrectinib)** @ Phase 2 (2 indication(s))
- 2023Q1: **SHP2 Inhibitor** @ Phase 1 (1 indication(s))
- 2023Q1: **RIPK1 Inhibitor** @ Phase 1 (1 indication(s))
- 2023Q2: **Claudin 18.2 ADC** @ Phase 1 (1 indication(s))
- 2023Q2: **PKCθ Inhibitor** @ Phase 1 (1 indication(s))
- 2023Q3: **subcutaneous relatlimab + nivolumab + rHuPH20** @ Phase 3 (1 indication(s))
- 2023Q3: **NME 1** @ Phase 1 (1 indication(s))
- 2023Q3: **NME 2** @ Phase 1 (1 indication(s))
- 2023Q4: **Helios CELMoD** @ Phase 1 (1 indication(s))
- 2023Q4: **BCL6 LDD** @ Phase 1 (1 indication(s))
- 2023Q4: **obexelimab** @ Phase 3 (1 indication(s))
- 2024Q2: **KRAZATI® (adagrasib)** @ Phase 3 (3 indication(s))
- 2024Q2: **navlimetostat** @ Phase 1 (1 indication(s))
- 2024Q2: **iza-bren** @ Phase 1 (1 indication(s))
- 2024Q2: **alnuctamab + mezigdomide** @ Phase 1 (1 indication(s))
- 2024Q2: **Dual Targeting BCMAxGPRC5D CAR T** @ Phase 1 (1 indication(s))
- 2024Q2: **CD33-GSPT1 ADC** @ Phase 1 (1 indication(s))
- 2024Q3: **RYZ101** @ Phase 3 (2 indication(s))
- 2024Q3: **SOS1 Inhibitor** @ Phase 1 (1 indication(s))
- 2024Q3: **KRASG12D Inhibitor** @ Phase 1 (1 indication(s))
- 2024Q3: **BMS-986454** @ Phase 1 (1 indication(s))
- 2024Q3: **COBENFY (KarXT)** @ Phase 3 (2 indication(s))
- 2024Q3: **TRPC4/5 Inhibitor** @ Phase 1 (1 indication(s))
- 2024Q4: **BMS-986463** @ Phase 1 (1 indication(s))
- 2024Q4: **BMS-986495** @ Phase 1 (1 indication(s))
- 2025Q1: **BMS-986484** @ Phase 1 (1 indication(s))
- 2025Q1: **Anti-Fucosyl GM1 + nivolumab** @ Phase 3 (1 indication(s))
- 2025Q1: **nivolumab+relatlimab HD** @ Phase 3 (1 indication(s))
- 2025Q1: **RYZ801** @ Phase 1 (1 indication(s))
- 2025Q1: **BMS-986460** @ Phase 1 (1 indication(s))
- 2025Q2: **BMS-986482** @ Phase 1 (1 indication(s))
- 2025Q2: **BMS-986488** @ Phase 1 (1 indication(s))
- 2025Q2: **BMS-986490** @ Phase 1 (1 indication(s))
- 2025Q2: **atigotatug + nivolumab** @ Phase 3 (1 indication(s))
- 2025Q2: **subcutaneous nivolumab + relatlimab + rHuPH20** @ Phase 3 (1 indication(s))
- 2025Q4: **BMS-986500** @ Phase 1 (1 indication(s))
- 2025Q4: **BMS-986517** @ Phase 1 (1 indication(s))
- 2025Q4: **CD40xFAP Bispecific** @ Phase 1 (1 indication(s))
- 2025Q4: **CEACAM5-TOPO1 ADC** @ Phase 1 (1 indication(s))
- 2025Q4: **RYZ401** @ Phase 1 (1 indication(s))
- 2025Q4: **WEE1 CELMoD** @ Phase 1 (1 indication(s))
- 2025Q4: **pumitamig (BNT327)** @ Phase 3 (3 indication(s))
- 2025Q4: **OPDIVO QVANTIG™ (nivolumab and hyaluronidase-nvhy) + YERVOY® (ipilimumab)** @ Phase 2 (1 indication(s))
- 2025Q4: **CD19 HD Allo CAR T** @ Phase 1 (1 indication(s))
- 2026Q1: **KarXT Long-Acting Injectable** @ Phase 1 (1 indication(s))
- 2026Q1: **BMS-986506** @ Phase 1 (1 indication(s))
- 2026Q1: **BMS-986523** @ Phase 1 (1 indication(s))
- 2026Q2: **mezigdomide + elranatamab** @ Phase 1 (1 indication(s))
- 2026Q2: **zola-cel** @ Phase 3 (6 indication(s))
- 2026Q2: **BMS-986521** @ Phase 1 (1 indication(s))
- 2026Q2: **BMS-986525** @ Phase 1 (1 indication(s))

## Left from Filed — almost certainly APPROVED & graduated


## Left from Phase 3 — AMBIGUOUS (approval or failure)

- ~2021-01-09…2021-03-20: **OPDIVO® (nivolumab) + Bacillus Calmette-Guerin**
- ~2021-01-09…2021-03-20: **marizomib**
- ~2021-06-28…2021-09-25: **NULOJIX (belatacept)**
- ~2021-12-13…2022-06-30: **OPDIVO® (nivolumab) + relatlimab**
- ~2021-12-13…2022-06-30: **OPDIVO® (nivolumab) + linrodostat**
- ~2021-12-13…2022-06-30: **OPDIVO® (nivolumab) + bempegaldesleukin**
- ~2022-06-30…2022-10-02: **OPDIVO® (nivolumab) + YERVOY® (ipilimumab) + cabozantinib**
- ~2022-06-30…2022-10-02: **EMPLICITI® (elotuzumab) + REVLIMID® (lenalidomide)**
- ~2022-06-30…2022-10-02: **ELIQUIS (apixaban)**
- ~2022-10-02…2022-12-29: **ISTODAX (romidepsin)**
- ~2023-09-21…2023-12-05: **INREBIC (fedratinib)**
- ~2024-07-12…2024-09-27: **ZEPOSIA (ozanimod)**
- ~2024-07-12…2024-09-27: **subcutaneous nivolumab + rHuPH20**
- ~2024-07-12…2024-09-27: **alnuctamab**
- ~2024-09-27…2025-01-07: **ABECMA (ide-cel)**
- ~2025-01-07…2025-04-03: **cendakimab**
- ~2025-01-07…2025-04-03: **subcutaneous relatlimab + nivolumab + rHuPH20**
- ~2025-01-07…2025-04-03: **Anti-Fucosyl GM1 + nivolumab**
- ~2025-04-03…2025-10-04: **OPDIVO® (nivolumab) + YERVOY® (ipilimumab)**
- ~2025-04-03…2025-10-04: **CAMZYOS (mavacamten)**
- ~2025-04-03…2025-10-04: **Opdualag (nivolumab + relatlimab)**

## Left from Phase 1/2 — likely discontinued / deprioritized

- ~2021-01-09…2021-03-20: **Anti-CD73** (was Phase 1)
- ~2021-01-09…2021-03-20: **orva-cel (BCMA CAR T)** (was Phase 2)
- ~2021-01-09…2021-03-20: **Relaxin** (was Phase 1)
- ~2021-06-28…2021-09-25: **CCR2/5 Dual Antagonist** (was Phase 2)
- ~2021-09-25…2021-12-13: **NLRP3 Agonist** (was Phase 1)
- ~2021-09-25…2021-12-13: **CD22 ADC (TriPhase)** (was Phase 1)
- ~2021-09-25…2021-12-13: **IL-2 Mutein** (was Phase 1)
- ~2021-09-25…2021-12-13: **pegbelfermin** (was Phase 2)
- ~2021-09-25…2021-12-13: **JNK Inhibitor** (was Phase 2)
- ~2021-12-13…2022-06-30: **relatlimab** (was Phase 1)
- ~2021-12-13…2022-06-30: **Anti-TIM-3** (was Phase 1)
- ~2021-12-13…2022-06-30: **Anti-OX40** (was Phase 1)
- ~2021-12-13…2022-06-30: **motolimod** (was Phase 1)
- ~2021-12-13…2022-06-30: **BCMA TCE** (was Phase 1)
- ~2021-12-13…2022-06-30: **CD3xCD33 Bispecific (GEMoaB)** (was Phase 1)
- ~2021-12-13…2022-06-30: **Immune Tolerance (Anokion)** (was Phase 1)
- ~2021-12-13…2022-06-30: **FPR-2 Agonist** (was Phase 1)
- ~2021-12-13…2022-06-30: **SARS-CoV-2 mAb Duo** (was Phase 2)
- ~2022-06-30…2022-10-02: **POMALYST (pomalidomide)** (was Phase 2)
- ~2022-06-30…2022-10-02: **FA-Relaxin** (was Phase 2)
- ~2022-10-02…2022-12-29: **BCMA NEX T** (was Phase 1)
- ~2022-10-02…2022-12-29: **CD19 NEX T** (was Phase 2)
- ~2022-10-02…2022-12-29: **S1PR1 Modulator** (was Phase 2)
- ~2022-10-02…2022-12-29: **ROMK Inhibitor** (was Phase 1)
- ~2022-10-02…2022-12-29: **Anti-RIPK1 Inhibitor** (was Phase 1)
- ~2022-12-29…2023-03-27: **STING Agonist** (was Phase 1)
- ~2022-12-29…2023-03-27: **IL-12 Fc** (was Phase 1)
- ~2022-12-29…2023-03-27: **ORENCIA (abatacept)** (was Phase 2)
- ~2022-12-29…2023-03-27: **MK2 Inhibitor** (was Phase 2)
- ~2022-12-29…2023-03-27: **NME** (was Phase 1)
- ~2022-12-29…2023-03-27: **ROR1 CAR T** (was Phase 1)
- ~2023-03-27…2023-08-03: **OPDIVO® (nivolumab) + CDK4/6 Inhibitor** (was Phase 2)
- ~2023-03-27…2023-08-03: **OPDIVO® (nivolumab) + EMPLICITI® (elotuzumab)** (was Phase 2)
- ~2023-03-27…2023-08-03: **CD3xPSCA Bispecific** (was Phase 1)
- ~2023-03-27…2023-08-03: **LSD1 Inhibitor** (was Phase 1)
- ~2023-03-27…2023-08-03: **BCMA ADC** (was Phase 1)
- ~2023-03-27…2023-08-03: **IDHIFA (enasidenib)** (was Phase 2)
- ~2023-08-03…2023-09-21: **Anti-TIGIT** (was Phase 2)
- ~2023-08-03…2023-09-21: **GSPT1 CELMoD (CC-90009)** (was Phase 1)
- ~2023-08-03…2023-09-21: **HSP47** (was Phase 2)
- ~2023-08-03…2023-09-21: **CD47xCD20** (was Phase 1)
- ~2023-08-03…2023-09-21: **RIPK1 Inhibitor** (was Phase 1)
- ~2023-09-21…2023-12-05: **Claudin 18.2 ADC** (was Phase 1)
- ~2023-12-05…2024-03-29: **AHR Antagonist** (was Phase 1)
- ~2023-12-05…2024-03-29: **ONUREG (azacitidine tablets)** (was Phase 2)
- ~2023-12-05…2024-03-29: **branebrutinib** (was Phase 1)
- ~2024-03-29…2024-07-12: **Anti-CTLA-4 NF Probody** (was Phase 2)
- ~2024-03-29…2024-07-12: **BET Inhibitor (BMS-986158)** (was Phase 2)
- ~2024-03-29…2024-07-12: **Anti-SIRPα** (was Phase 1)
- ~2024-03-29…2024-07-12: **Anti-NKG2A** (was Phase 2)
- ~2024-03-29…2024-07-12: **TGFβ Inhibitor** (was Phase 1)
- ~2024-03-29…2024-07-12: **danicamtiv** (was Phase 2)
- ~2024-03-29…2024-07-12: **Anti-CD40** (was Phase 1)
- ~2024-03-29…2024-07-12: **BCMA NKE** (was Phase 1)
- ~2024-03-29…2024-07-12: **Anti-ILT4** (was Phase 1)
- ~2024-03-29…2024-07-12: **DGK Inhibitor** (was Phase 1)
- ~2024-03-29…2024-07-12: **AUGTYRO (repotrectinib)** (was Phase 2)
- ~2024-03-29…2024-07-12: **NME 2** (was Phase 1)
- ~2024-07-12…2024-09-27: **TIGIT Bispecific** (was Phase 1)
- ~2024-07-12…2024-09-27: **farletuzumab ecteribulin** (was Phase 2)
- ~2024-07-12…2024-09-27: **alnuctamab + mezigdomide** (was Phase 1)
- ~2024-09-27…2025-01-07: **Anti-IL-8** (was Phase 2)
- ~2024-09-27…2025-01-07: **Anti-Fucosyl GM1** (was Phase 2)
- ~2024-09-27…2025-01-07: **MAGE A4/8 TCER** (was Phase 1)
- ~2024-09-27…2025-01-07: **SHP2 Inhibitor** (was Phase 1)
- ~2024-09-27…2025-01-07: **NME 1** (was Phase 1)
- ~2025-01-07…2025-04-03: **JNK Inhibitor** (was Phase 1)
- ~2025-01-07…2025-04-03: **CD33 NKE** (was Phase 1)
- ~2025-01-07…2025-04-03: **KRASG12D Inhibitor** (was Phase 1)
- ~2025-04-03…2025-10-04: **BMS-986465 (TYK2 Inhibitor)** (was Phase 2)
- ~2025-04-03…2025-10-04: **afimetoran** (was Phase 2)
- ~2025-04-03…2025-10-04: **IL2-CD25** (was Phase 1)
- ~2025-04-03…2025-10-04: **CK1α Degrader** (was Phase 1)
- ~2025-04-03…2025-10-04: **BMS-986463** (was Phase 1)
- ~2025-04-03…2025-10-04: **BMS-986484** (was Phase 1)
- ~2025-04-03…2025-10-04: **BMS-986490** (was Phase 1)
- ~2025-10-04…2026-01-03: **PKCθ Inhibitor** (was Phase 1)
- ~2025-10-04…2026-01-03: **SOS1 Inhibitor** (was Phase 1)

## Partner changes

- 2021Q3: ＋ **SARS-CoV-2 mAb Duo** — Rockefeller University
- 2022Q3: － **BMS-986465 (TYK2 Inhibitor)** — Nimbus Therapeutics
- 2022Q4: － **subcutaneous nivolumab + rHuPH20** — Ono
- 2022Q4: ＋ **IL-12 Fc** — Dragonfly Therapeutics
- 2022Q4: ＋ **ABECMA (ide-cel)** — 2seventy bio
- 2022Q4: － **ABECMA (ide-cel)** — bluebird bio
- 2022Q4: ＋ **CAMZYOS (mavacamten)** — LianBio
- 2023Q1: ＋ **subcutaneous nivolumab + rHuPH20** — Ono
- 2023Q1: ＋ **REBLOZYL (luspatercept-aamt)** — Merck
- 2023Q1: － **REBLOZYL (luspatercept-aamt)** — Acceleron Pharma
- 2023Q1: ＋ **IDHIFA (enasidenib)** — Servier
- 2023Q1: － **IDHIFA (enasidenib)** — Agios Pharmaceuticals, Inc.
- 2023Q2: － **subcutaneous nivolumab + rHuPH20** — Ono
- 2023Q3: － **eIF2B Activator** — Evotec
- 2023Q4: － **TIGIT Bispecific** — Agenus
- 2024Q1: ＋ **TIGIT Bispecific** — Agenus
- 2024Q1: ＋ **obexelimab** — Zenas
- 2024Q2: － **subcutaneous relatlimab + nivolumab + rHuPH20** — Halozyme
- 2024Q2: － **Anti-Tau (Prothena)** — Prothena
- 2024Q2: － **CAMZYOS (mavacamten)** — LianBio
- 2024Q3: ＋ **KRAZATI® (adagrasib)** — Zai Lab
- 2024Q3: ＋ **iza-bren** — SystImmune
- 2024Q3: ＋ **subcutaneous relatlimab + nivolumab + rHuPH20** — Halozyme
- 2025Q2: ＋ **Anti-CCR8** — Ono
- 2025Q2: ＋ **nivolumab+relatlimab HD** — Ono
- 2025Q2: ＋ **BMS-986495** — Prothena
- 2025Q4: － **Anti-CCR8** — Ono
- 2025Q4: － **KRAZATI® (adagrasib)** — Zai Lab
- 2025Q4: － **subcutaneous nivolumab + relatlimab + rHuPH20** — Ono
- 2025Q4: － **PKCθ Inhibitor** — Exscientia
- 2026Q1: ＋ **Anti-CCR8** — Ono