# Feature parity with `reference-script.html` (step 4.2, gate G4.2)

Reference: a hand-made reference script (private, never committed). Checked 2026-10-17 on the synthetic
sample `tests/fixtures/script_exams_sample.json`, in the in-app browser at 375, 768 and 1280 px, light and dark.
`[x]` = built and checked in the browser (the Python-side parts also by tests in `test_build.py` / `test_tabs.py`),
`[+]` = better than the reference, `[ ]` = not built.

| # | Reference feature | State | Where / how checked |
|---|---|---|---|
| 1 | Sticky bar with one coloured button per unit | [x] | one scrolling row at every width (the reference wrapped); bar 106 px on a phone |
| 2 | Tabs Gradivo / Ispiti / Favoriti + three big cards under the title | [x] | `nav.js`; tab remembered, links into another tab open it, hash deep link tested |
| 3 | Search over the question list | [+] | `search.js`; ignores case and diacritics (typing "cuva" finds "čuva"), several words must all match, says how many are shown |
| 4 | "Prikaži samo bez odgovora" filter | [x] | `search.js`, button text toggles |
| 5 | Three-state legend (word for word / own / none) + heart legend | [x] | template, dots with text labels |
| 6 | Question index grouped a) b) c) with status dots | [x] | template |
| 7 | Answer blocks: source line, word-for-word block, own block, "nema u materijalima" block | [x] | step 4.1 |
| 8 | Fold-out hint boxes "Podsjetnik pri rješavanju" (open for printing) | [x] | new answer block kind `hint`; `script.js` opens them for print |
| 9 | Hearts next to questions (index, answer, exam rows) | [x] | `favorites.js` |
| 10 | Favoriti tab: cards per unit, open/close, answer copy with source line, "otvori u gradivu" | [x] | `favorites.js` |
| 11 | Favoriti search (+ "also in answers"), filters (frequency, answer kind), order, open all / close all, remove all with 2nd press | [x] | `favorites.js`, all driven in the browser |
| 12 | Favourite counts (badge on tab, per unit, on card) | [x] | `favorites.js` |
| 13 | "↩ Natrag na pitanje" button + stack, back links change text | [x] | `nav.js`, tested from Ispiti and back |
| 14 | ★ +N exam badges in index and answers | [x] | count = highest exam count of any exam question that leads to the item |
| 15 | Ispiti: predicted exam with "znam" ticks and progress | [x] | rule 3-4 per unit (counts >= 3, at most 4, filled up to 3); ticks kept in the browser |
| 16 | Ispiti: most-asked list | [x] | counts >= 4 |
| 17 | Ispiti: all questions per unit, sorted by count, with stars, answer links and "kako je zapisano" per source | [x] | sources in the order of the source table; Croatian plurals (2 listića · 3 dojave) |
| 18 | Ispiti: search + star filters | [x] | `exams.js` |
| 19 | Ispiti: "Izvori i kako je brojano" fold-out | [x] | `Exams.method_note` |
| 20 | Light / dark switch, remembered | [x] | + `theme-color` for the phone's status bar |
| 21 | Print stylesheet (controls hidden, white page, answers open, tabs print as shown) | [x] | checked with print media emulation: bar, hearts, filters, buttons all `display: none`; hint boxes open on `beforeprint` |
| 22 | Safe-area insets, reduced motion | [x] | CSS; every transition and animation has a reduced-motion rule |
| 23 | Works without JavaScript | [+] | the Gradivo and Ispiti content is plain HTML; tabs, hearts and Favoriti hide cleanly |

Not in the reference but added: 44 px tap targets, `:active` press feedback, hover only on devices that hover,
no request to the server for `/favicon.ico`, nothing loaded from outside the file.

Known limits (not blockers for 4.2):
- Exam data (`Script.exams`) is filled in by Phase 3 (step 3.5, exam-frequency); until then only the synthetic sample has it.
- The JavaScript is checked by hand in the browser and by a syntax check (`node --check`) plus a few code-shape tests
  (no network calls, markup only in known places). Automated browser flows come with step 4.6 (axe + keyboard).
- Print was checked with print-media emulation, not on paper; Ivan's own print preview is part of Phase 7.
