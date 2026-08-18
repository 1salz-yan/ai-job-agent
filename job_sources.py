#!/usr/bin/env python3
"""Job-Quellen: Adzuna DE, BA Jobbörse und URL-Fetch (für JD-Extraktion).

Alle Quellen liefern ein einheitliches Dict:
{source, company, title, location, url, salary, description, employment_type, created}
"""
import re
import sys
import requests
from bs4 import BeautifulSoup

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}


def _clean_html(html: str, max_chars: int = 9000) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_chars:
        text = text[:max_chars] + " …"
    return text


# ---------------------------------------------------------------- Adzuna
def search_adzuna(app_id: str, app_key: str, query: str, location: str = "") -> list:
    if not (app_id and app_key):
        raise RuntimeError(
            "Adzuna-Key fehlt (kostenlos auf developer.adzuna.com registrieren, "
            "dann in den Einstellungen eintragen)."
        )
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": query,
        "where": location or "",
        "results_per_page": 20,
        "content-type": "application/json",
    }
    resp = requests.get(
        "https://api.adzuna.com/v1/api/jobs/de/search/1", params=params, timeout=30
    )
    resp.raise_for_status()
    data = resp.json()
    out = []
    for r in data.get("results", []):
        sal = ""
        if r.get("salary_min") and r.get("salary_max"):
            sal = f"{int(r['salary_min']):,}–{int(r['salary_max']):,} EUR/Jahr".replace(",", ".")
        out.append(
            {
                "source": "adzuna",
                "company": (r.get("company") or {}).get("display_name", ""),
                "title": r.get("title", ""),
                "location": (r.get("location") or {}).get("display_name", ""),
                "url": r.get("redirect_url", ""),
                "salary": sal,
                "description": _clean_html(r.get("description", "")),
                "employment_type": r.get("contract_time", ""),
                "created": r.get("created", ""),
            }
        )
    return out


# -------------------------------------------------------- BA Jobbörse
# Public, keyless API — no registration needed. The BA's old developer portal
# (jobsuche.arbeitsagentur.de/entwicklerportal) is DEAD (NXDOMAIN, 2026-08);
# the jobsuche service itself is open with a fixed client id.
# Docs: https://github.com/bundesAPI/jobsuche-api
BA_CLIENT_ID = "jobboerse-jobsuche"
BA_ENDPOINT = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"


