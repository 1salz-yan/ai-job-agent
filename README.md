# AI Job Agent

A self-hosted job-application dashboard with an AI agent. Built for German job
applications: track applications on a Kanban board, let DeepSeek score job
postings against your profile, auto-generate tailored cover letters and CVs,
monitor your inbox for application replies, and export ready-to-send .docx files.

All data stays **local** (SQLite + your machine). No cloud, no accounts, no
telemetry. You bring your own API keys.

---

## Features

- 📋 **Kanban board** — 10-stage pipeline (Merkliste → Beworben → Bestätigt →
  Interviews → Angebot → Absage), drag & drop, search + type filter, trash zone
  for drag-to-delete, per-status list view
- 🎯 **AI match score** — DeepSeek scores each posting 0–100 % against your
  profile (with strict honesty rules and experience filters)
- ✉️ **Anschreiben / CV generation** — tailored to each job description, based
  on *your confirmed profile data* (never fabricated)
- 🗣️ **Interview questions** — 8–10 likely questions with answer hints
- 🔍 **Job search** — Adzuna DE + RemoteOK (web3/blockchain/iot), optional BA
  Jobbörse. Daily/manual recommendations with Berlin-first sorting
- 📬 **Email monitoring** — IMAP inbox polling, AI classifies replies
  (rejected/interview/offer/confirmed/note), one-click apply to the board
- 📊 **Dashboard** — KPI cards, status distribution chart, monthly trend,
  search preferences (exclude & boost keywords)
- 📄 **.docx export** — DIN-5008-formatted cover letter + two-column CV, saved
  in `YYMMDD_Company_City_Role` folders

---

## Requirements

