#!/usr/bin/env python3
"""Daily recommender: search Adzuna, AI-score, deduplicate, save top matches."""
import json, re, sys, sqlite3
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
from ai_agent import ChatAgent
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
SEARCH_WORDS_PER_RUN = 9  # = 2 web3 + 7 other (random subsets)

# Search terms are grouped by direction so every run samples across DIFFERENT
# role families instead of 9 random terms all landing in one niche (e.g. all
# Projektmanagement). Each run picks 1 term from every group, then fills the
# remainder randomly — variety by construction.
QUERY_GROUPS = {
    "projekt": ["Praktikum Projektmanagement", "Junior Projektmanager",
                "Praktikum Projektsteuerung", "Trainee Prozessmanagement"],
    "supply": ["Praktikum Supply Chain", "Junior Supply Chain",
               "Praktikum Supply Chain Management", "Praktikum Einkauf"],
    "ops": ["Praktikum Operations", "Praktikum Prozessoptimierung",
            "Praktikum Produktdaten", "Junior Process Engineer"],
    "produkt": ["Junior Produktmanager", "Junior Business Development",
                "Praktikum Business Development", "Trainee Wirtschaftsingenieur"],
    "tech": ["Praktikum Wirtschaftsingenieur", "Praktikum Datenanalyse",
             "Praktikum Digitalisierung", "Junior Data Analyst"],
}
WEB3_QUERIES = [
    "Web3 Praktikum",
    "Web3 Junior",
    "Blockchain Junior",
    "DePIN Praktikum",
    "Crypto Operations",
    "Solidity Junior",
    "IoT Praktikum",
    "Fintech Junior",
    "Digitalisierung Praktikum",
]
# Legacy flat list (kept for settings fallback / docs)
QUERIES = [q for group in QUERY_GROUPS.values() for q in group]


def _url_key(url: str) -> str:
    """Stable dedup key for a job URL.

    Adzuna appends per-request tracking params (se=, v=, utm_*) that CHANGE on
    every search — the same ad then compares unequal as a plain string, so
    applied/confirmed jobs with the same ad id slip past the URL dedup and get
    re-recommended. Key = ad id for Adzuna, full path (no query) otherwise.
    """
    if not url:
        return ""
    path = url.split("?")[0].rstrip("/")
    m = re.search(r"/(?:details|land/ad)/(\d+)$", path)
    if m:
        return f"adzuna:{m.group(1)}"
    return path


def _norm_title(title: str) -> str:
    """Normalized title for fuzzy dedup: lowercase, drop parentheticals like
    (m/w/d), strip punctuation/whitespace. Two postings of the same role from
    the same company (Adzuna re-lists with a NEW ad id) then collide."""
    t = (title or "").lower()
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"[^a-z0-9äöüß ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _row2dict(row):
    return dict(zip([c[0] for c in row.description], row))


