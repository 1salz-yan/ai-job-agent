#!/usr/bin/env python3
"""AI Job Agent — lokales Bewerbungs-Dashboard & Agent (Flask + SQLite).

Start:  python3 app.py   →  http://localhost:8000
"""
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

from ai_agent import ChatAgent, JobAgentError
from job_sources import fetch_url_text, search_adzuna, search_ba
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "job_agent.db"
APPLICATION_FOLDER = Path.home() / "Desktop" / "Bewerbung" / "Bewerbung"

STATUSES = ["wishlist", "applied", "confirmed", "interview_1", "interview_2",
            "interview_3", "assessment", "offer", "rejected", "withdrawn"]

COMPANY_MAP = {
    "BMW Group": "BMW", "Bmw Group Recruiting": "BMW",
    "Tesla Berlin": "Tesla",
    "Mercedes Berlin": "Mercedes-Benz", "Mercedes Bremen": "Mercedes-Benz",
    "Mercedes-Benz AG": "Mercedes-Benz",
    "Lidl Recruiting": "Lidl",
    "CocaCola Berlin": "Coca-Cola",
    "Amadeus Fire AG": "Amadeus Fire",
    "Rhenus Office Systems GmbH": "Rhenus Office Systems",
    "Dach für Dach GmbH": "Dach für Dach",
    "Draeger": "Dräger",
    "Stadler Produktion": "Stadler",
    "HRlab": "HR Lab",
}


