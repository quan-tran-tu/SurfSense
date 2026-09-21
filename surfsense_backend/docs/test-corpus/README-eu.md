# EU Digital Law — retrieval test corpus

19 real legal acts in one tightly interlinked field. They were chosen so that the
same entities, events and numbers recur across documents, which is what makes the
hard retrieval scenarios in [`queries.md`](queries.md) possible.

## Get the files

```bash
bash surfsense_backend/docs/test-corpus/download.sh          # -> ./files/
bash surfsense_backend/docs/test-corpus/download.sh /path/to/dir
```

~10.7 MB, 19 `.html` files, ~2.5M characters of text. All 19 URLs were verified
working on 2026-09-08.

**Source host is `publications.europa.eu` (CELLAR), not `eur-lex.europa.eu`.**
EUR-Lex sits behind an AWS WAF captcha and returns `HTTP 202` + a JavaScript
challenge to any scripted client. CELLAR serves the same official texts via plain
HTTP content negotiation with no challenge.

**Why HTML and not PDF.** `.html` lands in `DIRECT_CONVERT`
(`app/etl_pipeline/file_classifier.py:102`), which is parsed in-process — no
LlamaCloud, no Unstructured, no Docling, no GPU. That keeps a retrieval test a
retrieval test instead of an OCR test. If you *want* to exercise the PDF path,
download the same documents as PDF manually from EUR-Lex in a browser.

## The documents

| # | Document | CELEX | Folder |
|---|---|---|---|
| 01 | GDPR — Regulation (EU) 2016/679 | 32016R0679 | data-protection |
| 02 | EUDPR — Regulation (EU) 2018/1725 (EU institutions) | 32018R1725 | data-protection |
| 03 | Data Protection Directive 95/46/EC *(repealed)* | 31995L0046 | data-protection |
| 04 | ePrivacy Directive 2002/58/EC | 32002L0058 | data-protection |
| 05 | *Schrems II* judgment, Case C-311/18 | 62018CJ0311 | data-protection |
| 06 | EU–US Data Privacy Framework adequacy decision 2023/1795 | 32023D1795 | data-protection |
| 07 | Data Governance Act — Regulation (EU) 2022/868 | 32022R0868 | data-economy |
| 08 | Data Act — Regulation (EU) 2023/2854 | 32023R2854 | data-economy |
| 09 | Free flow of non-personal data — Regulation (EU) 2018/1807 | 32018R1807 | data-economy |
| 10 | Digital Services Act — Regulation (EU) 2022/2065 | 32022R2065 | platforms-ai |
| 11 | Digital Markets Act — Regulation (EU) 2022/1925 | 32022R1925 | platforms-ai |
| 12 | AI Act — Regulation (EU) 2024/1689 | 32024R1689 | platforms-ai |
| 13 | NIS1 Directive (EU) 2016/1148 *(repealed)* | 32016L1148 | cyber-identity |
| 14 | NIS2 Directive (EU) 2022/2555 | 32022L2555 | cyber-identity |
| 15 | DORA — Regulation (EU) 2022/2554 | 32022R2554 | cyber-identity |
| 16 | Cyber Resilience Act — Regulation (EU) 2024/2847 | 32024R2847 | cyber-identity |
| 17 | Cybersecurity Act — Regulation (EU) 2019/881 (ENISA) | 32019R0881 | cyber-identity |
| 18 | eIDAS — Regulation (EU) No 910/2014 | 32014R0910 | cyber-identity |
| 19 | eIDAS 2 — Regulation (EU) 2024/1183 | 32024R1183 | cyber-identity |

Direct link for any one of them (swap the CELEX):

```
http://publications.europa.eu/resource/celex/32016R0679
  with header:  Accept: application/xhtml+xml   and   Accept-Language: eng
```

Documents 03 and 04 predate the XHTML format and need `Accept: text/html` instead;
`download.sh` already handles that.

## Suggested setup

One search space, `EU Digital Law`, with four folders matching the last column
above. Several scenarios in `queries.md` depend on that split — they check that a
folder-scoped question stays inside its folder, and that a workspace-wide question
does not.

Upload order does not matter, but let indexing finish before you start: with
19 documents this is where a chunk-count of zero would show up (see the Celery
prefork / CUDA note in project memory — check that documents have chunks, not just
rows).

## Optional: Vietnamese add-on

Only two Vietnamese sources were verified reachable from a script:

- Luật An ninh mạng 24/2018/QH14 — `https://datafiles.chinhphu.vn/cpp/files/vbpq/2022/07/24-2018-qh14..pdf` (525 KB, text PDF)
- Luật An ninh mạng 116/2025/QH15 — `https://datafiles.chinhphu.vn/cpp/files/vbpq/2026/01/luat116-2025.pdf` (17 MB, likely scanned — this exercises OCR, not retrieval)

Those two make a supersession pair (2018 law vs 2025 law) if you want a
Vietnamese-language version of scenario 5. `vbpl.vn` returns 403 to scripts and
`thuvienphapluat.vn` needs a login, so the rest of a Vietnamese corpus has to be
collected by hand from `congbao.chinhphu.vn`.
