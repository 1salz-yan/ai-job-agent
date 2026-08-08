#!/usr/bin/env python3
"""Email-Monitor: IMAP-Polling + AI-Klassifikation (DeepSeek).

QQ/foxmail: imap.qq.com:993, 授权码 statt Passwort
Gmail: imap.gmail.com:993, App-Passwort
"""
import imaplib
import email as em
import email.utils as email_utils
from email.header import decode_header
from datetime import datetime, timezone
import re
import json

from ai_agent import ChatAgent, JobAgentError

MAX_FETCH = 30  # max unread emails per check


def _decode(s: str) -> str:
    parts = decode_header(s or "")
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(charset or "utf-8", errors="replace"))
            except Exception:
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(text or "")
    return "".join(out)


def _body(msg) -> str:
    """Extract plain-text body snippet."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                cs = part.get_content_charset() or "utf-8"
                try:
                    body = part.get_payload(decode=True).decode(cs, errors="replace")
                except Exception:
                    pass
                break
    else:
        cs = msg.get_content_charset() or "utf-8"
        try:
            body = msg.get_payload(decode=True).decode(cs, errors="replace")
        except Exception:
            pass
    return (body or "")[:2000]


def _date(msg) -> str:
    try:
        d = email_utils.parsedate_to_datetime(msg.get("Date", ""))
        return d.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def check_mail(host, port, address, password, known_uids: set, jobs_ctx: list,
               agent: ChatAgent) -> list:
    """Poll IMAP inbox for unseen emails, classify with AI.

    Args:
        known_uids: set of already-processed IMAP UIDs (string)
        jobs_ctx: [{"id": int, "company": str, "title": str}, ...]
    Returns: [{"uid": str, "from": str, "subject": str, "date": str,
                "body_snippet": str, "job_id": int|None,
                "classification": "interview"|"rejected"|"offer"|"note"|"ignore",
                "summary": str}, ...]
    """
    if not address or not password:
        raise JobAgentError(
            "E-Mail-Konfiguration fehlt. Bitte in den Einstellungen IMAP-Daten eintragen."
        )
    try:
        mail = imaplib.IMAP4_SSL(host, int(port), timeout=30)
        mail.login(address, password)
        mail.select("INBOX", readonly=True)
    except imaplib.IMAP4.error as e:
        raise JobAgentError(
            f"IMAP-Login fehlgeschlagen ({host}:{port}). "
            f"Für QQ-Mail: 授权码 (nicht Passwort!) verwenden. Fehler: {e}"
        ) from e

    try:
        status, raw = mail.uid("search", "UTF-8", "UNSEEN")
        if status != "OK":
            return []
        uids = raw[0].split()[-MAX_FETCH:]  # newest first
        results = []
        for uid in uids:
            uid_str = uid.decode()
            if uid_str in known_uids:
                continue
            status, data = mail.uid("fetch", uid, "(RFC822)")
            if status != "OK":
                continue
            raw_msg = data[0][1]
            msg = em.message_from_bytes(raw_msg)
            from_ = _decode(msg.get("From", ""))
            subject = _decode(msg.get("Subject", ""))
            date = _date(msg)
            body_snip = _body(msg)

            # Build a compact representation for the email
            email_text = (
                f"FROM: {from_}\nSUBJECT: {subject}\nDATE: {date}\n\n{body_snip}"
            )
            # Classify with AI
            classification = _classify(agent, email_text, jobs_ctx, company_name=None)

            results.append({
                "uid": uid_str,
                "from_addr": from_,
                "subject": subject,
                "date": date,
                "body_snippet": body_snip,
                "job_id": classification.get("job_id"),
                "company_name": classification.get("company_match", ""),
                "classification": classification.get("classification", "ignore"),
                "summary": classification.get("summary", ""),
            })
        return results
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def _classify(agent: ChatAgent, email_text: str, jobs_ctx: list, company_name=None) -> dict:
    """Use DeepSeek to classify one email against the job board.

    Returns: {"job_id": int|None, "classification": str, "summary": str}
    """
    if not jobs_ctx:
        return {"job_id": None, "classification": "ignore",
                "summary": "Keine Stellen im Board zum Abgleich."}
    # Pre-filter with high-signal fields: sender + subject FIRST (sender display name
    # and domain almost always contain the company, body often doesn't).
    # e.g. "BMW Group Recruiting <noreply@bmwgroup.com>" → BMW
    email_lower = email_text[:3000].lower()
    header_lower = email_text.split("\n\n", 1)[0][:600].lower()  # FROM/SUBJECT block
    candidate_ids = []
    for j in jobs_ctx:
        company_lower = (j["company"] or "").lower()
        if not company_lower or len(company_lower) <= 2:
            continue
        # Also match short forms: "bmw" matches "bmwgroup.com", "mercedes" in "mercedes-benz"
        if company_lower in header_lower or company_lower in email_lower:
            candidate_ids.append(j)
    if not candidate_ids:
        candidate_ids = jobs_ctx  # fallback: send all, AI filters

    companies_json = json.dumps([
        {"id": j["id"], "company": j["company"], "title": j.get("title", "")}
        for j in candidate_ids[:10]
    ], ensure_ascii=False)

    prompt = (
        "Du klassifizierst eine eingehende E-Mail für eine Bewerbungs-Dashboard.\n"
        "Bestimme: (1) ob sie sich auf eine Bewerbung bezieht, (2) welche Stelle im Board "
        "(company/ID match), (3) was die Nachricht bedeutet.\n\n"
        "Klassifikationen:\n"
        "- interview = Vorstellungsgespräch-Einladung, Terminvorschlag, Gespräch\n"
        "- rejected = Absage, leider nicht berücksichtigt, anderweitig besetzt\n"
        "- offer = Zusage, Vertragsangebot, Einstellung\n"
        "- confirmed = Eingangsbestätigung, Bewerbung erhalten, thank you for applying (NUR bei Bestätigung, NICHT Ablehnung)\n"
        "- note = Rückfrage, Unterlagen-Nachforderung, Zwischenstand, sonstige Bewerbungs-Kommunikation\n"
        "- ignore = Kein Bezug zu einer Bewerbung (Newsletter, privat, Spam, etc.)\n\n"
        "STELLEN-ZUORDNUNG:\n"
        "- Der Absender/der Betreff nennt meist die Firma. Wähle die Stelle, deren Firma "
        "zur Absender-Firma passt.\n"
        "- Bei MEHREREN Stellen derselben Firma: Wähle anhand des Betreffs/Inhalts die "
        "konkrete Stelle (Job-Titel kommt oft im Mailtext vor). Wenn unklar, nimm die\n"
        "zuletzt aktualisierte (höchste ID).\n"
        "- Wenn keine Firma passt: job_id = null.\n\n"
        "=== STELLEN IM BOARD ===\n"
        f"{companies_json}\n\n"
        "=== E-MAIL ===\n"
        f"{email_text[:4000]}\n\n"
        'Antworte ausschließlich mit JSON: '
        '{"job_id": <int oder null>, "classification": "<interview|rejected|offer|note|ignore>", '
        '"summary": "<1 Satz Deutsch: was bedeutet die Mail für die Bewerbung?>"}'
    )
    try:
        return agent._chat(
            [{"role": "system", "content": "Du klassifizierst Bewerbungs-E-Mails. Antworte NUR mit JSON."},
             {"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=300,
        )
    except JobAgentError:
        return {"job_id": None, "classification": "ignore", "summary": "AI-Klassifikation fehlgeschlagen."}