- Python 3.10+
- An API key for an OpenAI-compatible chat API. Default: [DeepSeek](https://platform.deepseek.com/)
  (paid, very cheap). Any provider exposing an OpenAI-style `/chat/completions`
  endpoint works — set the base URL in `.env` (`DEEPSEEK_API_BASE`) or in
  ⚙️ Einstellungen.
- Optional: [Adzuna API](https://developer.adzuna.com/) (free) for job search,
  any IMAP-enabled mailbox (QQ Mail, GMail, Outlook, …) for email monitoring

---

## Installation

```bash
git clone <your-repo-url> ai-job-agent
cd ai-job-agent

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env     # then fill in your keys
python app.py
```

Open http://localhost:8000 in your browser.

---

## Configuration

### 1. Your profile (required before AI generation)

Go to **👤 Profil** and fill in your real, confirmed facts:

| Field | What to put |
|---|---|
| Name | Your full name (used in exports) |
| Kontakt | `Street, City \| email@example.com \| +49 1XX ...` (pipe-separated) |
| Headline | One line, e.g. "Wirtschaftsingenieur (B.Eng.)" |
| Summary | 2–3 sentence professional summary |
| Ausbildung | Degree, school, dates, modules |
| Berufserfahrung | Jobs/internships with verifiable bullets |
| Projekte | Project pool — each with a "Passt zu:" tag so the AI picks the right ones per job |
| Sprachen | Muttersprache, Deutsch, Englisch (with realistic CEFR levels) |
| Kenntnisse | Tools & skills |
| Verfügbarkeit | Your availability window |
| Zielrollen | Roles you are targeting |

> ⚠️ **Honesty rule (by design):** all AI-generated documents are built from
> this profile only. The prompts forbid fabricating anything. What you put in
> the profile is what gets written — nothing more.

### 2. Chat API (required — any OpenAI-compatible provider)

1. The app calls `/chat/completions` on the base URL with a Bearer token.
2. Default provider: **DeepSeek** — create an account at
   https://platform.deepseek.com, add credit, create an API key.
3. Other providers: any OpenAI-compatible endpoint works. Set the base URL via
   `LLM_API_BASE` in `.env` (e.g. `https://api.openai.com/v1`) or in
   ⚙️ Einstellungen.
4. Add the key either in the **⚙️ Einstellungen** tab or in `.env` as
   `LLM_API_KEY=sk-...`.

Model is configurable (`llm_model`, default `deepseek-chat`).

### 3. Job search APIs (optional)

**Adzuna DE** (recommended — free, used by the recommendation engine):
1. Register at https://developer.adzuna.com (free).
2. You get an **App ID** and **App Key**.
3. Enter them in **⚙️ Einstellungen** (`adzuna_app_id`, `adzuna_app_key`)
   or in `.env`.

**BA Jobbörse** (optional):
- Get a key via the Bundesagentur für Arbeit developer portal and set
  `BA_API_KEY`.

**RemoteOK** needs no key — web3/crypto/iot postings are fetched automatically.

### 4. Email monitoring (optional)

Works with any IMAP-enabled mailbox.

1. Enable IMAP on your mailbox:
   - **QQ Mail / Foxmail**: 设置 → 账户 → 开启 POP3/IMAP/SMTP → 生成授权码
     (an app-specific *auth code*, not your login password)
   - **GMail**: enable IMAP in settings → create an App Password (2FA required)
   - **Outlook**: IMAP host `outlook.office365.com:993`
2. In **⚙️ Einstellungen** set:
   - `email_imap_host` / `email_imap_port` (e.g. `imap.qq.com:993`)
   - `email_address`
   - `email_password` — the app-specific password / auth code
3. Click **📬 Posteingang → Jetzt prüfen**.

Each found email is AI-classified (Absage / Interview / Angebot / Bestätigung /
Nachricht). Click **Übernehmen** to apply it to the board (rejection → status
`rejected`, interview → advances the interview stage). If a company isn't on
the board yet, a new entry is created automatically. Wrong classification?
Click **✏️ Korrigieren** and fix it manually.

### 5. Search preferences (optional)

In **📊 Dashboard → Such-Präferenzen** you can set:

| Field | Effect |
|---|---|
| Bevorzugte Firmen | +6 score if the company matches (e.g. `Bosch, Siemens`) |
| Bevorzugte Orte | +4 score if the location matches (e.g. `Berlin, München`) |
| Bevorzugte Themen | +2 per matched keyword, max +6 (e.g. `Datenanalyse, IoT`) |
| Ausschließen | jobs matching these keywords are filtered out entirely (e.g. `HR, Personalwesen`) |

Click **🔄 Stellen suchen & bewerten** on the dashboard to run a search with
these preferences. Top 10 results land in the Kanban **Merkliste**.

---

## Usage

- **Kanban**: drag cards between columns; click a column header to open a
  filterable list view; drag a card to the bottom trash strip to delete.
- **Card modal**: click any card → Match berechnen, ✉️ Anschreiben,
  📄 Lebenslauf anpassen, 🗣️ Interview-Fragen, timeline (Zeitverlauf),
  and 📥 .docx export with "Im Finder zeigen".
- **Jobsuche**: paste a JD URL or text → extract → "Bewerten & merken".
- Generated documents are saved to `~/Desktop/Bewerbung/Bewerbung/<YYMMDD>_<Company>_<City>_<Role>/`
  (folder + filenames include a short role slug so multiple applications to the
  same company on the same day never overwrite each other; long company names
  are abbreviated, e.g. PricewaterhouseCoopers → PwC, to keep filenames
  portal-safe).

---

## Data & Privacy

- Everything lives in `job_agent.db` (SQLite) next to `app.py`.
- API keys live in `.env` and/or the `settings` table — never committed.
- No external servers, no tracking, no uploads. The app only calls the APIs
  you configured (DeepSeek, Adzuna, your IMAP server).
- Generated .docx files are written locally.

---

## Project structure

```
ai-job-agent/
├── app.py            # Flask backend: API, Kanban, email routes, dashboard stats
├── ai_agent.py       # DeepSeek client: match, Anschreiben, CV, interview questions
├── email_monitor.py  # IMAP polling + AI classification
├── job_sources.py    # Adzuna / BA / RemoteOK fetchers
├── recommend.py      # Search + score + top-10 recommendation logic
├── export_docx.py    # .docx export (DIN-5008 letter, two-column CV)
├── static/           # Frontend (vanilla JS, no framework)
├── requirements.txt
└── .env.example      # Environment variable template
```

---

## Roadmap ideas

- PDF export with A4 page size + short filenames (portal-safe uploads)
- More job sources (LinkedIn, StepStone scraping)
- Application statistics / funnel analysis
- Multi-profile support

---

## License

MIT — do whatever you want, but keep your own API keys out of public repos.
