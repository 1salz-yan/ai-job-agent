#!/usr/bin/env python3
"""OpenAI-kompatibler Bewerbungs-Agent (JSON-Mode für Struktur, Plaintext für Langtexte).

Standard-Endpunkt: DeepSeek. Jeder OpenAI-kompatible Chat-Endpunkt funktioniert
(API-Base-URL konfigurierbar über Settings / Umgebungsvariable LLM_API_BASE).
"""
import json, os, re, requests

DEFAULT_API_BASE = "https://api.deepseek.com"

HONESTY_RULES = """STRENGE EHRLICHKEITSREGELN (niemals verletzen):
- Erfinde NICHTS, übertreibe nichts. Nur bestätigte Profil-Fakten (Profil-Datenbank).
- Sprachniveau NIE selbst festlegen: IMMER aus dem Profil-Feld 'languages' übernehmen.
- Notenschnitt/Abschluss/Arbeitgeber: IMMER aus dem Profil übernehmen, nie raten.
- Alle Angaben zu Firmen, Projekten und Leistungen müssen im Profil belegt sein.
- Projekte NUR aufnehmen, wenn sie zur Stelle passen (PROJEKT-POOL im Profil mit 'Passt zu'-Tags).
- Stammdaten (Profil) können sich ändern — bei Widersprüchen immer das Profil nutzen.
- Nutzerprofil-Geschlecht: aus dem Profil übernehmen (z. B. Wirtschaftsingenieurin)."""

SYSTEM_PERSONA = "Du bist ein deutscher Bewerbungs-Assistent. " + HONESTY_RULES


class JobAgentError(Exception):
    pass


def _detect_lang(text: str, title: str = "") -> str:
    """Detect whether a job description is English or German (default: de).
    Empty description falls back to the title, so an English job TITLE like
    'Internship / Thesis Automation' still yields an English CV."""
    t = (text or "")[:4000].lower()
    if not t:
        t = (title or "")[:4000].lower()
    if not t:
        return "de"
    de_markers = ["stelle", "bewerbung", "praktikum", "wir bieten", "aufgaben",
                  "anforderungen", "wir suchen", "ihr profil", "einsatzort",
                  "vergütung", "ab sofort", "team", "unternehmen",
                  "werkstudent", "befristet"]
    en_markers = ["job", "responsibilities", "requirements", "we offer", "you will",
                  "about us", "what you'll", "qualifications", "internship",
                  "the role", "your profile", "apply now", "full-time", "part-time",
                  "intern", "trainee", "thesis", "engineering", "program"]
    de_hits = sum(1 for m in de_markers if m in t)
    en_hits = sum(1 for m in en_markers if m in t)
    return "en" if en_hits > de_hits else "de"


# German function words — if these are dense in an English-target document,
# the LLM left source text untranslated. Used as a post-generation check.
_DE_FUNCTION_WORDS = [
    "als", "und", "der ", "die ", "das ", "für", "mit", "auf", "von", "bei",
    "eine", "einen", "nicht", "auch", "sowie", "ihre", "wurde", "werden",
    "durch", "über", "aus", "nach", "im ", "am ", "zur", "zum", "des ", "dem ",
    "ein ", "einer", "kenntnisse", "ausbildung", "erfahrung", "abschluss",
    "sprachen", "aufgaben", "eigenprojekt", "gebaut", "projektmanagement",
]


def _german_ratio(text: str) -> float:
    """Fraction of lines that look German (dense German function words)."""
    lines = [l.strip() for l in (text or "").splitlines() if len(l.strip()) > 20]
    if not lines:
        return 0.0
    german_lines = 0
    for l in lines:
        low = l.lower()
        hits = sum(1 for w in _DE_FUNCTION_WORDS if w in low)
        if hits >= 2:  # 2+ German function words = a German line
            german_lines += 1
    return german_lines / len(lines)


