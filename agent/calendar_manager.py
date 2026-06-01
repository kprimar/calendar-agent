"""
Google Calendar CRUD operations.
"""

import os
from datetime import datetime, timedelta, date
from typing import Optional


TIMEZONE = os.getenv("CALENDAR_TIMEZONE", "America/New_York")


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


def create_event(calendar_service, event_details: dict) -> dict:
    """Insert a new event on the primary calendar. Returns the created event."""
    body = _build_event_body(event_details)
    return calendar_service.events().insert(calendarId="primary", body=body).execute()


def get_event(calendar_service, event_id: str) -> Optional[dict]:
    """Fetch a single event by ID. Returns None if not found or deleted."""
    try:
        return calendar_service.events().get(calendarId="primary", eventId=event_id).execute()
    except Exception:
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

    results = calendar_service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        privateExtendedProperty="created_by=calendar-agent",
        singleEvents=True,
        orderBy="startTime",
        maxResults=25,
    ).execute()

    return results.get("items", [])


def find_all_events(calendar_service, title: str, around_dt=None) -> list:
    """
    Return all calendar events matching title near around_dt.
    Same search window as find_event, but returns every match.
    """
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

    results = calendar_service.events().list(
        calendarId="primary",
        q=title,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=25,
    ).execute()

    title_lower = title.lower()
    return [e for e in results.get("items", []) if title_lower in e.get("summary", "").lower()]


def find_event(calendar_service, title: str, around_dt=None) -> Optional[dict]:
    """
    Search the primary calendar for an event matching title.
    If around_dt is provided, search within ±1 day of that date.
    Otherwise search the next 365 days.
    """
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

    results = calendar_service.events().list(
        calendarId="primary",
        q=title,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=5,
    ).execute()

    items = results.get("items", [])
    if not items:
        return None

    # Return the first match (most likely)
    title_lower = title.lower()
    for item in items:
        if title_lower in item.get("summary", "").lower():
            return item
    return items[0]


def list_all_events(calendar_service, days_back: int = 30, days_forward: int = 365) -> list:
    """Fetch all events on the primary calendar within the given window, handling pagination."""
    now = datetime.utcnow()
    time_min = (now - timedelta(days=days_back)).isoformat() + "Z"
    time_max = (now + timedelta(days=days_forward)).isoformat() + "Z"

    events = []
    page_token = None
    while True:
        resp = calendar_service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
            pageToken=page_token,
        ).execute()
        events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return events


def update_event(calendar_service, event_id: str, event_details: dict) -> dict:
    """Update an existing calendar event with new details. Returns the updated event."""
    body = _build_event_body(event_details)
    return calendar_service.events().update(
        calendarId="primary",
        eventId=event_id,
        body=body,
    ).execute()


def delete_event(calendar_service, event_id: str) -> None:
    """Delete a calendar event by ID."""
    calendar_service.events().delete(
        calendarId="primary",
        eventId=event_id,
    ).execute()
