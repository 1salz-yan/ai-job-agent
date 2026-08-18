# Job-Empfehlung v2 — Architektur-Entwurf (zur Freigabe)

> Status: ENTWURF — zur Prüfung durch die Nutzerin. Nach Freigabe wird
> phasenweise implementiert. Dieses Dokument ersetzt die bisherigen
> punktuellen Fixes (Randomisierung, Gruppierung, Dedup, Datenquellen)
> durch ein geschlossenes System.

---

## 1. Warum der Ist-Zustand nicht funktioniert

Die aktuelle Empfehlung ist eine Aneinanderreihung von Einzel-Patches:

| Problem | Bisherige Reaktion | Ergebnis |
|---|---|---|
| Immer gleiche Jobs | Zufalls-Suchbegriffe | Pool bleibt gleich, nur andere Reihenfolge |
| Zu wenig Vielfalt | Gruppen-Sampling | 5 Gruppen, aber kein Zielbild dahinter |
| Alte Jobs kommen zurück | rec_history (14 Tage) | Symptom bekämpft, keine Ursache |
| Zu wenig Quellen | Arbeitnow ergänzt | 16 Treffer, kaum relevant |
| Gleiche Firma mehrfach | Firmen-Normalisierung | Patch Nr. 5 in einer Woche |

**Grundproblem: Es gibt kein Zielbild.** Die Suche startet bei den
Suchbegriffen, nicht bei der Frage "Was sucht die Nutzerin eigentlich?". Und es gibt
keine Rückkopplung: Aktionen (Bewerbung, Löschen, Ignorieren) fließen nirgends
zurück.

---

## 2. Zielarchitektur: geschlossener Kreislauf (5 Schichten)

```
   ┌─────────────────────────────────────────────────────────┐
   │  L1 ZIELBILD (Job Target Profile)                      │
   │  Eine Quelle der Wahrheit: was sucht die Nutzerin      │
   └──────────────┬──────────────────────────────────────────┘
                  ▼
   ┌─────────────────────────────────────────────────────────┐
   │  L2 ENTDECKUNG (Discovery-Pipeline)                    │
   │  Adzuna · BA-Jobbörse · Arbeitnow · RemoteOK            │
   │  einheitliche Normalisierung, Inkrementell              │
   └──────────────┬──────────────────────────────────────────┘
                  ▼
   ┌─────────────────────────────────────────────────────────┐
   │  L3 BEWERTUNG (Scoring)                                 │
   │  Harte Filter → Regel-Score → AI-Score → Gesamtpunktzahl│
   │  Score-Aufschlüsselung pro Job (transparent)            │
   └──────────────┬──────────────────────────────────────────┘
                  ▼
   ┌─────────────────────────────────────────────────────────┐
   │  L4 AUSWAHL (Selection)                                 │
   │  Diversität: Richtung / Stadt / Firma, Top-N            │
   └──────────────┬──────────────────────────────────────────┘
                  ▼
   ┌─────────────────────────────────────────────────────────┐
   │  L5 RÜCKKOPPLUNG (Feedback-Loop)                        │
   │  Bewerbung · Merken · Löschen · Ignorieren → Statistik  │
   │  → passt L1-Zielbild automatisch an                     │
   └──────────────┬──────────────────────────────────────────┘
                  └──────────────► zurück zu L1
```

---

## 3. L1 — Zielbild (Job Target Profile)

**Neu: Tabelle `target_profile`** — eine strukturierte Definition der
Suchabsicht, ersetzt die verstreuten Settings (search_queries,
prefer_*, exclude_*).

```sql
CREATE TABLE target_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),      -- Singleton
  role_types   TEXT NOT NULL,                  -- JSON: ["praktikum","junior","trainee"]
  directions   TEXT NOT NULL,                  -- JSON: [{"name":"projekt","weight":3}, ...]
  cities       TEXT NOT NULL,                  -- JSON: [{"name":"Berlin","weight":4}, ...]
  companies    TEXT NOT NULL,                  -- JSON: ["Siemens","BMW", ...] (boost)
  exclusions   TEXT NOT NULL,                  -- JSON: ["HR","Sachbearbeitung", ...]
  max_commute  INTEGER DEFAULT 0,              -- km, 0 = egal
  updated_at   TEXT DEFAULT ''
);
```

**Was sich ändert:**
- Die 4 Such-Präferenz-Felder im Dashboard (prefer_companies, prefer_keywords,
  prefer_locations, exclude_keywords) werden durch dieses eine Zielbild ersetzt
  (Migration der bestehenden Werte beim Upgrade).
- Richtungen und Städte haben **Gewichte** (nicht nur ja/nein): "Web3" ist
  wichtiger als "allgemeine Digitalisierung" — das fließt in den Score ein.
- Jede Schicht liest NUR aus diesem Bild. Kein anderes Modul interpretiert
  Suchbegriffe mehr selbst.

---

## 4. L2 — Discovery-Pipeline

**Einheitlicher Adapter pro Quelle** mit gleichem Ausgabe-Schema:

```python
def discover(source: str) -> list[RawJob]:
    # RawJob = {source, external_id, title, company, location, url,
    #           description, salary, published_at}
```

**Quellen:**
| Quelle | Aufwand | Ertrag |
|---|---|---|
| Adzuna (vorhanden) | 0 | hoch, DE-weit |
| BA-Jobbörse (Key fehlt) | API-Key beantragen | hoch, größter DE-Bestand |
| Arbeitnow (neu, vorhanden) | 0 | niedrig, aber andere Firmen |
| RemoteOK (vorhanden) | 0 | Web3/remote |

**Normalisierung zentralisiert** — ein Modul, keine verstreuten Patches:
- `_url_key()` (Adzuna-ID / Pfad ohne Query)
- `_norm_company()` (Suffixe + Mapping)
- `_norm_title()` (Klammern, Satzzeichen)

