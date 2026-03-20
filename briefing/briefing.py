#!/usr/bin/env python3
"""
Jarvis - Briefing Diário 06:15
Dr. Anderson De Freitas — Rio do Sul, SC
"""

import os
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR      = Path(__file__).parent
PENDING_FILE  = BASE_DIR / "pending.json"
TOKEN_FILE    = BASE_DIR / "token.json"
CREDENTIALS   = BASE_DIR / "credentials.json"
TELEGRAM_CHAT = "127842708"
TZ            = timezone(timedelta(hours=-3))

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

EMAIL_CATEGORIES = {
    "HRAVA":   ["escala", "plantão", "resid", "cirurgia", "anestesia", "hrava",
                "comissão", "protocolo", "pop", "corpo clínico"],
    "SEAMEP":  ["seamep", "direção técnica", "relatório", "alvará", "crm", "fiscalização"],
    "UNIDAVI": ["unidavi", "aula", "aluno", "banca", "tcc", "disciplina",
                "semestre", "notas", "coordenação"],
    "URGENTE": ["urgente", "importante", "prazo", "vence hoje", "até amanhã"],
}

DIAS_PT = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]


# ─── GOOGLE AUTH ───────────────────────────────────────────────────────────────
def get_service(api, version):
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build(api, version, credentials=creds)


# ─── CALENDAR ──────────────────────────────────────────────────────────────────
def get_events(cal, date):
    start = datetime(date.year, date.month, date.day, 0, 0, 0, tzinfo=TZ)
    end   = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=TZ)
    result = cal.events().list(
        calendarId="primary",
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime"
    ).execute()
    events = []
    for e in result.get("items", []):
        title   = e.get("summary", "(sem título)")
        loc     = e.get("location", "")
        start_r = e["start"].get("dateTime", e["start"].get("date", ""))
        t = datetime.fromisoformat(start_r).strftime("%H:%M") if "T" in start_r else "dia todo"
        line = f"{t} — {title}"
        if loc:
            line += f" ({loc})"
        events.append(line)
    return events


# ─── GMAIL ─────────────────────────────────────────────────────────────────────
def get_emails(gmail):
    since = (datetime.now(TZ) - timedelta(hours=20)).strftime("%Y/%m/%d")
    result = gmail.users().messages().list(
        userId="me", q=f"after:{since} is:unread", maxResults=60
    ).execute()

    categorized = {k: [] for k in EMAIL_CATEGORIES}

    for m in result.get("messages", []):
        msg = gmail.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["Subject", "From"]
        ).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        subject = headers.get("Subject", "(sem assunto)")
        subj_l  = subject.lower()

        matched = False
        for cat, kws in EMAIL_CATEGORIES.items():
            if any(kw in subj_l for kw in kws):
                categorized[cat].append(subject[:70])
                matched = True
                break
        # Urgente pode capturar mesmo se já categorizou em outro grupo
        if not matched:
            pass  # não duplicar

    return categorized


# ─── PENDÊNCIAS ────────────────────────────────────────────────────────────────
def load_pending():
    if PENDING_FILE.exists():
        data = json.loads(PENDING_FILE.read_text())
        return [p for p in data if not p.get("resolved")]
    return []

def save_pending(items):
    PENDING_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2))

def add_pending(desc, institution=None):
    data = json.loads(PENDING_FILE.read_text()) if PENDING_FILE.exists() else []
    data.append({
        "id": len(data) + 1,
        "desc": desc,
        "institution": institution,
        "date": datetime.now(TZ).strftime("%d/%m/%Y"),
        "resolved": False
    })
    save_pending(data)

def resolve_pending(keyword):
    if not PENDING_FILE.exists():
        return False
    data = json.loads(PENDING_FILE.read_text())
    found = False
    for p in data:
        if keyword.lower() in p["desc"].lower() and not p["resolved"]:
            p["resolved"] = True
            found = True
            break
    save_pending(data)
    return found


# ─── TELEGRAM ──────────────────────────────────────────────────────────────────
def send_telegram(message):
    subprocess.run(
        ["/home/anderson/.npm-global/bin/openclaw", "message", "send",
         "--channel", "telegram",
         "--target", TELEGRAM_CHAT,
         "--message", message],
        check=True, capture_output=True
    )


# ─── MONTAR BRIEFING ───────────────────────────────────────────────────────────
def build_briefing():
    today    = datetime.now(TZ).date()
    tomorrow = today + timedelta(days=1)
    weekday  = DIAS_PT[today.weekday()]
    parts    = []

    parts.append(f"🦞 BOM DIA, DR. ANDERSON — {today.strftime('%d/%m/%Y')} {weekday.upper()}")
    parts.append("")

    # AGENDA HOJE
    parts.append("📅 HOJE")
    try:
        cal = get_service("calendar", "v3")
        events_today = get_events(cal, today)
        if events_today:
            parts.extend(events_today)
        else:
            parts.append("Sem eventos agendados.")
        parts.append("")

        # AMANHÃ
        parts.append(f"📅 AMANHÃ ({tomorrow.strftime('%d/%m')} {DIAS_PT[tomorrow.weekday()]})")
        events_tomorrow = get_events(cal, tomorrow)
        if events_tomorrow:
            parts.extend(events_tomorrow[:4])
            if len(events_tomorrow) > 4:
                parts.append(f"...e mais {len(events_tomorrow)-4} evento(s).")
        else:
            parts.append("Agenda livre.")
    except Exception as e:
        parts.append(f"Google Calendar não encontrado: {e}")
        parts.append("")
        parts.append("📅 AMANHÃ")
        parts.append("Google Calendar não encontrado.")

    parts.append("")

    # E-MAILS
    parts.append("📧 E-MAILS RECENTES")
    try:
        gmail  = get_service("gmail", "v1")
        emails = get_emails(gmail)
        has_any = False
        for cat, items in emails.items():
            if items:
                has_any = True
                parts.append(f"{cat} ({len(items)})")
                for subj in items[:4]:
                    parts.append(f"  • {subj}")
                if len(items) > 4:
                    parts.append(f"  ...e mais {len(items)-4}.")
        if not has_any:
            parts.append("Nenhum e-mail novo.")
    except Exception as e:
        parts.append(f"Gmail não encontrado: {e}")

    parts.append("")

    # PENDÊNCIAS
    pending = load_pending()
    if pending:
        parts.append("⚠️ PENDÊNCIAS EM ABERTO")
        for i, p in enumerate(pending, 1):
            inst = f" [{p['institution']}]" if p.get("institution") else ""
            parts.append(f"{i}. {p['desc']}{inst} (desde {p['date']})")
        parts.append("")

    parts.append("Bom dia. 🩺")

    return "\n".join(parts)


# ─── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    import sys
    if len(sys.argv) > 1:
        cmd = " ".join(sys.argv[1:]).strip()

        # Adicionar pendência
        if cmd.lower().startswith("pendência:") or cmd.lower().startswith("lembra:"):
            desc = cmd.split(":", 1)[1].strip()
            add_pending(desc)
            send_telegram(f"Pendência registrada: {desc}")
            return

        # Resolver pendência
        if cmd.lower().startswith("resolvido:") or cmd.lower().startswith("ok:"):
            keyword = cmd.split(":", 1)[1].strip()
            found = resolve_pending(keyword)
            msg = f"Pendência resolvida: {keyword}" if found else f"Nenhuma pendência encontrada com: {keyword}"
            send_telegram(msg)
            return

    briefing = build_briefing()
    send_telegram(briefing)
    print("Briefing enviado.")


if __name__ == "__main__":
    main()
