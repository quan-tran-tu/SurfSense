# Test queries

Every fact in the "Correct answer" column was checked against the downloaded text,
not recalled. The "Must cite" column is the ground truth for citations: if the
answer is right but cites the wrong documents, the retriever got lucky.

Scenarios are ordered roughly by difficulty. Scenarios 4, 7 and 9 are the ones that
actually break RAG systems — run those first if you only have time for a few.

---

## 1. Baseline — single document, single passage

Establishes that plain lookup works before you blame anything else.

| # | Query | Correct answer | Must cite |
|---|---|---|---|
| 1.1 | How long does a controller have to notify a personal data breach to the supervisory authority? | Not later than 72 hours after becoming aware of it, unless the breach is unlikely to result in a risk. Where later, reasons for the delay must accompany the notification. | 01 |
| 1.2 | What is a "trusted flagger" and who awards that status? | DSA mechanism; status awarded by the Digital Services Coordinator of the Member State where the applicant is established. | 10 (the only document in the corpus using the term) |
| 1.3 | What is "data altruism"? | Voluntary sharing of data on the basis of consent of data subjects, or permissions of data holders, for objectives of general interest, without seeking or receiving a reward. | 07 |

## 2. One entity, many documents (fan-out)

The corpus was picked so these entities really do recur. The counts below are actual
document counts across the 19 files.

| # | Query | Correct answer | Must cite |
|---|---|---|---|
| 2.1 | Every role given to the European Data Protection Board across these laws — list them by act. | EDPB appears in 12 of the 19 documents. A good answer covers at least: GDPR (establishes it, consistency mechanism), EUDPR, the DPF adequacy decision, DGA, DMA, AI Act, NIS2. | 5 or more of: 01, 02, 05, 06, 07, 08, 11, 12, 14, 16, 17, 19 |
| 2.2 | What is ENISA, which act created it, and which other acts give it tasks? | Created by Regulation (EU) 2019/881 (Cybersecurity Act) as the European Union Agency for Cybersecurity. Tasked further by NIS1, NIS2, DORA, the Cyber Resilience Act, the AI Act, eIDAS/eIDAS 2 and the DGA. | 17 **must** be cited as the founding act, plus 3 or more of 07, 12, 13, 14, 15, 16, 18, 19 |
| 2.3 | Which of these laws refer to Regulation (EU) 2016/679, and for what? | 15 of 19 documents cite it. A good answer groups them (data-protection acquis / data economy / platforms / cyber) rather than listing one. | 6 or more distinct documents |

**What to watch for:** a system that retrieves top-k chunks by similarity will
answer 2.1 from the GDPR alone and stop. That is the failure mode — a confident,
correct-looking, single-source answer to a question that spans twelve documents.

## 3. One event, several documents (narrative stitching)

| # | Query | Correct answer | Must cite |
|---|---|---|---|
| 3.1 | What happened to the EU–US Privacy Shield, and what replaced it? | In Case C-311/18 (*Schrems II*) the Court declared Commission Implementing Decision (EU) 2016/1250 (Privacy Shield) invalid. It was replaced in 2023 by the adequacy decision for the EU–US Data Privacy Framework, Decision (EU) 2023/1795. | 05 **and** 06 — both, or the answer is incomplete |
| 3.2 | Were the Standard Contractual Clauses invalidated at the same time as Privacy Shield? | No. The Court upheld the SCC decision while invalidating Privacy Shield. | 05 |

"Privacy Shield" appears in exactly 2 of the 19 documents, so this is a clean test:
either the retriever found both sides of the event or it did not.

## 4. Conflicting numbers — the deadline trap

Four documents contain a reporting deadline expressed in hours. A retriever that
matches on "24 hours" or "72 hours" will mix them up. This is the single most
diagnostic scenario in this file.

| # | Query | Correct answer | Must cite |
|---|---|---|---|
| 4.1 | I run an essential entity under NIS2 and I have had a significant incident. What are my reporting deadlines? | Early warning without undue delay and in any event within **24 hours**; incident notification within **72 hours**; final report not later than **one month** after the incident notification. | 14 only |
| 4.2 | A manufacturer discovers an actively exploited vulnerability in its product. How long does it have to report? | Early warning within **24 hours** of becoming aware, under the Cyber Resilience Act. | 16 only |
| 4.3 | Compare every "within 24 hours" and "within 72 hours" obligation in this corpus and say who owes what to whom. | GDPR 72h: controller to supervisory authority, personal data breach. NIS2 24h: entity to CSIRT/competent authority, early warning. NIS2 72h: incident notification. CRA 24h: manufacturer, actively exploited vulnerability or severe incident. | 01, 14, 16 |
| 4.4 | Does the GDPR's 72-hour rule apply to the NIS2 early warning? | No — separate obligations under separate acts, with different triggers, recipients and clocks. | 01 and 14 |

