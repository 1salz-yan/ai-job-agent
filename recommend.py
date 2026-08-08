#!/usr/bin/env python3
"""Daily recommender: search Adzuna, AI-score, deduplicate, save top matches."""
import json, sys, sqlite3
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
from ai_agent import DeepSeekAgent
from job_sources import search_adzuna, search_remoteok


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

DB_PATH = BASE_DIR / "job_agent.db"
QUERIES = [
    # === ANPASSEN: Suchbegriffe für deinen Berufsbereich ===
    # Profession-related (technical Wirtschaftsingenieurwesen roles)
    "Praktikum Projektmanagement",
    "Junior Projektmanager",
    "Trainee Wirtschaftsingenieur",
    "Praktikum Prozessoptimierung",
    "Praktikum Supply Chain",
    "Junior Produktmanager",
    # Interest-based
    "Blockchain Junior",
    "Web3 Praktikum",
    "IoT Praktikum",
    "Fintech Junior",
]


def _row2dict(row):
    return dict(zip([c[0] for c in row.description], row))


def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    rows = db.execute("SELECT key, value FROM settings").fetchall()
    settings = {r["key"]: r["value"] for r in rows}

    app_id = settings.get("adzuna_app_id", "").strip()
    app_key = settings.get("adzuna_app_key", "").strip()
    location = settings.get("default_location", "Berlin").strip()
    agent = DeepSeekAgent(
        settings.get("deepseek_api_key", ""),
        settings.get("deepseek_model", "deepseek-chat"),
    )
    if not app_id or not app_key:
        print("Adzuna-Keys fehlen.")
        sys.exit(1)

    # 1. Search Adzuna (Berlin + Deutschland) + RemoteOK
    all_results = []
    for loc, loc_label in [(location, "Berlin"), ("", "Deutschland")]:
        print(f"🔍 Adzuna ({loc_label}) …", file=sys.stderr)
        for q in QUERIES:
            try:
                all_results.extend(search_adzuna(app_id, app_key, q, loc))
            except Exception as e:
                print(f"   ⚠️ {q}: {e}", file=sys.stderr)
    print("🔍 RemoteOK (web3/crypto/iot) …", file=sys.stderr)
    try:
        all_results.extend(search_remoteok())
    except Exception as e:
        print(f"   ⚠️ RemoteOK: {e}", file=sys.stderr)

    # 2. Dedup + filter out Werkstudent + exclude keywords
    existing_urls = {r[0] for r in db.execute("SELECT url FROM jobs WHERE url != ''").fetchall()}
    exclude_raw = settings.get("exclude_keywords", "") or ""
    exclude = [w.strip().lower() for w in exclude_raw.replace(",", " ").split() if w.strip()]
    new = []
    for x in all_results:
        title = (x.get("title") or "").lower()
        if "werkstudent" in title or "werkstudium" in title:
            continue
        if x["url"] in existing_urls:
            continue
        # Exclude keywords (user preferences, e.g. HR)
        if exclude:
            hay = title + " " + (x.get("description") or "")[:500].lower()
            if any(k in hay for k in exclude):
                continue
        new.append(x)
    new = new[:20]

    if not new:
        print("No new jobs found.")
        return

    # 3. Score
    profile = {}
    row = db.execute("SELECT * FROM profile WHERE id=1").fetchone()
    if row:
        profile = {k: row[k] for k in row.keys()}

    scored = []
    for j in new:
        try:
            m = agent.match(profile, j)
            j["score"] = m.get("score", 0)
            j["summary"] = m.get("summary", "")
        except Exception:
            j["score"] = 0
            j["summary"] = ""
        scored.append(j)

    scored.sort(key=lambda x: (0 if "berlin" in str(x.get("location", "")).lower() else 1, -x["score"]))
    # 3b. Preference boost (companies / content keywords / locations)
    pref_companies = [c.strip().lower() for c in (settings.get("prefer_companies", "") or "").replace(",", " ").split() if c.strip()]
    pref_keywords = [k.strip().lower() for k in (settings.get("prefer_keywords", "") or "").replace(",", " ").split() if k.strip()]
    pref_locs = [l.strip().lower() for l in (settings.get("prefer_locations", "") or "").replace(",", " ").split() if l.strip()]
    for j in scored:
        boost = 0
        hay = (j.get("title", "") + " " + (j.get("description") or "")[:800]).lower()
        if any(c in j.get("company", "").lower() for c in pref_companies):
            boost += 6
        if pref_keywords:
            hits = sum(1 for k in pref_keywords if k in hay)
            boost += min(hits, 3) * 2  # max +6
        if any(l in str(j.get("location", "")).lower() for l in pref_locs):
            boost += 4
        j["score"] = min(j["score"] + boost, 100)
    scored.sort(key=lambda x: (0 if "berlin" in str(x.get("location", "")).lower() else 1, -x["score"]))
    top = scored[:10]

    # 4. Print
    print(f"\n📬 Job-Empfehlungen ({len(new)} neu / {len(all_results)} gescannt)\n")
    for i, j in enumerate(top, 1):
        s = j.get("score", 0)
        e = "🟢" if s >= 70 else "🟡" if s >= 40 else "⚪"
        print(f"{i}. {e} {s}%  {j['title']}")
        print(f"   🏢 {j['company']}  📍 {j.get('location','')}")
        if j.get("salary"):
            print(f"   💰 {j['salary']}")
        if j.get("summary"):
            print(f"   💬 {j['summary'][:150]}")
        print(f"   🔗 {j['url']}\n")

    # 5. Save to DB
    for j in top:
        try:
            db.execute(
                "INSERT OR IGNORE INTO jobs (company, title, location, url, source, "
                "description, salary, status, match_score, match_reasons, job_type, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'wishlist', ?, ?, ?, datetime('now'), datetime('now'))",
                (j["company"], j["title"], j.get("location", ""), j["url"], "adzuna",
                 j.get("description", ""), j.get("salary", ""), j["score"],
                 json.dumps({"score": j["score"], "summary": j.get("summary", "")},
                           ensure_ascii=False),
                 _job_type_from_title(j["title"])),
            )
        except Exception:
            pass
    db.commit()

    print(f"Saved {len(top)} recommendations to Merkliste.")


if __name__ == "__main__":
    main()
