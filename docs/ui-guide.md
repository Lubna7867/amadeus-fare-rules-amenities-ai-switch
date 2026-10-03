# FareLens – UI & Website Developer Guide

For the UI team. This explains how to get the project running on your machine, where everything lives, how the admin UI and the public website are built, and how to submit changes.

There is **no build step** anywhere in this project: the admin UI and the website are plain HTML, CSS and JavaScript. Edit a file, refresh the browser.

---

## 1. Get it running

### Prerequisites
- Git
- Docker Desktop (for the full stack) **or** Python 3.11+ (for backend-only local dev)
- Any editor (VS Code recommended)

### Clone and run with Docker (recommended)

```bash
git clone https://github.com/travelswitch/farelens.git
cd farelens
docker compose up -d
```

Open http://localhost:8000 and sign in with `admin` / `admin`.

The container bakes the UI files in, so after editing anything under `ui/` run:

```bash
docker compose up -d --build app
```

### Faster loop: run the backend locally (UI changes are live on refresh)

```bash
docker compose up -d postgres redis        # datastores only
python -m venv .venv && .venv\Scripts\activate   # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
set DATABASE_URL=postgresql://farelens:farelens@localhost:5432/farelens
set REDIS_URL=redis://localhost:6379/0
uvicorn farelens.main:app --reload
```

For this you must publish the datastore ports: uncomment the two `ports:` blocks in `docker-compose.yml` (Postgres 5432, Redis 6379). The UI is then served straight from the `ui/` folder, so a browser refresh shows your change immediately.

To actually see summaries/chat you need an LLM key: **LLM provider** page, pick Groq (free tier works), paste a key, *Test connection*, *Save & activate*.

---

## 2. Repository map (what you will touch)

```
ui/                          ADMIN UI (served at /ui by the backend)
  index.html                 page shell: login form, sidebar nav, top bar, banners, <script src=app.js>
  assets/
    app.js                   the whole single-page app (routing + every screen)
    styles.css               all styles, design tokens at the top
    icons/*.svg              provider logos (from lobe-icons, MIT)
    travelswitch-logo.png    footer logo

docs/                        PUBLIC WEBSITE (GitHub Pages, https://travelswitch.github.io/farelens/)
  index.html                 the landing page, fully self-contained (inline CSS)
  assets/icons/*.svg         provider logos for the site
  assets/travelswitch-logo.png
  screenshots/*.png          used by the website, the README and the Word/PDF guide
  robots.txt, sitemap.xml    SEO
  .nojekyll                  tells GitHub Pages to serve files as-is
  deployment.md, ui-guide.md documentation
  FareLens.docx / .pdf       product guide (generated, do not hand-edit)

farelens/                    BACKEND (Python / FastAPI) – see section 6
prompts/                     LLM prompt templates (editable from the UI too)
README.md                    GitHub front page
```

---

## 3. Admin UI: how it is built

### 3.1 Structure of `ui/assets/app.js`

One IIFE, no framework, no dependencies. Read it top to bottom once; the sections are marked with `// ---------- name` comments.

| Section | What it does |
|---|---|
| **utils** | `$`, `$$` (querySelector helpers), `esc()` (HTML escaping – always use it when inserting data), `fmtInt/fmtDate/fmtMs`, `toast()`, `modal()`, `confirmDialog()` |
| **api** | `api.get/post/put/del(url, body)` – wraps `fetch`, sends the CSRF header, parses the JSON error envelope into `ApiError`, and redirects to login on 401 |
| **markdown** | `renderMarkdown()` – tiny renderer for the model output (headings, bold, tables, lists) |
| **chart** | `barChart()` – inline SVG stacked bar chart, and `sparkline()` |
| **routing** | the `routes` object maps a hash (`#/overview`, `#/usage`, …) to `{ title, render }`; `navigate()` is called on `hashchange` |
| **auth flow** | `showLogin()`, `showApp()`, login form handler |
| **one `renderXxx(page)` function per screen** | `renderOverview`, `renderUsage`, `renderLLM`, `renderApiKeys`, `renderDatastores`, `renderPrompts`, `renderPlayground`, `renderAccount` |

Every screen follows the same pattern:

```js
async function renderThing(page) {
  const data = await api.get("/api/admin/thing");     // 1. load data
  page.innerHTML = `...template literal with ${esc(data.x)}...`;   // 2. render HTML
  $("#some-button").addEventListener("click", async () => { ... });  // 3. wire events
}
```

Rules of the road:
- **Always wrap data in `esc()`** inside templates (`${esc(value)}`). Never insert raw API strings.
- Use the existing CSS classes (`.card`, `.btn`, `.pill`, `.field`, `.table`, `.spec`, …) before inventing new ones.
- To add a screen: add a `routes` entry, a `renderXxx` function, and a link in the sidebar `<nav>` in `ui/index.html`.
- Playground tabs live inside `renderPlayground` (summary, chat, amenities, farenames); each tab is a `<section class="pg-tab" id="tab-…">`.

### 3.2 Styles: `ui/assets/styles.css`