A wrong answer here looks like "you must report within 24 hours under the GDPR", or
merging the NIS2 and CRA 24-hour rules into a single obligation.

## 5. Supersession and time

| # | Query | Correct answer | Must cite |
|---|---|---|---|
| 5.1 | What did NIS2 change about incident reporting compared with the directive it replaced? | NIS1 required notification "without undue delay" with no fixed clock; NIS2 introduced the staged 24h / 72h / one-month regime. | 13 **and** 14 |
| 5.2 | Is Directive (EU) 2016/1148 still in force? | No — repealed with effect from 18 October 2024 by NIS2. | 14 (13 alone is not enough — a repealed act does not know it was repealed) |
| 5.3 | Which act repealed Directive 95/46/EC? | The GDPR, which says so in its own title. | 01 |
| 5.4 | List these laws by the date they start to apply, earliest first. | Includes: DORA 17 January 2025; Data Act 12 September 2025; AI Act general application 2 August 2026. | 15, 08, 12 at minimum |

5.2 is a directional test. The repealing act carries the fact; the repealed act does
not. A retriever that ranks only by topical similarity will land on document 13.

## 6. Definitions that differ between acts

| # | Query | Correct answer | Must cite |
|---|---|---|---|
| 6.1 | How do "personal data", "data" and "non-personal data" differ across these laws? | GDPR: personal data = any information relating to an identified or identifiable natural person. Data Act: data = any digital representation of acts, facts or information, including sound, visual or audio-visual recordings — deliberately broader and not limited to persons. Reg 2018/1807 covers non-personal data, defined by reference to the GDPR definition. | 01, 08, 09 |
| 6.2 | If a dataset contains both personal and non-personal data, which regime applies? | Mixed datasets — Reg 2018/1807 addresses this; the GDPR continues to apply to the personal data part. | 09 (and 01) |

## 7. Ambiguous term — four different "Boards"

The corpus contains four distinct bodies that the acts themselves shorten to
"the Board". This tests entity disambiguation, not retrieval recall.

| # | Query | Correct answer | Must cite |
|---|---|---|---|
| 7.1 | What does "the Board" mean in this corpus? | It is ambiguous — a good answer says so and enumerates: European Data Protection Board (GDPR), European Data Innovation Board (DGA), European Board for Digital Services (DSA), European Artificial Intelligence Board (AI Act). | 01, 07, 10, 12 |
| 7.2 | What are the Board's tasks under the Data Governance Act? | The European Data Innovation Board's tasks — must **not** answer with EDPB tasks. | 07 |
| 7.3 | Who chairs the Board and how often does it meet? | Deliberately under-specified. The right behaviour is to ask which Board, or to answer for each. | — |

A system that answers 7.1 with "the European Data Protection Board" and nothing else
has silently collapsed four entities into one. That is a correctness bug, not a
ranking bug, and it will not show up in any single-document test.

## 8. Aggregation across the whole corpus

| # | Query | Correct answer | Must cite |
|---|---|---|---|
| 8.1 | Build a table of the maximum fines under every act here. | GDPR: EUR 20 000 000 or 4% of worldwide annual turnover, with a lower tier of EUR 10 000 000 or 2%. NIS2: essential entities at least EUR 10 000 000 or 2%; important entities at least EUR 7 000 000 or 1.4%. DSA: 6% of annual worldwide turnover. DMA: 10%, rising to 20% for a repeated infringement. AI Act: EUR 35 000 000 or 7% for prohibited practices. | 01, 10, 11, 12, 14 |
| 8.2 | Which act has the highest percentage-based penalty ceiling? | The DMA — 20% of total worldwide turnover for a repeated infringement. | 11 |
| 8.3 | Which acts create a new EU-level supervisory or coordination body, and what is each called? | GDPR (EDPB), DGA (European Data Innovation Board), DSA (European Board for Digital Services), AI Act (European Artificial Intelligence Board), plus ENISA under the Cybersecurity Act. | 01, 07, 10, 12, 17 |

8.1 is where numeric confusion shows: watch for 2% being attributed to the DSA, or
the GDPR's 4% turning into 6%.

## 9. Negative controls — the answer is "not here"

Run these. A system that never says "I don't know" is not usable for OSINT.