class ChatAgent:
    """OpenAI-kompatibler Chat-Client (Standard: DeepSeek, Base-URL konfigurierbar)."""

    def __init__(self, api_key, model="deepseek-chat", api_base=None):
        self.api_key = (api_key or "").strip()
        self.model = model or "deepseek-chat"
        self.api_base = (api_base or os.getenv("LLM_API_BASE", "") or DEFAULT_API_BASE).rstrip("/")
        self._url = f"{self.api_base}/chat/completions"

    def _call(self, messages, temperature=0.6, max_tokens=4000, json_mode=True):
        if not self.api_key:
            raise JobAgentError("API-Key fehlt (DeepSeek oder anderer OpenAI-kompatibler Anbieter).")
        body = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            resp = requests.post(self._url, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json=body, timeout=180)
        except requests.RequestException as e:
            raise JobAgentError(f"Keine Verbindung zum API-Endpunkt: {e}") from e
        if resp.status_code != 200:
            raise JobAgentError(f"API Fehler (HTTP {resp.status_code}): {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"]

    def _chat(self, messages, **kw):
        """JSON-mode → returns parsed dict."""
        text = self._call(messages, json_mode=True, **kw)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise JobAgentError(f"DeepSeek lieferte kein JSON") from e

    def _chat_text(self, messages, **kw):
        """Plain-text mode → returns raw string."""
        return self._call(messages, json_mode=False, **kw)

    def test(self):
        return self._chat([{"role": "user", "content": 'Gib NUR JSON: {"ok":true}'}], max_tokens=20).get("ok") is True

    @staticmethod
    def _profile_text(profile: dict) -> str:
        parts = []
        for key, label in [("name", "Name"), ("contact", "Kontakt"), ("headline", "Headline"), ("summary", "Profil"), ("education", "Ausbildung"), ("experience", "Erfahrung"), ("projects", "Projekte"), ("languages", "Sprachen"), ("skills", "Kenntnisse"), ("availability", "Verfügbarkeit"), ("target_roles", "Zielrollen"), ("rules", "Regeln")]:
            v = (profile.get(key) or "").strip()
            if v:
                parts.append(f"### {label}\n{v}")
        return "\n\n".join(parts)

    @staticmethod
    def _job_text(job: dict, max_chars=7000) -> str:
        desc = (job.get("description") or "").strip()
        if len(desc) > max_chars:
            desc = desc[:max_chars] + "\n[… gekürzt …]"
        return f"Unternehmen: {job.get('company','')}\nTitel: {job.get('title','')}\nOrt: {job.get('location','')}\nGehalt: {job.get('salary','') or 'n/a'}\nDeadline: {job.get('deadline','') or 'n/a'}\nURL: {job.get('url','')}\n\nStellenbeschreibung:\n{desc}"

    def match(self, profile: dict, job: dict) -> dict:
        hay = ((job.get("title") or "") + " " + (job.get("description") or "")).lower()
        is_web3 = any(k in hay for k in [
            "web3", "blockchain", "crypto", "defi", "depin", "de pin", "solidity",
            "smart contract", "ethereum", "solana", "validator", "staking", "nft",
            "token", "wallet", "on-chain", "onchain",
        ])
        web3_rule = (
            "WEB3/CRYPTO-STELLE erkannt: Bewerte die Kandidatin gegen die relevanten "
            "Profil-Projekte (z.B. Blockchain/DePIN/Smart-Contract/Staking-Erfahrung im Profil). "
            "Diese Projekte zählen bei Web3-Stellen WIE direkte Berufserfahrung — nicht als fehlend werten.\n"
            "ABER: Auch bei Web3-Stellen gilt die Erfahrungsregel — Senior/Lead/Staff/Principal-Stellen "
            "und Stellen mit ≥2 Jahren geforderter Erfahrung werden stark abgewertet. Die Kandidatin "
            "sucht Praktikum/Junior/Entry-Level (10/2026–03/2027, 5 Monate).\n"
            "Remotestellen sind zugänglich — kein Nachteil.\n"
            if is_web3 else ""
        )
        return self._chat([{"role": "system", "content": SYSTEM_PERSONA}, {"role": "user", "content": (
            "Bewerte die Eignung der Kandidatin ehrlich.\n\n"
            "=== PROFIL ===\n" + self._profile_text(profile) + "\n\n"
            "=== STELLE ===\n" + self._job_text(job) + "\n\n"
            "SCORING-REGELN:\n"
            + web3_rule +
            "- Berufsanfängerin (<1 Jahr Erfahrung). ≥2J gefordert → max 40%. ≥3J → max 25%.\n"
            "- 'Junior' im Titel heißt NICHT automatisch niedrige Anforderungen — JD prüfen.\n"
            "- Nur 10/2026–03/2027 verfügbar (5 Monate). Unbefristet → -15 Punkte.\n"
            "- Werkstudent → 0% (kein Studentenstatus).\n"
            'Antworte mit JSON: {"score":<int 0-100>,"summary":"<2-3 Sätze>","strengths":["..."],"gaps":["..."],"experience_required":"<Jahre>","keywords":["..."],"advice":"<1-2 Sätze>"}'
        )}], temperature=0.3)

    def anschreiben(self, profile: dict, job: dict) -> dict:
        # Use user's own wording as base if available
        base = (profile.get("cv_base") or "").strip()
        base_note = f"=== KANDIDATIN EIGENE FORMULIERUNG (BLUEPRINT) ===\n{base}\n\n" if base else ""
        lang = _detect_lang(job.get("description", ""), job.get("title", ""))
        lang_rule = (
            "Die Stellenanzeige ist auf ENGLISCH → schreibe das Anschreiben vollständig auf "
            "Englisch (Betreff 'Application: [Role]', Anrede 'Dear Hiring Manager', Gruß 'Best regards'). "
            "Übersetze AUCH zitierte Projekte/Erfahrungen ins Englische — kein Deutsch im fertigen Brief.\n"
            if lang == "en" else
            "Die Stellenanzeige ist auf Deutsch → schreibe das Anschreiben auf Deutsch. "
            "Übersetze AUCH zitierte Projekte/Erfahrungen ins Deutsche — kein Englisch im fertigen Brief.\n"
        )
        raw = self._chat_text([{"role": "system", "content": (
            "Du schreibst ein Anschreiben für eine Wirtschaftsingenieurin. "
            "WICHTIG: Übernimm ihre eigene Formulierung und ihren Ton aus dem Blueprint, "
            "erfinde KEINE neuen Fakten und übertreibe NICHT.\n\n"
            "STIL-REGELN (aus User-Feedback):\n"
            "- Kompakt und direkt, wie die Kandidatin selbst schreibt. KEIN Zahlen-Overload.\n"
            "- KEINE marktüblichen Floskeln ('mit großem Interesse', 'hiermit bewerbe ich mich').\n"
            "- Formeller Geschäftsbrief-Stil, 1500–2000 Zeichen, 3–4 Absätze.\n"
            "- Betreff: sachlich, 'Bewerbung: [Rolle] (Kennziffer: [ID])' (auf Englisch: 'Application: [Rolle]').\n"
            "- ALLE Fakten aus dem Profil. NIE erfinden.\n"
            + HONESTY_RULES
        )}, {"role": "user", "content": (
            "Schreibe ein Anschreiben für diese Stelle. Orientiere dich an ihrem Stil.\n"
            + lang_rule +
            "Aufbau: Betreff → Anrede → Hook (Warum DIESE Firma? 1 Satz) → "
            "2 Absätze Beweise (stärkste passende Erfahrungen, kompakt) → "
            "Abschluss (Verfügbarkeit 10/2026–03/2027, 5 Monate) → Gruß + Name\n\n"
            + base_note +
            "=== PROFIL ===\n" + self._profile_text(profile) + "\n\n"
            "=== STELLE ===\n" + self._job_text(job)
        )}], temperature=0.6, max_tokens=4000)
        lines = raw.strip().split("\n")
        betreff = lines[0] if lines else ""
        if betreff.lower().startswith("betreff"):
            betreff = re.sub(r"^[Bb]etreff\s*[:–-]?\s*", "", betreff).strip()
        else:
            betreff = ""
        return {"betreff": betreff, "text": raw.strip()}

    def lebenslauf(self, profile: dict, job: dict) -> dict:
        base = (profile.get("cv_base") or "").strip()
        base_note = f"=== KANDIDATIN EIGENE FORMULIERUNG (BLUEPRINT) ===\n{base}\n\n" if base else ""
        lang = _detect_lang(job.get("description", ""), job.get("title", ""))
        lang_rule = (
            "Die Stellenanzeige ist auf ENGLISCH → schreibe den gesamten Lebenslauf auf Englisch "
            "(Sektionsnamen: Experience, Education, Projects, Skills & Languages). "
            "WICHTIG: Übersetze AUCH alle Projekt-Beschreibungen aus dem Pool ins Englische — "
            "im fertigen Lebenslauf darf KEIN Deutsch vorkommen.\n"
            if lang == "en" else
            "Die Stellenanzeige ist auf Deutsch → schreibe den Lebenslauf auf Deutsch. "
            "WICHTIG: Übersetze AUCH alle Projekt-Beschreibungen aus dem Pool ins Deutsche — "
            "im fertigen Lebenslauf darf KEIN Englisch vorkommen.\n"
        )
        text = self._chat_text([{"role": "system", "content": (
            "Du passt einen Lebenslauf an eine Stellenanzeige an. "
            "WICHTIG: Folge der Struktur und dem Ton der Kandidatin aus dem Blueprint. "
            "Übernimm ihre Formulierungen so weit wie möglich. Erfinde NICHTS.\n\n"
            "REGELN:\n"
            "- Struktur: Name/Kontakt → Profil (kompakt, wie sie schreibt) → Berufserfahrung → "
            "Ausbildung → Projekte → Sprachen & Tools.\n"
            "- PROJEKT-AUSWAHL: Es gibt einen PROJEKT-POOL (siehe Profil). Wähle daraus die 2-3 "
            "Projekte, die am besten zur Stellenanzeige passen — nutze die 'Passt zu'-Tags. "
            "Verschiedene Stellen → verschiedene Projektauswahl. Bei Unsicherheit weglassen.\n"
            "- Kein Zahlen-Overload. Zahlen NUR wo sie natürlich passt.\n"
            "- KEINE Markdown-Zeichen (**/#). KEIN Chinesisch.\n"
            "- Sprachen & Kenntnisse immer als letzte Sektion.\n"
            "- ALLE Fakten aus dem Profil. NIE erfinden.\n"
            + HONESTY_RULES
        )}, {"role": "user", "content": (
            "Erstelle einen an die Stelle angepassten Lebenslauf.\n"
            "Übernimm die Formulierungen der Kandidatin aus dem Blueprint, passe nur an, "
            "was nötig ist (Profil-Satz, PROJEKT-AUSWAHL nach JD).\n"
            + lang_rule +
            "WICHTIG: Lies die Stellenbeschreibung unten genau, extrahiere die wichtigsten "
            "Themen und wähle die passenden Projekte aus dem Pool. Nicht immer dieselben!\n\n"
            + base_note +
            "=== PROFIL (inkl. Projekt-Pool) ===\n" + self._profile_text(profile) + "\n\n"
            "=== STELLE ===\n" + self._job_text(job)
        )}], temperature=0.5, max_tokens=8000)
        # Post-check: for English-target CVs, if too many lines are still
        # German (the LLM often leaves project/experience bullets untranslated
        # when it reuses the user's own wording), run a translation pass.
        if lang == "en" and _german_ratio(text) > 0.25:
            text = self._chat_text([{"role": "system", "content": (
                "Du bist Übersetzerin für einen englischen Lebenslauf. Übersetze die "
                "deutschen Passagen INS Englische. Behalte Struktur, Fakten, Zahlen, "
                "Namen und Formatierung exakt bei. KEINE Markdown-Zeichen. Gib NUR "
                "den übersetzten Lebenslauf zurück."
            )}, {"role": "user", "content": text}], temperature=0.2, max_tokens=8000)
        return {"text": text}

    def interview(self, profile: dict, job: dict) -> dict:
        return self._chat([{"role": "system", "content": SYSTEM_PERSONA}, {"role": "user", "content": (
            "8-10 wahrscheinliche Interview-Fragen (Deutsch). Zu jeder Frage ein Antwort-Hinweis "
            "mit konkreten Zahlen aus dem Profil.\n\n"
            "=== PROFIL ===\n" + self._profile_text(profile) + "\n\n"
            "=== STELLE ===\n" + self._job_text(job) + "\n\n"
            'Antworte mit JSON: {"questions":[{"q":"<Frage>","hint":"<Hinweis>"}]}'
        )}], temperature=0.5)

    def extract(self, raw_text: str) -> dict:
        text = (raw_text or "").strip()
        if not text:
            raise JobAgentError("Kein Text zum Extrahieren.")
        if len(text) > 12000:
            text = text[:12000] + "\n[… gekürzt …]"
        return self._chat([{"role": "system", "content": SYSTEM_PERSONA}, {"role": "user", "content": (
            "Extrahiere aus dem Rohtext einer Stellenanzeige strukturierte Daten.\n\n"
            f"=== ROHTEXT ===\n{text}\n\n"
            'Antworte mit JSON: {"company":"","title":"","location":"","salary":"","deadline":"","employment_type":"","url":"","description":"<bereinigt>","requirements":["..."]}'
        )}], temperature=0.2)
