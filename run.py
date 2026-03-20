"""
Sprocket Sports → Google Calendar
Scans Gmail for events from support@sprocketsports.com and adds them to Google Calendar.
Skips duplicates by checking existing calendar events first.
"""

import os
import json
import re
from datetime import datetime, timedelta, timezone
from anthropic import Anthropic
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Config ────────────────────────────────────────────────────────────────────
SENDER_EMAIL    = "support@sprocketsports.com"
SUBJECT_KEYWORD = "New Event"
DAYS_BACK       = 30
ATTENDEE_EMAIL  = "katie.mckinley1@gmail.com"
CALENDAR_ID     = "primary"
TIMEZONE        = "America/New_York"

# ── Auth ──────────────────────────────────────────────────────────────────────

def get_google_credentials():
    """Build Google credentials from environment secrets."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON secret is not set.")
    token_data = json.loads(creds_json)
    creds = Credentials(
        token=None,
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar"
        ]
    )
    creds.refresh(Request())
    return creds


# ── Gmail ─────────────────────────────────────────────────────────────────────

def fetch_emails(service):
    """Search Gmail for matching emails from the last DAYS_BACK days."""
    after_date = (datetime.now() - timedelta(days=DAYS_BACK)).strftime("%Y/%m/%d")
    query = f"from:{SENDER_EMAIL} subject:{SUBJECT_KEYWORD} after:{after_date}"
    print(f"Gmail query: {query}")

    result = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
    messages = result.get("messages", [])
    print(f"Found {len(messages)} matching email(s).")

    emails = []
    for msg in messages:
        full = service.users().messages().get(
            userId="me", id=msg["id"], format="full"
        ).execute()

        subject = ""
        body = ""
        date_str = ""

        headers = full.get("payload", {}).get("headers", [])
        for h in headers:
            if h["name"] == "Subject":
                subject = h["value"]
            if h["name"] == "Date":
                date_str = h["value"]

        payload = full.get("payload", {})
        body = extract_body(payload)

        emails.append({
            "subject": subject,
            "date": date_str,
            "body": body[:4000]
        })

    return emails


def extract_body(payload):
    """Recursively extract plain text body from Gmail payload."""
    import base64
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data", "")

    if mime == "text/plain" and data:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")

    for part in payload.get("parts", []):
        result = extract_body(part)
        if result:
            return result

    return ""


# ── Claude: extract events ────────────────────────────────────────────────────

def extract_events(emails):
    """Use Claude to extract structured event details from email content."""
    if not emails:
        return []

    client = Anthropic()
    email_text = "\n\n---\n\n".join(
        f"Subject: {e['subject']}\nDate: {e['date']}\n\n{e['body']}"
        for e in emails
    )

    prompt = f"""Extract all sports/activity event details from these emails.
For each event found, return a JSON object with:
- title: descriptive event name (e.g. "Soccer Game vs Eagles")
- date: YYYY-MM-DD format
- startTime: HH:MM in 24h format, or null
- endTime: HH:MM in 24h format, or null
- location: venue name and/or address, or null
- description: notes about opponent, uniform color, etc., or null
- sourceSubject: the email subject line this came from

Return ONLY a valid JSON array. If no events found, return [].

Emails:
{email_text}"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system="You extract structured event data from email text. Return only valid JSON arrays with no markdown or explanation.",
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"```json|```", "", raw).strip()

    try:
        events = json.loads(raw)
        if not isinstance(events, list):
            events = []
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", raw)
        events = json.loads(match.group()) if match else []

    print(f"Extracted {len(events)} event(s) from emails.")
    return events


# ── Calendar: dedup + create ──────────────────────────────────────────────────

def fetch_existing_events(service):
    """Fetch calendar events for the next 12 months to use for dedup."""
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=365)).isoformat()

    result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=500
    ).execute()

    existing = []
    for e in result.get("items", []):
        start = e.get("start", {})
        date = start.get("date") or start.get("dateTime", "")[:10]
        existing.append({
            "title": e.get("summary", ""),
            "date": date
        })

    print(f"Found {len(existing)} existing calendar event(s) to check against.")
    return existing


def normalize(text):
    """Normalize a string for fuzzy dedup comparison."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def is_duplicate(event, existing_events):
    """Return True if a matching event already exists on the calendar."""
    event_key = f"{event.get('date', '')}_{normalize(event.get('title', ''))}"
    for ex in existing_events:
        ex_key = f"{ex['date']}_{normalize(ex['title'])}"
        if event_key == ex_key:
            return True
    return False


def create_calendar_event(service, event):
    """Create a single Google Calendar event."""
    title = event.get("title", "Untitled Event")
    date = event.get("date")
    start_time = event.get("startTime")
    end_time = event.get("endTime")
    location = event.get("location") or ""
    description = event.get("description") or ""

    if start_time and date:
        start = {"dateTime": f"{date}T{start_time}:00", "timeZone": TIMEZONE}
        if end_time:
            end = {"dateTime": f"{date}T{end_time}:00", "timeZone": TIMEZONE}
        else:
            # Default: 1 hour duration
            h, m = map(int, start_time.split(":"))
            end_h = str(h + 1).zfill(2)
            end = {"dateTime": f"{date}T{end_h}:{str(m).zfill(2)}:00", "timeZone": TIMEZONE}
    else:
        start = {"date": date}
        end = {"date": date}

    body = {
        "summary": title,
        "location": location,
        "description": f"{description}\n\nAdded automatically from Sprocket Sports email.".strip(),
        "start": start,
        "end": end,
        "attendees": [{"email": ATTENDEE_EMAIL}],
        "reminders": {"useDefault": False, "overrides": []}
    }

    created = service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
    print(f"  ✓ Created: {title} on {date}")
    return created


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("Sprocket Sports → Calendar")
    print(f"Run time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    # Auth
    creds = get_google_credentials()
    gmail_service = build("gmail", "v1", credentials=creds)
    calendar_service = build("calendar", "v3", credentials=creds)

    # Step 1: Fetch emails
    emails = fetch_emails(gmail_service)
    if not emails:
        print("No matching emails found. Exiting.")
        return

    # Step 2: Extract events via Claude
    events = extract_events(emails)
    if not events:
        print("No events extracted from emails. Exiting.")
        return

    # Step 3: Fetch existing calendar events for dedup
    existing = fetch_existing_events(calendar_service)

    # Step 4: Add new events, skip duplicates
    added = 0
    skipped = 0
    for event in events:
        if is_duplicate(event, existing):
            print(f"  ↷ Skipped (duplicate): {event.get('title')} on {event.get('date')}")
            skipped += 1
        else:
            try:
                create_calendar_event(calendar_service, event)
                added += 1
            except Exception as e:
                print(f"  ✗ Failed to create '{event.get('title')}': {e}")

    print("=" * 50)
    print(f"Done. Added: {added}  |  Skipped (duplicates): {skipped}")
    print("=" * 50)


if __name__ == "__main__":
    main()