**Inkrementell statt Voll-Scan:** pro Quelle wird der Stand der letzten
Abfrage gespeichert (`last_seen`). Jobs, die schon einmal gesehen wurden,
werden nicht erneut bewertet — nur neue.

---

## 5. L3 — Bewertung (Scoring)

**Drei Stufen, Gesamtpunkte 0–100, aufgeschlüsselt:**

```
score = hard_pass ? (rule_score + ai_score) : 0
       rule_score  ∈ [0, 60]
       ai_score    ∈ [0, 40]
```

| Stufe | Kriterien | Punkte |
|---|---|---|
| **Harte Filter** | Werkstudent/Senior/Lead, exclude_keywords, falsche Rolle | 0 → aussortiert |
| **Regel-Score** | Rollentyp-Treffer (15) · Richtung×Gewicht (20) · Stadt×Gewicht (15) · Wunschfirma (10) | 0–60 |
| **AI-Score** | ChatAgent.match() gegen Profil (normalisiert auf 0–40) | 0–40 |

**Transparenz:** `jobs.score_breakdown` (JSON) speichert die Zerlegung:
```json
{"rule": {"role": 15, "direction": 20, "city": 10, "company": 0},
 "ai": 28, "total": 73}
```
Im Modal zeigt die UI "Warum 73%?" — aufklappbar mit den Einzelposten.
Das macht Bewertungen prüfbar und debugbar (kein Blackbox-AI-Raten mehr).

---

## 6. L4 — Auswahl (Selection)

Basierend auf dem Score-Aufschluss, nicht auf dem Gesamtscore allein:

1. Sortierung nach Gesamtpunktzahl, aber mit **Diversitäts-Quoten**:
   - max. 3 Jobs pro Richtung (aus L1), max. 2 pro Firma
   - mind. 2 verschiedene Städte in den Top 10
2. **Vergangenheit:** rec_history (14 Tage) bleibt als harte Sperre —
   aber jetzt aus dem Zielbild begründet (Job passt nicht mehr), nicht als
   Zufalls-Bremse.
3. Top-N = 12 (statt 10), damit die Nutzerin mehr Auswahl hat.

---

## 7. L5 — Rückkopplung (Feedback-Loop)  ← das fehlende Stück

**Neu: Tabelle `feedback`:**

```sql
CREATE TABLE feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER REFERENCES jobs(id),
  action TEXT NOT NULL,          -- apply | keep | ignore | delete
  created_at TEXT DEFAULT ''
);
```

**Automatische Erfassung (kein Extra-Klick für die Nutzerin):**
| Aktion in der UI | feedback-Eintrag |
|---|---|
| Status → applied/confirmed/offer | `apply` |
| Job in Merkliste behalten (> 2 Tage) | `keep` |
| Job gelöscht (Papierkorb) | `delete` |
| Job geöffnet und verworfen | `ignore` |

**Wöchentliche Aggregation (`feedback_stats`):**
- Trefferquote pro Richtung: `apply / (apply + ignore)` je Richtung
- Trefferquote pro Stadt, pro Firma
- **Rückwirkung auf L1:** Richtungs-/Stadtgewichte werden aus den echten
  Daten neu berechnet (exponentiell geglättet, alte Gewichte zählen weniger).
  Nach 2–4 Wochen realer Nutzung sagt das System "Du bewirbst dich real auf
  Supply-Chain-Jobs in Heilbronn" — und empfiehlt genau dahin, statt nach
  einem statischen Profil.

**Kalter Start:** Gewichte aus dem initialen Zielbild; Feedback läuft
parallel dazu und übernimmt schrittweise.

---

## 8. Datenmodell — Zusammenfassung der Änderungen

| Tabelle | Aktion |
|---|---|
| `target_profile` | NEU (L1) |
| `feedback` | NEU (L5) |
| `jobs` | + Spalte `score_breakdown` (L3) |
| `settings` | migrate prefer_* → target_profile (einmalig) |
| `rec_history` | bleibt (L4-Sperre) |

---

## 9. Umsetzungsplan (phasenweise)

| Phase | Inhalt | Verifizierbar durch |
|---|---|---|
| **P1** | L1 Zielbild + Migration + UI-Editor | Profile speichern/laden, alte Werte migriert |
| **P2** | L3 Scoring umbauen + score_breakdown + UI "Warum?" | Score-Zerlegung stimmt, Filter wirken |
| **P3** | L5 Feedback-Erfassung + Wochenstatistik + Gewichte-Anpassung | Aktion → feedback-Zeile → Gewicht ändert sich |
| **P4** | L2 inkrementelle Pipeline + BA-Key | Zweiter Lauf erkennt "keine neuen Jobs" korrekt |
| **P5** | L4 Quoten + Top-12 | Simulation: 3 Richtungen, 2 Städte, 2 Firmen eingehalten |

Jede Phase ist eigenständig lauffähig und einzeln testbar (Ad-hoc-Skripte
im Projekt, kein Test-Framework nötig). Rollback = alte Tabelle/Spalte
wiederherstellen, kein Datenverlust.

---

## 10. Was NICHT Teil dieser Lösung ist

- Kein Web-Scraping gegen Glassdoor/LinkedIn (Cloudflare, instabil) —
  diese Quellen bleiben "Text einfügen".
- Keine automatische Bewerbung, keine LLM-Agenten die sich selbst bewerben.
- Keine externe Anbindung an LinkedIn API (kostenpflichtig, Scope-Probleme).

---

*Entwurf v0.1 — zur Freigabe. Kommentare/Änderungswünsche bitte direkt im
Dokument oder im Chat.*