| # | Query | Correct behaviour |
|---|---|---|
| 9.1 | What does HIPAA require for breach notification? | Say it is not in the corpus. Must not answer from the model's own knowledge without saying so. |
| 9.2 | Summarise Article 147 of the GDPR. | The GDPR has 99 articles. Correct behaviour is to say no such article exists, not to invent one. |
| 9.3 | What are the CCPA's opt-out requirements? | Not in the corpus. |
| 9.4 | Which of these acts regulates cryptocurrency exchanges? | None, in the way implied — MiCA is not in the corpus. DORA covers financial entities' ICT risk, which is adjacent but different. |

## 10. Multi-hop — the answer needs two documents chained

| # | Query | Correct answer | Must cite |
|---|---|---|---|
| 10.1 | If an EU institution deploys a high-risk AI system, who supervises it and under which data-protection rules? | The AI Act designates the European Data Protection Supervisor as market surveillance authority for Union institutions, agencies and bodies, with power to fine them; the EDPS's data-protection mandate over EU institutions comes from Regulation (EU) 2018/1725. | 12 **and** 02 |
| 10.2 | Can a company designated as a gatekeeper benefit from the Data Act's data access rights? | No — the Data Act excludes undertakings designated as gatekeepers under the DMA from being eligible third parties, to avoid concentrating data value further. | 08 **and** 11 |

10.2 only resolves if the system connects "gatekeeper" in the Data Act (11 mentions)
to its definition in the DMA (517 mentions). Either document alone gives a partial
answer that reads complete.

## 11. Folder scope

Requires the four-folder layout from the README. Same question, different scope —
the answers must differ.

| # | Query | Scope | Expected |
|---|---|---|---|
| 11.1 | What are the incident reporting obligations? | folder `cyber-identity` | NIS2 24h/72h/one-month and CRA 24h. Must **not** cite GDPR Article 33. |
| 11.2 | What are the incident reporting obligations? | folder `data-protection` | GDPR 72-hour breach notification only. Must **not** cite NIS2. |
| 11.3 | What are the incident reporting obligations? | whole space | Both, distinguished from each other. |
| 11.4 | Who enforces this? | folder `platforms-ai` | DSA/DMA/AI Act enforcement — Commission, Digital Services Coordinators, national authorities. No data-protection authorities. |

If 11.1 cites a document outside its folder, folder scoping is leaking. If 11.3
answers identically to 11.1, the workspace-wide path is silently scoped.

## 12. Long-document recall

Each of these is buried deep in a large file — document 12 is the largest in the
corpus at 1.26 MB.

| # | Query | Correct answer | Must cite |
|---|---|---|---|
| 12.1 | What does the AI Act say about AI literacy? | Providers and deployers must take measures to ensure a sufficient level of AI literacy among their staff; the AI Board supports the Commission in promoting AI literacy tools. | 12 |
| 12.2 | What is the European Digital Identity Wallet? | Introduced by Regulation (EU) 2024/1183 amending eIDAS; Member States must offer at least one wallet. | 19 (also referenced in 16) |
| 12.3 | What safeguards apply to processing for scientific research purposes? | GDPR Article 89 safeguards — data minimisation, pseudonymisation where the purpose can be achieved that way. | 01 |

## 13. Cross-lingual

The corpus is English. Your prompts are Vietnamese. These check that a Vietnamese
question retrieves English chunks and answers in Vietnamese.

| # | Query | Expected |
|---|---|---|
| 13.1 | Thời hạn thông báo vi phạm dữ liệu cá nhân cho cơ quan giám sát là bao lâu? | 72 giờ, citing 01. |
| 13.2 | So sánh mức phạt tối đa theo GDPR và theo Đạo luật Thị trường Kỹ thuật số. | GDPR EUR 20 triệu / 4%; DMA 10%, 20% khi tái phạm. Citing 01 and 11. |
| 13.3 | Cơ quan nào giám sát việc các tổ chức của EU sử dụng hệ thống AI rủi ro cao? | EDPS, citing 12 (and 02). |

If 13.1 works and 13.2 does not, the embedding model is handling Vietnamese but the
lexical/FTS half of hybrid search is not contributing — which is exactly the split
worth knowing about before a demo.

---

## Scoring

For each query record: answer correct (y/n), citations complete (y/n), citations
correct (y/n), hallucinated content (y/n).

The interesting number is not the pass rate. It is how many queries produce a
**confident and wrong** answer — scenarios 4, 7, 9 and 10 are built to provoke
exactly that, and those are the failures a user will not catch on their own.