def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    rows = db.execute("SELECT key, value FROM settings").fetchall()
    settings = {r["key"]: r["value"] for r in rows}

    app_id = settings.get("adzuna_app_id", "").strip()
    app_key = settings.get("adzuna_app_key", "").strip()
    location = settings.get("default_location", "").strip()
    agent = ChatAgent(
        settings.get("api_key", ""),
        settings.get("llm_model", "deepseek-chat"),
        settings.get("api_base", ""),
    )
    if not app_id or not app_key:
        print("Adzuna-Keys fehlen.")
        sys.exit(1)

    # 1. Search Adzuna (Berlin + Deutschland) + RemoteOK
    import random
    all_results = []
    # Search terms come from settings (user-configurable); fall back to built-in defaults
    def _split(s): return [x.strip() for x in (s or "").split(";") if x.strip()]
    # Grouped sampling: 1 term per role family (projekt/supply/ops/produkt/tech),
    # then fill the rest randomly — every run spans DIFFERENT role directions.
    # Legacy settings.search_queries (flat list) still feeds the pool if set.
    settings_groups = {}
    for g, terms in QUERY_GROUPS.items():
        sg = _split(settings.get(f"search_group_{g}", ""))
        settings_groups[g] = sg or terms
    groups_pool = list(settings_groups.values())
    legacy = _split(settings.get("search_queries", ""))
    web3_all = _split(settings.get("web3_queries", "")) or WEB3_QUERIES
    n_web3 = min(2, len(web3_all))
    # 1 from every group (spread), then fill remaining slots from all pools.
    # Total stays exactly SEARCH_WORDS_PER_RUN: first guarantee web3 quota, then fill.
    picked = [random.choice(g) for g in groups_pool]
    all_terms = [t for g in groups_pool for t in g] + legacy + web3_all
    uniq_terms = list(dict.fromkeys(all_terms))
    web3_picked = [q for q in picked if q in web3_all]
    need_web3 = max(0, n_web3 - len(web3_picked))
    if need_web3:
        extra_w = [t for t in web3_all if t not in picked]
        if extra_w:
            picked = picked + random.sample(extra_w, min(need_web3, len(extra_w)))
    slots = max(0, SEARCH_WORDS_PER_RUN - len(picked))
    remaining = [t for t in uniq_terms if t not in picked]
    fill = random.sample(remaining, min(slots, len(remaining)))
    queries = picked + fill
    random.shuffle(queries)
    for loc, loc_label in [(location, location or "Default"), ("", "Deutschland")]:
        print(f"🔍 Adzuna ({loc_label}) …", file=sys.stderr)
        for q in queries:
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
    # Active/wishlisted postings are never re-recommended; rejected/withdrawn
    # ones ARE (user may want to re-apply).
    active = ("wishlist", "applied", "confirmed", "interview_1", "interview_2",
              "interview_3", "assessment", "offer")
    placeholders = ",".join("?" * len(active))
    # Dedup by NORMALIZED URL key (Adzuna tracking params change per request,
    # so plain URL equality misses re-listings of applied/confirmed jobs).
    existing_urls = {_url_key(r[0]) for r in db.execute(
        f"SELECT url FROM jobs WHERE url != '' AND status IN ({placeholders})", active
    ).fetchall()}
    # Cross-run history: anything recommended in the last 14 days is NOT
    # re-recommended — even if the user cleared the Merkliste, old picks don't
    # instantly resurrect (was the top "还是那些岗位" complaint).
    try:
        existing_urls |= {r[0] for r in db.execute(
            "SELECT url_key FROM rec_history WHERE last_seen > datetime('now', '-14 days')"
        ).fetchall()}
    except Exception:
        pass  # table may not exist on very old DBs
    # Same-company same-normalized-title = the same role re-listed with a new ad id.
    existing_titles = {(r[0].strip().lower(), _norm_title(r[1])) for r in db.execute(
        f"SELECT company, title FROM jobs WHERE status IN ({placeholders})", active
    ).fetchall()}
    exclude_raw = settings.get("exclude_keywords", "") or ""
    exclude = [w.strip().lower() for w in exclude_raw.replace(",", " ").split() if w.strip()]
    new = []
    for x in all_results:
        title = (x.get("title") or "").lower()
        if "werkstudent" in title or "werkstudium" in title:
            continue
        # Dedup: normalized URL key OR same company+normalized title as an active job.
        if _url_key(x.get("url", "")) in existing_urls:
            continue
        if (str(x.get("company", "")).strip().lower(), _norm_title(x.get("title", ""))) in existing_titles:
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

    # 3b. Preference boost (companies / content keywords / locations)
    pref_companies = [c.strip().lower() for c in (settings.get("prefer_companies", "") or "").replace(",", " ").split() if c.strip()]
    pref_keywords = [k.strip().lower() for k in (settings.get("prefer_keywords", "") or "").replace(",", " ").split() if k.strip()]
    pref_locs = [l.strip().lower() for l in (settings.get("prefer_locations", "") or "").replace(",", " ").split() if l.strip()]

    def _loc_rank(x):
        """0 = preferred location match (or no preference configured), 1 = other."""
        loc = str(x.get("location", "")).lower()
        if pref_locs:
            return 0 if any(p in loc for p in pref_locs) else 1
        return 0  # no preference → score decides

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

    # 3c. Pick top 10 — stratified sampling: ~6 high (≥70) + ~4 mid (50–69),
    #     random within each band → quality floor with real variety per run.
    import random as _random
    good = [j for j in scored if j.get("score", 0) >= 50]
    if len(good) <= 10:
        top = sorted(good, key=lambda x: (_loc_rank(x), -x["score"]))[:10]
    else:
        high = [j for j in good if j.get("score", 0) >= 70]
        mid = [j for j in good if j.get("score", 0) < 70]
        top = []
        if high:
            top += _random.sample(high, min(6, len(high)))
        if mid:
            top += _random.sample(mid, min(4, len(mid)))
        if len(top) < 10:  # top up from remaining
            rest = [j for j in good if j not in top]
            top += _random.sample(rest, min(10 - len(top), len(rest)))
        top.sort(key=lambda x: (_loc_rank(x), -x.get("score", 0)))

    # 3d. Diversity guard: max 2 postings per company (bulk ads from one firm
    #     used to flood the list, e.g. 8x "KI-Berater" from the same GmbH).
    #     Also: same company + same normalized title appears ONCE per run,
    #     even when Adzuna lists it under multiple ad ids.
    from collections import Counter as _Counter
    seen_key, seen_title, by_company = set(), set(), _Counter()
    # Companies already present in the active board count toward the 2-limit —
    # otherwise the same firms (Syntex, Basf…) would keep re-appearing every run.
    existing_company_counts = _Counter(
        r[0].strip().lower() for r in db.execute(
            f"SELECT company FROM jobs WHERE status IN ({placeholders})", active
        ).fetchall() if r[0]
    )
    top_diverse = []
    for j in top:
        k = _url_key(j.get("url", ""))
        if k in seen_key:
            continue
        seen_key.add(k)
        comp_key = str(j.get("company", "")).strip().lower()
        t_key = (comp_key, _norm_title(j.get("title", "")))
        if t_key in seen_title:
            continue
        seen_title.add(t_key)
        if by_company[comp_key] + existing_company_counts[comp_key] >= 2:
            continue
        by_company[comp_key] += 1
        top_diverse.append(j)
    top = top_diverse

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

    # 5. Save to DB — upsert by NORMALIZED url key (tracking params change per
    #    request, so raw URL equality would INSERT duplicates of the same ad);
    #    reactivate rejected/withdrawn instead of duplicating.
    url_key_to_id = {_url_key(r[0]): r[1] for r in db.execute(
        "SELECT url, id FROM jobs WHERE url != ''"
    ).fetchall()}
    for j in top:
        try:
            existing_id = url_key_to_id.get(_url_key(j.get("url", "")))
            if existing_id:
                db.execute(
                    "UPDATE jobs SET status='wishlist', match_score=?, match_reasons=?, updated_at=datetime('now') "
                    "WHERE id=?",
                    (j["score"],
                     json.dumps({"score": j["score"], "summary": j.get("summary", "")},
                                ensure_ascii=False),
                     existing_id),
                )
            else:
                db.execute(
                    "INSERT INTO jobs (company, title, location, url, source, "
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
    # Record what was recommended (cross-run dedup) — upsert by url_key
    for j in top:
        try:
            db.execute(
                "INSERT INTO rec_history (url_key, company, title, last_seen) VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(url_key) DO UPDATE SET last_seen=datetime('now')",
                (_url_key(j.get("url", "")), j.get("company", ""), j.get("title", "")),
            )
        except Exception:
            pass
    db.commit()

    print(f"Saved {len(top)} recommendations to Merkliste.")


if __name__ == "__main__":
    main()