def _normalize_company(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return n
    n = re.sub(r"\s*Group\s*Recruiting$", "", n, flags=re.I)
    n = re.sub(r"\s*Recruiting$", "", n, flags=re.I)
    n = re.sub(r"\s*GmbH$|\s*AG$|\s*SE$|\s*KGaA$", "", n, flags=re.I).strip()
    return COMPANY_MAP.get(n, n)


def _job_type_from_title(title: str) -> str:
    t = (title or "").lower()
    if "pflichtpraktikum" in t or "pflicht" in t:
        return "pflichtpraktikum"
    if "werkstudent" in t:
        return "werkstudent"
    if "trainee" in t:
        return "trainee"
    if "junior" in t:
        return "junior"
    if "abschlussarbeit" in t or "bachelorarbeit" in t or "masterarbeit" in t or "thesis" in t:
        return "abschlussarbeit"
    if "praktikum" in t or "praktikant" in t or "internship" in t or "intern" in t or "praktika" in t:
        return "praktikum"
    return ""
JOB_FIELDS = {
    "company", "title", "location", "url", "source", "description",
    "salary", "deadline", "status", "notes", "match_score", "match_reasons",
    "job_type",
}

load_dotenv(BASE_DIR / ".env")
app = Flask(__name__, static_folder=str(BASE_DIR / "static"), static_url_path="/static")

# ------------------------------------------------------------------ DB
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT DEFAULT '', contact TEXT DEFAULT '', headline TEXT DEFAULT '',
                summary TEXT DEFAULT '', education TEXT DEFAULT '', experience TEXT DEFAULT '',
                projects TEXT DEFAULT '', languages TEXT DEFAULT '', skills TEXT DEFAULT '',
                availability TEXT DEFAULT '', target_roles TEXT DEFAULT '',
                rules TEXT DEFAULT '', cv_base TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT DEFAULT '', title TEXT DEFAULT '', location TEXT DEFAULT '',
                url TEXT DEFAULT '', source TEXT DEFAULT '', description TEXT DEFAULT '',
                salary TEXT DEFAULT '', deadline TEXT DEFAULT '',
                status TEXT DEFAULT 'wishlist', match_score INTEGER,
                match_reasons TEXT DEFAULT '', notes TEXT DEFAULT '',
                created_at TEXT DEFAULT '', updated_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, content TEXT NOT NULL,
                created_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT UNIQUE, from_addr TEXT DEFAULT '',
                subject TEXT DEFAULT '', body_snippet TEXT DEFAULT '',
                company_name TEXT DEFAULT '',
                received_at TEXT DEFAULT '', job_id INTEGER REFERENCES jobs(id),
                classification TEXT DEFAULT 'ignore',
                summary TEXT DEFAULT '',
                applied INTEGER DEFAULT 0,
                created_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS status_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER REFERENCES jobs(id),
                old_status TEXT DEFAULT '', new_status TEXT DEFAULT '',
                source TEXT DEFAULT 'manual',
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            );
        """)
    # Ensure columns added in later versions
    try: db.execute("ALTER TABLE emails ADD COLUMN company_name TEXT DEFAULT ''")
    except Exception: pass
    # Migrate legacy settings keys (deepseek_* → generic names)
    for old, new in [("deepseek_api_key", "api_key"), ("deepseek_model", "llm_model"),
                     ("deepseek_api_base", "api_base")]:
        db.execute(
            "UPDATE settings SET key=? WHERE key=? AND NOT EXISTS (SELECT 1 FROM settings WHERE key=?)",
            (new, old, new),
        )
    _seed_profile(db)
    _seed_settings_from_env(db)
    _seed_jobs_from_folder(db)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _seed_profile(db):
    if db.execute("SELECT id FROM profile WHERE id = 1").fetchone():
        return
    profile = {
        "name": os.getenv("APPLICANT_NAME", "Max Mustermann"),
        "contact": os.getenv("APPLICANT_CONTACT", "Musterstraße 1, 10115 Berlin  |  max@example.com  |  +49 170 0000000"),
        "headline": "Wirtschaftsingenieur (B.Eng.) — Doppelabschluss",
        "summary": "Platzhalter-Profil: Bitte im Reiter 'Profil' mit eigenen, bestätigten Fakten ersetzen. "
                   "Alle AI-generierten Dokumente basieren auf diesen Angaben — niemals erfundene Inhalte.",
        "education": "B.Eng. Wirtschaftsingenieurwesen (Doppelabschluss)\nHochschule  |  09/20XX – 09/20XX  |  GPA X.X\nBitte eigene Module eintragen",
        "experience": "Bitte eigene praktische Erfahrungen eintragen (Profil → Berufserfahrung)",
        "projects": "Bitte eigene Projekte eintragen (Profil → Projekte)",
        "languages": "Muttersprache  ·  Sprache 2 (Niveau)  ·  Sprache 3 (Niveau)",
        "skills": "Bitte eigene Kenntnisse eintragen",
        "availability": "Ab sofort verfügbar.",
        "target_roles": "Bitte eigene Zielrollen eintragen",
        "rules": "CV-Regeln: Sprachen immer zuletzt; spezifische, belegbare Bulletpoints mit Zahlen; keine Buzzwords; Fakten konsistent halten.",
        "cv_base": "",
    }
    db.execute(
        "INSERT INTO profile (id, name, contact, headline, summary, education, experience, "
        "projects, languages, skills, availability, target_roles, rules, cv_base) "
        "VALUES (1, :name, :contact, :headline, :summary, :education, :experience, "
        ":projects, :languages, :skills, :availability, :target_roles, :rules, :cv_base)",
        profile,
    )


def _seed_settings_from_env(db):
    for env_key, db_key in [
        ("LLM_API_KEY", "api_key"),
        ("LLM_API_BASE", "api_base"),
        ("ADZUNA_APP_ID", "adzuna_app_id"),
        ("ADZUNA_APP_KEY", "adzuna_app_key"),
        ("BA_API_KEY", "ba_api_key"),
        ("EMAIL_PASSWORD", "email_password"),
    ]:
        val = os.getenv(env_key, "").strip()
        if val and not db.execute(
            "SELECT 1 FROM settings WHERE key = ?", (db_key,)
        ).fetchone():
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)", (db_key, val)
            )
    for key in ("llm_model", "api_base", "export_dir", "default_location", "default_query",
                "email_imap_host", "email_imap_port", "email_address",
                "email_poll_interval", "exclude_keywords",
                "prefer_companies", "prefer_keywords", "prefer_locations",
                "search_queries", "web3_queries"):
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, {"llm_model": "deepseek-chat", "api_base": "",
                   "export_dir": "",
                   "default_location": "",
                   "default_query": "Praktikum Wirtschaftsingenieurwesen",
                   "email_imap_host": "imap.example.com", "email_imap_port": "993",
                   "email_address": "", "email_poll_interval": "15",
                   "exclude_keywords": "HR, Personalwesen, Sachbearbeitung, Personal, Admin",
                   "prefer_companies": "", "prefer_keywords": "", "prefer_locations": "",
                   "search_queries": "Praktikum Projektmanagement; Junior Projektmanager; Trainee Wirtschaftsingenieur; Praktikum Prozessoptimierung; Praktikum Supply Chain; Junior Produktmanager; Praktikum Wirtschaftsingenieur; Werkstudent Wirtschaftsingenieur; Junior Process Engineer; Praktikum Operations; Junior Business Development; Praktikum Produktdaten; Junior Supply Chain; Praktikum Projektsteuerung; Trainee Prozessmanagement",
                   "web3_queries": "Web3 Praktikum; Web3 Junior; Blockchain Junior; DePIN Praktikum; Crypto Operations; Solidity Junior; IoT Praktikum; Fintech Junior; Digitalisierung Praktikum"}[key]),
        )


def _seed_jobs_from_folder(db):
    if db.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] > 0 or not APPLICATION_FOLDER.exists():
        return
    try:
        from docx import Document
    except ImportError:
        Document = None
    for folder in sorted(APPLICATION_FOLDER.iterdir()):
        if not folder.is_dir():
            continue
        m = re.match(r"^(\d{6})_(.+)$", folder.name)
        if not m:
            continue
        date_part, rest = m.group(1), m.group(2)
        created = f"{date_part[:2]}-{date_part[2:4]}-{date_part[4:]} 12:00"
        status = "rejected" if "rejected" in rest else "applied"
        rest_clean = rest.replace("_rejected", "")
        tokens = rest_clean.split("_")
        city = tokens[-1] if len(tokens) > 1 else ""
        company = " ".join(tokens[:-1])
        title = ""
        if Document is not None:
            for f in folder.glob("Anschreiben*.docx"):
                try:
                    doc = Document(str(f))
                    for p in doc.paragraphs:
                        t = p.text.strip()
                        if t.lower().startswith("bewerbung"):
                            title = re.sub(r"^Bewerbung\s*[:–-]?\s*", "", t)
                            title = re.sub(r"\s*\(Job-ID:.*?\)\s*$", "", title).strip()
                            break
                        if t and len(title) < 20:
                            title = t
                    if title:
                        break
                except Exception:
                    continue
        db.execute(
            "INSERT INTO jobs (company, title, location, source, status, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (company, title or "Bewerbung", city, "import", status, str(folder),
             created, created),
        )


# ------------------------------------------------------------------ Helpers
def _get_settings() -> dict:
    with get_db() as db:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def _agent(settings: dict) -> ChatAgent:
    return ChatAgent(
        settings.get("api_key"),
        settings.get("llm_model"),
        settings.get("api_base"),
    )


def _profile_row() -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    return dict(row) if row else {}


def _job_row(job_id: int) -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise JobAgentError(f"Stelle #{job_id} nicht gefunden.")
    return dict(row)


def _clean(payload: dict, allowed: set) -> dict:
    return {k: ("" if v is None else v) for k, v in payload.items() if k in allowed}


def _log_status(db, job_id: int, old: str, new: str, source: str = "manual", note: str = ""):
    db.execute(
        "INSERT INTO status_log (job_id, old_status, new_status, source, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, old, new, source, note, _now()),
    )


# ------------------------------------------------------------------ API
@app.get("/")
def index():
    return send_from_directory(BASE_DIR / "static", "index.html")


@app.get("/api/dashboard/stats")
def dashboard_stats():
    """Aggregated stats for dashboard homepage."""
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        applied = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('applied','confirmed',"
            "'interview_1','interview_2','interview_3','assessment')"
        ).fetchone()[0]
        interviews = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN "
            "('interview_1','interview_2','interview_3','assessment')"
        ).fetchone()[0]
        rejected = db.execute("SELECT COUNT(*) FROM jobs WHERE status='rejected'").fetchone()[0]
        offers = db.execute("SELECT COUNT(*) FROM jobs WHERE status='offer'").fetchone()[0]
        # Status distribution for chart
        dist = {}
        for status in STATUSES:
            dist[status] = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status=?", (status,)
            ).fetchone()[0]
        # Monthly trend (last 6 months)
        monthly = db.execute(
            "SELECT substr(created_at,1,7) AS month, COUNT(*) FROM jobs "
            "GROUP BY month ORDER BY month DESC LIMIT 6"
        ).fetchall()
        months = [r["month"] for r in monthly][::-1]
        counts = [r[1] for r in monthly][::-1]
        # Top recommendations (wishlist with scores)
        recs = db.execute(
            "SELECT company, title, match_score, location FROM jobs "
            "WHERE status='wishlist' AND match_score IS NOT NULL "
            "ORDER BY match_score DESC LIMIT 3"
        ).fetchall()
        recs_list = [dict(r) for r in recs]
    return jsonify(
        total=total, applied=applied, interviews=interviews,
        rejected=rejected, offers=offers,
        status_dist={k: dist.get(k, 0) for k in STATUSES},
        monthly_labels=months, monthly_counts=counts,
        top_recs=recs_list,
    )


@app.get("/api/health")
def health():
    return jsonify(ok=True, statuses=STATUSES)


# ---- Profil
@app.get("/api/profile")
def get_profile():
    return jsonify(_profile_row())


@app.put("/api/profile")
def put_profile():
    data = request.get_json(force=True, silent=True) or {}
    allowed = {"name", "contact", "headline", "summary", "education", "experience",
               "projects", "languages", "skills", "availability", "target_roles",
               "rules", "cv_base"}
    vals = _clean(data, allowed)
    with get_db() as db:
        sets = ", ".join(f"{k} = ?" for k in vals)
        db.execute(f"UPDATE profile SET {sets} WHERE id = 1", list(vals.values()))
    return jsonify(_profile_row())


# ---- Settings
@app.get("/api/settings")
def get_settings():
    return jsonify(_get_settings())


@app.put("/api/settings")
def put_settings():
    data = request.get_json(force=True, silent=True) or {}
    allowed = {"api_key", "llm_model", "api_base", "export_dir",
               "adzuna_app_id", "adzuna_app_key",
               "ba_api_key", "default_location", "default_query",
               "email_imap_host", "email_imap_port", "email_address",
               "email_password", "email_poll_interval", "exclude_keywords",
               "prefer_companies", "prefer_keywords", "prefer_locations",
               "search_queries", "web3_queries"}
    with get_db() as db:
        for k, v in _clean(data, allowed).items():
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, str(v)),
            )
    return jsonify(_get_settings())


# ---- Jobs (Kanban)
@app.get("/api/jobs")
def list_jobs():
    status = request.args.get("status")
    with get_db() as db:
        if status:
            rows = db.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC, id DESC", (status,)
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC, id DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/jobs/<int:job_id>")
def get_job(job_id):
    return jsonify(_job_row(job_id))


@app.get("/api/jobs/<int:job_id>/timeline")
def get_job_timeline(job_id):
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM status_log WHERE job_id = ? ORDER BY id ASC", (job_id,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/jobs")
def create_job():
    data = request.get_json(force=True, silent=True) or {}
    vals = _clean(data, JOB_FIELDS)
    vals.setdefault("status", "wishlist")
    if vals.get("company"):
        vals["company"] = _normalize_company(vals["company"])
    if not vals.get("job_type"):
        vals["job_type"] = _job_type_from_title(vals.get("title", ""))
    for k in JOB_FIELDS - set(vals):
        vals[k] = ""
    now = _now()
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO jobs (company, title, location, url, source, description, salary, "
            "deadline, status, notes, job_type, created_at, updated_at) "
            "VALUES (:company, :title, :location, :url, :source, :description, :salary, "
            ":deadline, :status, :notes, :job_type, :created_at, :updated_at)",
            {**vals, "created_at": now, "updated_at": now},
        )
        job_id = cur.lastrowid
    return jsonify(_job_row(job_id)), 201


@app.patch("/api/jobs/<int:job_id>")
def patch_job(job_id):
    old = _job_row(job_id)  # Existenzprüfung
    data = request.get_json(force=True, silent=True) or {}
    vals = _clean(data, JOB_FIELDS)
    if not vals:
        return jsonify(old)
    # Auto-detect job_type when title changes or type is missing
    new_title = vals.get("title", old.get("title", ""))
    if "job_type" not in vals or not vals.get("job_type"):
        vals["job_type"] = _job_type_from_title(new_title)
    with get_db() as db:
        sets = ", ".join(f"{k} = ?" for k in vals)
        db.execute(
            f"UPDATE jobs SET {sets}, updated_at = ? WHERE id = ?",
            [*vals.values(), _now(), job_id],
        )
        if "status" in vals and vals["status"] != old.get("status"):
            _log_status(db, job_id, old.get("status", ""),
                        vals["status"], "manual",
                        vals.get("notes", ""))
    return jsonify(_job_row(job_id))


@app.delete("/api/jobs/<int:job_id>")
def delete_job(job_id):
    with get_db() as db:
        db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    return jsonify(ok=True)


@app.delete("/api/jobs/clear")
def clear_jobs():
    """Batch delete all jobs in a given status (e.g. wishlist → Merkliste leeren)."""
    status = request.args.get("status", "").strip()
    if status not in STATUSES:
        return jsonify(error="Ungültiger Status"), 400
    with get_db() as db:
        cur = db.execute("DELETE FROM jobs WHERE status = ?", (status,))
        return jsonify(ok=True, deleted=cur.rowcount)


# ---- Drafts
@app.get("/api/jobs/<int:job_id>/drafts")
def list_drafts(job_id):
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM drafts WHERE job_id = ? ORDER BY id DESC", (job_id,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/jobs/<int:job_id>/drafts")
def create_draft(job_id):
    _job_row(job_id)
    data = request.get_json(force=True, silent=True) or {}
    kind = str(data.get("kind", "note"))[:32]
    content = str(data.get("content", ""))
    if not content.strip():
        return jsonify(error="Leerer Entwurf"), 400
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO drafts (job_id, kind, content, created_at) VALUES (?, ?, ?, ?)",
            (job_id, kind, content, _now()),
        )
        row = db.execute("SELECT * FROM drafts WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.post("/api/jobs/<int:job_id>/export/<kind>")
def export_docx(job_id, kind):
    """Export Anschreiben or CV as .docx. kind: anschreiben | lebenslauf"""
    if kind not in ("anschreiben", "lebenslauf"):
        return jsonify(error="Ungültiger Typ"), 400
    job = _job_row(job_id)
    # Find latest draft of this kind, or the exact draft the user clicked
    data = request.get_json(force=True, silent=True) or {}
    draft_id = data.get("draft_id")
    with get_db() as db:
        if draft_id:
            row = db.execute(
                "SELECT content FROM drafts WHERE id=? AND job_id=? AND kind=?",
                (draft_id, job_id, kind),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT content FROM drafts WHERE job_id=? AND kind=? ORDER BY id DESC LIMIT 1",
                (job_id, kind),
            ).fetchone()
    if not row:
        return jsonify(error=f"Kein {kind} generiert. Bitte zuerst generieren."), 400
    from export_docx import export_anschreiben, export_lebenslauf
    fn = export_anschreiben if kind == "anschreiben" else export_lebenslauf
    path = fn(job, row["content"])
    return jsonify(ok=True, path=str(path))


@app.post("/api/reveal")
def reveal_file():
    """Öffne Finder und markiere die Datei (macOS: open -R)."""
    data = request.get_json(force=True, silent=True) or {}
    path = str(data.get("path", ""))
    if not path.startswith(str(Path.home())) or ".docx" not in path:
        return jsonify(error="Ungültiger Pfad"), 400
    if not os.path.exists(path):
        return jsonify(error="Datei nicht gefunden"), 404
    import subprocess
    subprocess.Popen(["open", "-R", path])
    return jsonify(ok=True)


# ---- Email
@app.get("/api/emails")
def list_emails():
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM emails ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        return jsonify([dict(r) for r in rows])


@app.put("/api/emails/<int:email_id>")
def update_email(email_id):
    """Manuelle Korrektur der Klassifikation/Zuordnung einer E-Mail."""
    data = request.get_json(force=True, silent=True) or {}
    cls = str(data.get("classification", ""))[:32]
    job_id = data.get("job_id")
    if cls not in ("interview", "rejected", "offer", "confirmed", "note", "ignore"):
        return jsonify(error="Ungültige Klassifikation"), 400
    with get_db() as db:
        row = db.execute("SELECT id FROM emails WHERE id=?", (email_id,)).fetchone()
        if not row:
            return jsonify(error="E-Mail nicht gefunden"), 404
        db.execute(
            "UPDATE emails SET classification=?, job_id=?, applied=0 WHERE id=?",
            (cls, job_id if job_id else None, email_id),
        )
    return jsonify(ok=True, classification=cls, job_id=job_id)


@app.post("/api/emails/check")
def check_emails():
    from email_monitor import check_mail
    settings = _get_settings()
    host = settings.get("email_imap_host", "imap.qq.com").strip()
    port = settings.get("email_imap_port", "993").strip()
    address = settings.get("email_address", "").strip()
    password = settings.get("email_password", "").strip()
    if not address or not password:
        return jsonify(error="E-Mail-Zugangsdaten fehlen. Bitte in den Einstellungen eintragen."), 400
    # Build jobs context
    with get_db() as db:
        jobs_rows = db.execute(
            "SELECT id, company, title FROM jobs WHERE status IN ('applied','interview','wishlist')"
        ).fetchall()
    jobs_ctx = [{"id": r["id"], "company": r["company"], "title": r["title"]} for r in jobs_rows]
    # Known UIDs
    with get_db() as db:
        known = {r["uid"] for r in db.execute("SELECT uid FROM emails").fetchall()}

    agent = _agent(settings)
    results = check_mail(host, port, address, password, known, jobs_ctx, agent)
    # Persist
    with get_db() as db:
        saved = []
        for r in results:
            try:
                db.execute(
                    "INSERT OR IGNORE INTO emails (uid, from_addr, subject, body_snippet, "
                    "received_at, job_id, classification, summary, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["uid"], r.get("from_addr", ""), r.get("subject", ""),
                     r.get("body_snippet", ""), r.get("date", ""),
                     r.get("job_id"), r.get("classification", "ignore"),
                     r.get("summary", ""), _now()),
                )
                saved.append(r)
            except Exception:
                pass
    return jsonify(emails=saved, count=len(saved))


@app.post("/api/emails/<int:email_id>/apply")
def apply_email(email_id):
    with get_db() as db:
        row = db.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
        if not row:
            return jsonify(error="Nicht gefunden"), 404
        if row["applied"]:
            return jsonify(error="Bereits angewandt"), 400
        cls = row["classification"]
        job_id = row["job_id"]
        if cls == "ignore":
            db.execute("UPDATE emails SET applied = 1 WHERE id = ?", (email_id,))
            return jsonify(ok=True, action="dismissed")

        # If no matching job, create one from email data
        if not job_id and cls in ("rejected", "interview", "offer"):
            from_addr = row["from_addr"] or ""
            ai_company = row["company_name"] or ""
            company = ""
            # Prefer AI-classified company name
            if ai_company and len(ai_company) > 1:
                company = ai_company.title()
            elif "<" in from_addr:
                company = from_addr.split("<")[0].strip().strip('"').title()
            elif "@" in from_addr:
                company = from_addr.split("@")[-1].split(".")[0].title()
            if not company or company in ("Noreply", "Mail", "Info"):
                company = "Unbekannt"
            title = (row["subject"] or "").replace("Bewerbung", "").replace("RE:", "").replace("Re:", "").strip()[:120] or "(aus E-Mail)"
            cur = db.execute(
                "INSERT INTO jobs (company, title, status, notes, created_at, updated_at, source, job_type) "
                "VALUES (?, ?, 'applied', ?, ?, ?, 'email', ?)",
                (company, title, "", _now(), _now(), _job_type_from_title(title)),
            )
            job_id = cur.lastrowid
            db.execute("UPDATE emails SET job_id = ? WHERE id = ?", (job_id, email_id))
            _log_status(db, job_id, None, "applied", "email", f"Aus E-Mail erstellt: {row['subject']}")

        job = db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        old_status = job["status"] if job else ""
        new_status = None
        if cls == "rejected":
            new_status = "rejected"
        elif cls == "offer":
            new_status = "offer"
        elif cls == "confirmed":
            new_status = "confirmed"
        elif cls == "interview":
            # Progress: applied/confirmed → interview_1 → interview_2 → interview_3
            if old_status in ("interview_1",):
                new_status = "interview_2"
            elif old_status in ("interview_2",):
                new_status = "interview_3"
            elif old_status in ("interview_3",):
                new_status = "assessment"
            else:
                new_status = "interview_1"

        note = f"📬 {row['received_at']}: {row['summary'] or cls}"
        if new_status:
            db.execute(
                "UPDATE jobs SET status = ?, notes = COALESCE(notes || '\n\n' || ?, ?), "
                "updated_at = ? WHERE id = ?",
                (new_status, note, note, _now(), job_id),
            )
            _log_status(db, job_id, old_status, new_status, "email", note)
        else:
            db.execute(
                "UPDATE jobs SET notes = COALESCE(notes || '\n\n' || ?, ?), "
                "updated_at = ? WHERE id = ?",
                (note, note, _now(), job_id),
            )
        db.execute("UPDATE emails SET applied = 1 WHERE id = ?", (email_id,))
    return jsonify(ok=True, action=cls, job_id=job_id, new_status=new_status)


@app.post("/api/emails/<int:email_id>/dismiss")
def dismiss_email(email_id):
    with get_db() as db:
        db.execute("UPDATE emails SET applied = 1 WHERE id = ?", (email_id,))
    return jsonify(ok=True)


# ---- AI
@app.post("/api/ai/match")
def ai_match():
    job = _job_row(int((request.get_json(silent=True) or {}).get("job_id", 0)))
    agent = _agent(_get_settings())
    result = agent.match(_profile_row(), job)
    with get_db() as db:
        db.execute(
            "UPDATE jobs SET match_score = ?, match_reasons = ?, updated_at = ? WHERE id = ?",
            (int(result.get("score", 0)), json.dumps(result, ensure_ascii=False),
             _now(), job["id"]),
        )
    return jsonify(result)


@app.post("/api/ai/<kind>")
def ai_generate(kind):
    """kind in: anschreiben | lebenslauf | interview"""
    if kind not in ("anschreiben", "lebenslauf", "interview"):
        return jsonify(error="Unbekannter Typ"), 404
    job = _job_row(int((request.get_json(silent=True) or {}).get("job_id", 0)))
    agent = _agent(_get_settings())
    profile = _profile_row()
    if kind == "anschreiben":
        out = agent.anschreiben(profile, job)
        content = f"{out.get('betreff', '')}\n\n{out.get('text', '')}"
    elif kind == "lebenslauf":
        out = agent.lebenslauf(profile, job)
        content = out.get("text", "")
    else:
        out = agent.interview(profile, job)
        content = "\n\n".join(
            f"Frage {i+1}: {q.get('q', '')}\n→ Hinweis: {q.get('hint', '')}"
            for i, q in enumerate(out.get("questions", []))
        )
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO drafts (job_id, kind, content, created_at) VALUES (?, ?, ?, ?)",
            (job["id"], kind, content, _now()),
        )
        row = db.execute("SELECT * FROM drafts WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.post("/api/ai/extract")
def ai_extract():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    text = (data.get("text") or "").strip()
    source_url = url
    if url:
        try:
            fetched = fetch_url_text(url)
            text = fetched["text"]
            source_url = url
        except RuntimeError as e:
            return jsonify(error=str(e)), 400
    if not text:
        return jsonify(error="Bitte URL oder Text angeben."), 400
    agent = _agent(_get_settings())
    result = agent.extract(text)
    result["url"] = result.get("url") or source_url
    return jsonify(result)


# ---- Jobsuche
@app.post("/api/search")
def search():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    location = (data.get("location") or "").strip()
    source = (data.get("source") or "both").strip()
    settings = _get_settings()
    results, errors = [], []
    try:
        if source in ("adzuna", "both"):
            results += search_adzuna(
                settings.get("adzuna_app_id", ""), settings.get("adzuna_app_key", ""),
                query, location,
            )
    except Exception as e:
        errors.append(f"Adzuna: {e}")
    try:
        if source in ("ba", "both"):
            results += search_ba(settings.get("ba_api_key", ""), query, location)
    except Exception as e:
        errors.append(f"BA Jobbörse: {e}")
    if not results and not errors:
        errors.append("Keine Ergebnisse. Keys in den Einstellungen prüfen.")
    return jsonify(results=results, errors=errors)


@app.post("/api/import")
def import_jobs():
    data = request.get_json(silent=True) or {}
    jobs = data.get("jobs") or []
    created = []
    with get_db() as db:
        for j in jobs:
            vals = _clean(j, JOB_FIELDS)
            vals.setdefault("status", "wishlist")
            for k in JOB_FIELDS - set(vals):
                vals[k] = ""
            cur = db.execute(
                "INSERT INTO jobs (company, title, location, url, source, description, salary, "
                "deadline, status, notes, created_at, updated_at) "
                "VALUES (:company, :title, :location, :url, :source, :description, :salary, "
                ":deadline, :status, :notes, :created_at, :updated_at)",
                {**vals, "created_at": _now(), "updated_at": _now()},
            )
            created.append(cur.lastrowid)
    return jsonify(ids=created)


@app.post("/api/recommend/run")
def run_recommend():
    """Manuelle Jobsuche + AI-Scoring (wie früher täglich per Cron)."""
    try:
        import recommend as rec
        rec.main()
        with get_db() as db:
            n = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='wishlist' AND source IN ('adzuna','remoteok') "
                "AND date(created_at) = date('now')"
            ).fetchone()[0]
        return jsonify(ok=True, new=n)
    except Exception as e:
        return jsonify(error=f"Empfehlung fehlgeschlagen: {e}"), 500


# ---- AI
@app.post("/api/ai/test")
def ai_test():
    try:
        ok = _agent(_get_settings()).test()
        return jsonify(ok=ok)
    except JobAgentError as e:
        return jsonify(ok=False, error=str(e)), 400


@app.post("/api/ai/analyze")
def ai_analyze():
    """Analyze all jobs against profile — identify strengths, gaps, trends."""
    settings = _get_settings()
    agent = _agent(settings)
    profile = _profile_row()
    with get_db() as db:
        jobs = [dict(r) for r in db.execute(
            "SELECT company, title, location, match_score, match_reasons, status, source "
            "FROM jobs WHERE match_score IS NOT NULL ORDER BY match_score DESC LIMIT 30"
        ).fetchall()]
    if not jobs:
        return jsonify(error="Keine bewerteten Stellen vorhanden. Bitte zuerst Jobs matchen."), 400

    jobs_text = "\n".join(
        f"- [{j['status']}] {j['company']} | {j['title']} | {j['location']} | "
        f"Score: {j.get('match_score','?')}% | Source: {j.get('source','?')}"
        for j in jobs
    )
    profile_text = ChatAgent._profile_text(profile)

    result = agent._chat([{"role": "system", "content": (
        "Du bist ein Karriere-Strategie-Analyst. Analysiere das Bewerbungsprofil einer "
        "Wirtschaftsingenieurin (B.Sc., HTW Berlin & Tongji) gegen ihre bewerteten Stellen. "
        "Gib ehrliche, datengestützte Einsichten. Antworte mit JSON."
    )}, {"role": "user", "content": (
        "Analysiere die unten stehenden bewerteten Stellen und das Profil. "
        "Identifiziere Muster, Stärken, Lücken und gib strategische Empfehlungen.\n\n"
        f"=== PROFIL ===\n{profile_text}\n\n"
        f"=== BEWERTETE STELLEN (letzte 30) ===\n{jobs_text}\n\n"
        'Antworte mit JSON: {'
        '"summary": "<3-4 Sätze: Gesamteinschätzung der Wettbewerbsposition>",'
        '"top_strengths": ["<3-5 konkrete Stärken, die in vielen Stellen hoch scoren>"],'
        '"key_gaps": ["<3-5 Fähigkeiten/Kenntnisse, die systematisch fehlen>"],'
        '"best_role_categories": ["<2-3 Rollentypen mit den höchsten Scores>"],'
        '"recommended_focus": "<2-3 Sätze: worauf fokussieren für höhere Match-Raten?>",'
        '"skill_recommendations": ["<2-3 konkrete Skills, die zu lernen sich lohnen würde>"],'
        '"search_strategy": "<Empfehlung für optimale Suchbegriffe>"'
        '}'
    )}], temperature=0.5, max_tokens=1500)
    return jsonify(result)


# ---- Background email poller ----
def handle_agent_error(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(Exception)
def handle_error(e):
    return jsonify(error=f"Interner Fehler: {e}"), 500


if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", "8000"))
    print(f"\n  AI Job Agent läuft:  http://localhost:{port}\n")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
