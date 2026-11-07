# Third-party software and fonts

Only permissive licences are bundled (MIT, Apache-2.0, BSD, SIL OFL). Nothing under AGPL or GPL is copied into this project.
This list covers what is **bundled inside the repository or inside the generated study scripts**. Python and Node
dependencies installed by `uv sync` are listed with their licences by their own metadata (`uv pip list`, `pyproject.toml`).

## Inside every generated study script (files in `src/scriptmaker/render/vendor/`)

| Component | Version | Licence | Source | Used for |
|---|---|---|---|---|
| KaTeX (`katex.min.js`, `katex.min.css`, 13 `.woff2` fonts) | 0.19.0 | MIT (Copyright 2013-2020 Khan Academy and other contributors) | https://github.com/KaTeX/KaTeX, npm `katex` | typesetting formulas offline. Licence text: `render/vendor/katex/LICENSE` |
| IBM Plex Sans (400, 500, 600; latin and latin-ext subsets) | 5.3.0 (npm `@fontsource/ibm-plex-sans`) | SIL Open Font License 1.1 (Copyright 2019 IBM Corp.) | https://github.com/IBM/plex | body text. Licence text: `render/vendor/fonts/LICENSE-IBM-Plex-Sans.txt` |
| STIX Two Text (400, 400 italic, 600; latin, latin-ext, greek subsets) | 5.3.0 (npm `@fontsource/stix-two-text`) | SIL Open Font License 1.1 (Copyright 2001-2021 The STIX Fonts Project Authors) | https://github.com/stipub/stixfonts | headings and mathematical text. Licence text: `render/vendor/fonts/LICENSE-STIX-Two-Text.txt` |

The OFL permits embedding the fonts in documents and software, including these generated files, as long as the licence
notice travels with the font files (it is kept next to them in the repository). The fonts are not sold on their own and
are not renamed.

How they were obtained (2026-10-16, Ivan approved the download, decision D17): `npm pack` of the three packages into an
empty scratch folder with scripts disabled, then only the files listed above were copied.

## Inside the app's pages (files in `src/scriptmaker/web/static/vendor/`)

| Component | Version | Licence | Source | Used for |
|---|---|---|---|---|
| HTMX (`htmx.min.js`) | 2.0.11 | Zero-Clause BSD (0BSD) | https://github.com/bigskysoftware/htmx, npm `htmx.org` | pages that refresh a part of themselves (import progress, later the review screen). Licence text: `web/static/vendor/LICENSE-htmx.txt`. SHA-256 of the file: d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717 |

How it was obtained (2026-10-26, Ivan approved the download, decision D30): `npm pack htmx.org@2.0.11 --ignore-scripts` into an empty scratch folder, then only `dist/htmx.min.js` and `LICENSE` were copied.

## Used from outside the repository (not bundled)
- Tesseract OCR 5.4 (Apache-2.0) installed separately; language data `hrv.traineddata` and `eng.traineddata` from `tesseract-ocr/tessdata_fast` (Apache-2.0).
- RapidOCR 3.10 (Apache-2.0) and its PP-OCRv6 models, installed with the Python package.

## Development only (never inside generated scripts)
- axe-core 4.13.0 (MPL-2.0, Deque Systems): accessibility checker used by `tests/render/a11y/browser_check.mjs`; file and licence in `tests/vendor/axe/` (D22).