def search_ba(query: str, location: str = "", api_key: str = "") -> list:
    """Search the BA Jobbörse (largest German job database). No key required —
    the API is public; api_key is kept as an optional parameter for
    compatibility. Returns the uniform job dict schema."""
    params = {"was": query, "size": 20}
    if location:
        params["wo"] = location  # empty wo="" causes HTTP 400 on v6
    resp = requests.get(
        BA_ENDPOINT,
        params=params,
        headers={"X-API-Key": BA_CLIENT_ID, "User-Agent": UA["User-Agent"]},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    out = []
    for s in data.get("ergebnisliste", []):
        # v6 field names are German camelCase: stellenangebotsTitel, firma,
        # referenznummer, stellenlokationen[].adresse.ort
        locs = s.get("stellenlokationen") or []
        ort = ""
        if locs:
            adr = (locs[0].get("adresse") or {})
            ort = " ".join(x for x in [adr.get("plz", ""), adr.get("ort", "")] if x)
        out.append(
            {
                "source": "ba",
                "company": s.get("firma") or "",
                "title": s.get("stellenangebotsTitel") or "",
                "location": ort,
                # v6 has no detail URL in the list — build one from refnr
                "url": f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{s.get('referenznummer', '')}",
                "salary": s.get("verguetungsangabe") or "",
                # list API carries no description; the occupational category
                # (alleBerufe) gives the AI scorer at least some signal
                "description": "Berufsfeld: " + ", ".join(s.get("alleBerufe", []) or []),
                "employment_type": "",
                "created": s.get("datumErsteVeroeffentlichung") or "",
            }
        )
    return out


# -------------------------------------------------------- URL-Fetch
# Sites that aggressively block server-side scraping (Cloudflare/PerimeterX).
# For these, guide the user to paste the text instead of failing cryptically.
SCRAPE_BLOCKED = {
    "glassdoor": "Glassdoor blockiert automatische Abrufe (Cloudflare-Schutz). "
                 "Bitte kopiere die Stellenbeschreibung und füge sie als Text ein.",
    "linkedin": "LinkedIn blockiert automatische Abrufe. "
                "Bitte kopiere die Stellenbeschreibung und füge sie als Text ein.",
    "indeed": "Indeed blockiert automatische Abrufe (Captcha). "
              "Bitte kopiere die Stellenbeschreibung und füge sie als Text ein.",
    "kununu": "Kununu blockiert automatische Abrufe. "
              "Bitte kopiere die Stellenbeschreibung und füge sie als Text ein.",
    "xing": "XING blockiert automatische Abrufe. "
            "Bitte kopiere die Stellenbeschreibung und füge sie als Text ein.",
}


def _blocked_hint(url: str) -> str:
    """Return a friendly German hint if the URL's domain is a known
    scrape-blocked site, else empty string."""
    host = (url or "").lower()
    for key, hint in SCRAPE_BLOCKED.items():
        if key in host:
            return hint
    return ""


def fetch_url_text(url: str, max_chars: int = 12000) -> dict:
    hint = _blocked_hint(url)
    if hint:
        raise RuntimeError(hint)
    try:
        resp = requests.get(url, headers=UA, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"URL konnte nicht geladen werden: {e}") from e
    ctype = resp.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        raise RuntimeError("PDF-Dateien werden nicht unterstützt — Text direkt einfügen.")
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else url
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_chars:
        text = text[:max_chars] + " …"
    return {"title": title, "text": text}


def search_arbeitnow(max_pages: int = 1) -> list:
    """Arbeitnow job board API — free, no key, Germany-focused.
    Returns ~176 jobs per page; keeps only entry-level/intern/student titles
    (the candidate targets Praktikum/Junior/Trainee, not senior roles).
    https://www.arbeitnow.com/blog/job-board-api
    """
    out, seen = [], set()
    for page in range(1, max_pages + 1):
        try:
            r = requests.get(
                f"https://www.arbeitnow.com/api/job-board-api?page={page}",
                headers={"User-Agent": UA["User-Agent"]}, timeout=25,
            )
            r.raise_for_status()
            jobs = r.json().get("data", [])
        except Exception as e:
            print(f"   ⚠️ Arbeitnow p{page}: {e}", file=sys.stderr)
            break
        if not jobs:
            break
        for j in jobs:
            title = str(j.get("title", "") or "")
            tl = title.lower()
            if not any(w in tl for w in ["intern", "junior", "trainee", "werkstudent",
                                         "praktikum", "student", "entry", "graduate", "working student"]):
                continue
            if any(w in tl for w in ["senior", "lead ", "head of", "principal", "director of"]):
                continue
            url = j.get("url", "") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({
                "source": "arbeitnow",
                "company": j.get("company_name", "") or "",
                "title": title,
                "location": j.get("location", "") or "",
                "url": url,
                "salary": "",
                "description": _clean_html(j.get("description", "") or ""),
                "employment_type": "",
                "created": j.get("created_at", "") or "",
            })
    return out


# -------------------------------------------------------- RemoteOK
def search_remoteok(tags: list = None) -> list:
    """Fetch blockchain/crypto/web3/iot jobs from RemoteOK (free, no key)."""
    if tags is None:
        tags = ["blockchain", "crypto", "web3", "iot"]
    out = []
    seen = set()
    for tag in tags:
        try:
            resp = requests.get(
                f"https://remoteok.com/api?tag={tag}",
                headers={"Accept": "application/json", "User-Agent": UA["User-Agent"]},
                timeout=30,
            )
            data = resp.json()
            for j in data[1:]:
                cid = j.get("id") or j.get("slug", "")
                if cid in seen:
                    continue
                seen.add(cid)
                loc = str(j.get("location", "") or "")
                tgs = [str(t).lower() for t in (j.get("tags", []) or [])]
                # RemoteOK's tag system is unreliable (bulk-tagged) → keep jobs whose
                # TITLE or TAGS mention web3/crypto, but drop obviously unrelated roles
                title = str(j.get("position", "") or "").lower()
                web3_words = ["blockchain", "crypto", "web3", "web 3", "defi", "ethereum",
                              "solidity", "smart contract", "nft", "bitcoin", "solana",
                              "staking", "wallet", "token", "depin", "validator",
                              "on-chain", "onchain", "cryptocurrency", "digital asset"]
                tech_role = ["engineer", "developer", "analyst", "trader", "architect",
                             "consultant", "lead", "director", "scientist", "manager",
                             "dev", "head", "product"]
                non_tech = ["customer", "support", "marketing", "hr ", "human resource",
                            "designer", "admin", "assistant", "graphic", "social media",
                            "help desk", "sales", "coach", "procurement", "recruit",
                            "biolog", "environmental", "clinical", "specialist",
                            "language", "english", "german", "content", "writer"]
                senior = ["senior", "staff ", "principal", "lead ", "head of", "vp ", "director of",
                          "architect", "manager of", "5+ years", "5 years", "3+ years", "3 years",
                          "experienced"]
                title_hit = any(w in title for w in web3_words)
                tag_hit = any(any(w in t for w in web3_words) for t in tgs) \
                    and any(w in title for w in tech_role)
                if not (title_hit or tag_hit):
                    continue
                if any(w in title for w in non_tech):
                    continue
                # Drop senior/senior-level postings — the candidate targets
                # Praktikum/Junior/Entry-Level only
                if any(w in title for w in senior):
                    continue
                if not any(w in loc.lower() for w in
                          ["germany", "berlin", "deutschland", "europe", "remote", "eu"]):
                    continue
                out.append({
                    "source": "remoteok",
                    "company": j.get("company", ""),
                    "title": j.get("position", ""),
                    "location": loc or "Remote",
                    "url": f"https://remoteok.com/remote-jobs/{j.get('slug', '')}",
                    "salary": "",
                    "description": (j.get("description", "") or "")[:5000],
                    "employment_type": ", ".join(tgs[:5]) if tgs else "",
                    "created": j.get("date", ""),
                })
        except Exception:
            pass
    return out
