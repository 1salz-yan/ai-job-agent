#!/usr/bin/env python3
"""Job-Quellen: Adzuna DE, BA Jobbörse und URL-Fetch (für JD-Extraktion).

Alle Quellen liefern ein einheitliches Dict:
{source, company, title, location, url, salary, description, employment_type, created}
"""
import re
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
def _ba_token(api_key: str) -> str:
    resp = requests.post(
        "https://rest.arbeitsagentur.de/oauth/getToken",
        data={"client_id": api_key, "client_secret": api_key, "grant_type": "client_credentials"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def search_ba(api_key: str, query: str, location: str = "") -> list:
    if not api_key:
        raise RuntimeError(
            "BA-Jobbörse-Key fehlt (Key über das Entwicklerportal der Arbeitsagentur beantragen, "
            "dann in den Einstellungen eintragen)."
        )
    token = _ba_token(api_key)
    params = {"was": query, "wo": location or "", "size": 20}
    resp = requests.get(
        "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/v2/pc/jobs",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    out = []
    for s in data.get("stellenangebote", []):
        out.append(
            {
                "source": "ba",
                "company": s.get("arbeitgeber") or "",
                "title": s.get("titel") or "",
                "location": s.get("ort") or "",
                "url": s.get("bewerbungsURL") or s.get("url") or "",
                "salary": s.get("entgeltgruppe") or "",
                "description": _clean_html(s.get("beschreibung", "")),
                "employment_type": s.get("arbeitszeitmodelle", ""),
                "created": s.get("veroeffentlicht", ""),
            }
        )
    return out


# -------------------------------------------------------- URL-Fetch
def fetch_url_text(url: str, max_chars: int = 12000) -> dict:
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
                title_hit = any(w in title for w in web3_words)
                tag_hit = any(any(w in t for w in web3_words) for t in tgs) \
                    and any(w in title for w in tech_role)
                if not (title_hit or tag_hit):
                    continue
                if any(w in title for w in non_tech):
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