- **Design tokens** are CSS variables at the top of the file under `:root` (colours, radius, font). Dark mode overrides them under `@media (prefers-color-scheme: dark)`. Change a colour once there and the whole app follows.
- Brand: purple `--primary #6928d9`, `--secondary #442690`, `--accent #6d4dff`, font **Figtree** (loaded from Google Fonts in `index.html`), pill-shaped buttons, 14–20 px radii. These mirror travelswitch.com.
- Sections in the file match the app areas (`Auth`, `Layout`, `Cards`, `Forms`, `Tables`, `Chart`, `Playground`, `Provider picker`, `Prompts`, `Amenities`, …).
- Responsive breakpoints: 960 px (grids collapse) and 720 px (sidebar becomes a top bar).

### 3.3 Admin API the UI talks to

All under `/api/admin/*`, session-cookie auth, mutations need the `X-Requested-With: XMLHttpRequest` header (the `api` helper adds it). Browse them in the backend file `farelens/api/routes/admin.py`. The public endpoints the playground calls are `/api/v1/fare-rules/*` and `/api/v1/amenities/*` (documented at http://localhost:8000/docs).

Quick way to see any response shape: open DevTools → Network while using the page.

### 3.4 Testing a UI change

1. Click through the screen in the browser; watch the DevTools console for errors.
2. `node --check ui/assets/app.js` catches syntax errors.
3. Check both light and dark mode (OS setting or DevTools → Rendering → emulate `prefers-color-scheme`).
4. Check a narrow width (≤ 720 px).

---

## 4. Website: `docs/index.html`

- Single self-contained file: inline `<style>` with the same design tokens as the admin UI, inline SVG icons, no JS needed.
- Sections in order: nav → hero (headline, lead, CTA buttons, terminal snippet) → provider logo row → showcase screenshot → *The problem* (raw vs clean panels) → *How it works* (steps) → *Everything included* (feature cards `.feat`) → gallery (`.gallery` figures + `.stats`) → *Built for* → CTA banner → footer.
- SEO lives in `<head>`: `<title>`, `meta description/keywords`, Open Graph, Twitter card, canonical URL, and a JSON-LD `SoftwareApplication` block. **If you change the product wording in the hero, update these too** so search results and link previews match.
- Images: `screenshots/NN-name.png` (relative paths) and `assets/icons/*.svg`.
- Preview locally by simply opening `docs/index.html` in a browser (double-click) – relative paths work from disk.

### Deploying the website
Nothing to do. GitHub Pages serves the `docs/` folder of the `main` branch. Every push to `main` rebuilds the site within about a minute. Check the build under the repo's *Actions → pages build and deployment* if something does not show up.

---

## 5. Screenshots

`docs/screenshots/` feeds the website, the README and the PDF guide. Keep the numbering:

| File | Screen |
|---|---|
| `00-sign-in.png` | login (not used on the site) |
| `02-overview.png` … `09-prompts.png` | admin screens |
| `10-swagger-ui.png`, `11-swagger-summary-endpoint.png` | API docs |
| `12-amenities.png`, `13-fare-names.png` | playground tabs |

Capture at **1600 × 1000** on a clean install with the password banner hidden (DevTools: `document.getElementById('password-banner').hidden = true`), and with some demo data on the Overview/Usage screens so the charts are not empty. Overwrite the file with the same name; everything that references it updates automatically.

---

## 6. If you need to touch the backend

The backend is Python 3.12 / FastAPI. You rarely need it for UI work, but when a screen needs a new field:

| Want to… | Edit |
|---|---|
| Add/alter an admin endpoint the UI calls | `farelens/api/routes/admin.py` (+ request models in `farelens/schemas/admin.py`) |
| Change what the overview/usage queries return | `farelens/services/usage.py`, `farelens/api/routes/admin.py` (`overview`) |
| Add a provider to the picker | `farelens/llm/registry.py` (the UI form is generated from it) + an adapter in `farelens/llm/providers/` |
| Change a prompt | `prompts/**/*.md` (or the **Prompts** page in the UI, which stores overrides in the database) |
| Change the database schema | add `migrations/000N_name.sql` (applied automatically at startup) |

Run checks before pushing: `ruff check farelens tests` and `pytest -q`.

---

## 7. Submitting changes

1. Create a branch: `git checkout -b ui/short-description`
2. Commit with a clear message, e.g. `ui: tighten spacing on the usage tiles` (keep commits small).
3. Push and open a Pull Request against `main` on GitHub. Describe what changed and attach a before/after screenshot for visual changes.
4. One review, then squash-merge. Merging to `main` publishes the website automatically; the Docker image is rebuilt by whoever deploys.

Do not commit: `.env`, anything under `data/`, `README.preview.html`, `.venv/`. (They are git-ignored already.)

---

## 8. Conventions and gotchas

- **Never put secrets in the front end.** API keys and LLM keys are handled by the backend; the UI only ever receives masked values unless the admin explicitly reveals them.
- The admin UI is marked `noindex`; the website is the public, indexable page.
- Line endings are LF (enforced by `.gitattributes`).
- Text content: British English in the UI copy, no em dashes in marketing copy (house style).
- Credits: provider icons are from lobe-icons (MIT) – keep `ui/assets/icons/LICENSE.md` and the footer credit if you swap icons.
- If you add a new language to the playground selectors, also add it to `LANGUAGE_NAMES` in `farelens/services/prompts.py` so the model gets the language name.

Questions: open an issue on GitHub or ask in the team channel.
