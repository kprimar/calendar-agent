"""
Google Calendar CRUD operations.
"""

import os
from datetime import datetime, timedelta, date
from typing import Optional


TIMEZONE = os.getenv("CALENDAR_TIMEZONE", "America/New_York")

# New events always go to this calendar (Personal)
CREATE_CALENDAR_ID = os.getenv("CREATE_CALENDAR_ID", "primary")

# All calendars the agent can read, update, and delete on
_managed_raw = os.getenv("MANAGED_CALENDAR_IDS", CREATE_CALENDAR_ID)
MANAGED_CALENDAR_IDS = list(dict.fromkeys(
    c.strip() for c in _managed_raw.split(",") if c.strip()
))


def _parse_dt(dt_str: str):
    """Parse ISO 8601 string into datetime or date. Returns None on failure."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return datetime.strptime(dt_str, "%Y-%m-%d").date()
    except ValueError:
        return None


def _is_date_only(obj) -> bool:
    return isinstance(obj, date) and not isinstance(obj, datetime)


def _build_event_body(event_details: dict) -> dict:
    """Convert extracted event details into a Google Calendar API event body."""
    title = event_details.get("title", "Untitled Event")
    start = _parse_dt(event_details.get("start_datetime", ""))
    end = _parse_dt(event_details.get("end_datetime", ""))

    if start is None:
        raise ValueError(f"Cannot parse start_datetime: {event_details.get('start_datetime')}")

    if _is_date_only(start):
        end_val = end if end else start
        if _is_date_only(end_val):
            end_date = end_val.isoformat()
        else:
            end_date = start.isoformat()
        body = {
            "summary": title,
            "start": {"date": start.isoformat()},
            "end": {"date": end_date},
        }
    else:
        if end is None or _is_date_only(end):
            end_dt = start + timedelta(hours=1)
        else:
            end_dt = end
        body = {
            "summary": title,
            "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE},
        }

    if event_details.get("location"):
        body["location"] = event_details["location"]
    if event_details.get("description"):
        body["description"] = event_details["description"]

    body["extendedProperties"] = {"private": {"created_by": "calendar-agent"}}

    return body


def _find_calendar_for_event(calendar_service, event_id: str) -> Optional[str]:
    """Return the calendar ID that owns this event, or None if not found."""
    for cal_id in MANAGED_CALENDAR_IDS:
        try:
            calendar_service.events().get(calendarId=cal_id, eventId=event_id).execute()
            return cal_id
        except Exception:
            continue
    return None


def create_event(calendar_service, event_details: dict) -> dict:
    """Insert a new event on the Personal calendar. Returns the created event."""
    body = _build_event_body(event_details)
    return calendar_service.events().insert(calendarId=CREATE_CALENDAR_ID, body=body).execute()


def get_event(calendar_service, event_id: str) -> Optional[dict]:
    """Fetch a single event by ID across all managed calendars. Returns None if not found."""
    for cal_id in MANAGED_CALENDAR_IDS:
        try:
            return calendar_service.events().get(calendarId=cal_id, eventId=event_id).execute()
        except Exception:
            continue
    return None


def find_agent_events_at_time(calendar_service, around_dt) -> list:
    """Return all agent-created events whose start time falls within ±1 hour of around_dt."""
    if around_dt is None:
        return []

    if _is_date_only(around_dt):
        base = datetime(around_dt.year, around_dt.month, around_dt.day)
    else:
        base = around_dt.replace(tzinfo=None) if around_dt.tzinfo else around_dt

    time_min = (base - timedelta(hours=1)).isoformat() + "Z"
    time_max = (base + timedelta(hours=1)).isoformat() + "Z"

    results = []
    seen = set()
    for cal_id in MANAGED_CALENDAR_IDS:
        resp = calendar_service.events().list(
            calendarId=cal_id,
            timeMin=time_min,
            timeMax=time_max,
            privateExtendedProperty="created_by=calendar-agent",
            singleEvents=True,
            orderBy="startTime",
            maxResults=25,
        ).execute()
        for e in resp.get("items", []):
            if e["id"] not in seen:
                seen.add(e["id"])
                results.append(e)
    return results


def find_all_events(calendar_service, title: str, around_dt=None) -> list:
    """Return all events matching title across all managed calendars near around_dt."""
    now = datetime.utcnow()

    if around_dt is not None:
        if _is_date_only(around_dt):
            base = datetime(around_dt.year, around_dt.month, around_dt.day)
        else:
            base = around_dt.replace(tzinfo=None) if around_dt.tzinfo else around_dt
        time_min = (base - timedelta(days=1)).isoformat() + "Z"
        time_max = (base + timedelta(days=1)).isoformat() + "Z"
    else:
        time_min = now.isoformat() + "Z"
        time_max = (now + timedelta(days=365)).isoformat() + "Z"

    title_lower = title.lower()
    results = []
    seen = set()
    for cal_id in MANAGED_CALENDAR_IDS:
        resp = calendar_service.events().list(
            calendarId=cal_id,
            q=title,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=25,
        ).execute()
        for e in resp.get("items", []):
            if e["id"] not in seen and title_lower in e.get("summary", "").lower():
                seen.add(e["id"])
                results.append(e)
    return results


def find_event(calendar_service, title: str, around_dt=None) -> Optional[dict]:
    """Search all managed calendars for an event matching title. Returns first match."""
    matches = find_all_events(calendar_service, title, around_dt)
    return matches[0] if matches else None


def list_all_events(calendar_service, days_back: int = 30, days_forward: int = 365) -> list:
    """Fetch all events across all managed calendars within the given window."""
    now = datetime.utcnow()
    time_min = (now - timedelta(days=days_back)).isoformat() + "Z"
    time_max = (now + timedelta(days=days_forward)).isoformat() + "Z"

    events = []
    seen = set()
    for cal_id in MANAGED_CALENDAR_IDS:
        page_token = None
        while True:
            resp = calendar_service.events().list(
                calendarId=cal_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=250,
                pageToken=page_token,
            ).execute()
            for e in resp.get("items", []):
                if e["id"] not in seen:
                    seen.add(e["id"])
                    events.append(e)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    return events


def update_event(calendar_service, event_id: str, event_details: dict) -> dict:
    """Update an existing event on whichever managed calendar owns it."""
    body = _build_event_body(event_details)
    cal_id = _find_calendar_for_event(calendar_service, event_id) or CREATE_CALENDAR_ID
    return calendar_service.events().update(
        calendarId=cal_id,
        eventId=event_id,
        body=body,
    ).execute()


def delete_event(calendar_service, event_id: str) -> None:
    """Delete an event from whichever managed calendar owns it."""
    cal_id = _find_calendar_for_event(calendar_service, event_id) or CREATE_CALENDAR_ID
    calendar_service.events().delete(
        calendarId=cal_id,
        eventId=event_id,
    ).execute()
