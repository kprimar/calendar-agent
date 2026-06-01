"""
Classifies emails using Claude and extracts structured event details.

Actions:
  create     - new appointment/event/ticket the user has confirmed
  cancel     - a confirmed event has been cancelled
  reschedule - an event has moved to a new date/time/location
  ignore     - promotional/marketing/unrelated email
"""

import os
import anthropic

_client = None

SYSTEM_PROMPT = """You are an intelligent email parser that maintains a personal Google Calendar.

Analyze each email and classify it into exactly one of these actions:

"create" — The email confirms the user has committed to attending something at a specific date and time. This includes:
  - Doctor/dentist/service appointment confirmations
  - Event tickets the user purchased (concerts, sports, theatre)
  - Meeting or call invites the user accepted
  - Reservation confirmations (restaurant, hotel check-in)
  - Flight or travel itineraries

"cancel" — A previously confirmed event has been explicitly cancelled or refunded.

"reschedule" — A confirmed event has changed its date, time, or location.

"ignore" — The email does NOT represent a personal commitment. Always ignore:
  - Marketing, promotional, or sales emails (even if they mention sale-end dates)
  - Newsletters and digests
  - Password resets, receipts for purchases (unless the purchase IS the ticket/reservation)
  - Social notifications (likes, follows, comments)
  - Automated shipping/tracking updates
  - Any email where the user has NOT committed to being somewhere

When extracting event details:
  - Always express datetimes in ISO 8601 format: "2026-05-25T14:00:00" for timed events or "2026-05-25" for all-day
  - If only a time is given without a year, infer the most plausible future date based on the email date
  - For reschedule: include the old datetime in old_start_datetime
  - Make the event title short and descriptive (e.g. "Dentist Appointment", "Taylor Swift Concert")
  - Location should be a physical address or venue name, not a URL"""


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    return _client


CLASSIFY_TOOL = {
    "name": "classify_email",
    "description": "Classify the email and extract any calendar event details.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "cancel", "reschedule", "ignore"],
                "description": "What calendar action to take based on this email.",
            },
            "reason": {
                "type": "string",
                "description": "One sentence explaining the classification decision.",
            },
            "event": {
                "type": "object",
                "description": "Event details — required for create/cancel/reschedule, omit for ignore.",
                "properties": {
                    "title": {"type": "string", "description": "Short event title."},
                    "start_datetime": {
                        "type": "string",
                        "description": "ISO 8601 start datetime or date (e.g. '2026-05-25T14:00:00' or '2026-05-25').",
                    },
                    "end_datetime": {
                        "type": "string",
                        "description": "ISO 8601 end datetime/date, if known. Omit if unknown.",
                    },
                    "location": {
                        "type": "string",
                        "description": "Venue name or address, if present.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief notes or confirmation number to include on the calendar event.",
                    },
                    "old_start_datetime": {
                        "type": "string",
                        "description": "Previous ISO 8601 datetime — only for reschedule action.",
                    },
                },
                "required": ["title", "start_datetime"],
            },
        },
        "required": ["action", "reason"],
    },
}


BULK_SYSTEM_PROMPT = """You are a calendar assistant. The user has sent an email to their calendar agent to create multiple events.

Extract every event mentioned in the email body. For each event:
- Make the title short and descriptive
- Express datetimes in ISO 8601 format: "2026-06-07T18:00:00" for timed events or "2026-06-07" for all-day
- If a year is not given, infer the most plausible upcoming date based on the email's date
- Include location and description only if explicitly mentioned"""

BULK_EXTRACT_TOOL = {
    "name": "extract_events",
    "description": "Extract all calendar events from the email.",
    "input_schema": {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "description": "List of events to create.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Short event title."},
                        "start_datetime": {"type": "string", "description": "ISO 8601 start datetime or date."},
                        "end_datetime": {"type": "string", "description": "ISO 8601 end datetime, if known."},
                        "location": {"type": "string", "description": "Venue or address, if mentioned."},
                        "description": {"type": "string", "description": "Any extra notes."},
                    },
                    "required": ["title", "start_datetime"],
                },
            }
        },
        "required": ["events"],
    },
}


def extract_bulk_events(email: dict) -> list[dict]:
    """
    Parse a 'Calendar Agent:' command email and return a list of event dicts.
    Each dict has the same shape as the 'event' field from classify_email.
    """
    client = get_client()

    MAX_BODY = 6000
    body = email.get("body", "") or email.get("snippet", "")
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + "\n\n[... email truncated ...]"

    user_content = (
        f"From: {email['sender']}\n"
        f"Date: {email['date']}\n"
        f"Subject: {email['subject']}\n\n"
        f"{body}"
    )

    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=2048,
        system=BULK_SYSTEM_PROMPT,
        tools=[BULK_EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_events"},
        messages=[{"role": "user", "content": user_content}],
    )

    tool_block = next(b for b in response.content if b.type == "tool_use")
    return tool_block.input.get("events", [])


def classify_email(email: dict) -> dict:
    """
    Send one email to Claude for classification.

    Returns a dict with keys: action, reason, event (or None).
    """
    client = get_client()

    # Build the user message with key email fields
    MAX_BODY = 6000
    body = email.get("body", "") or email.get("snippet", "")
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + "\n\n[... email truncated ...]"

    user_content = (
        f"From: {email['sender']}\n"
        f"Date: {email['date']}\n"
        f"Subject: {email['subject']}\n\n"
        f"{body}"
    )

    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},  # stable across all emails in a run
            }
        ],
        tools=[CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "classify_email"},
        messages=[{"role": "user", "content": user_content}],
    )

    tool_block = next(b for b in response.content if b.type == "tool_use")
    result = tool_block.input

    return {
        "action": result["action"],
        "reason": result.get("reason", ""),
        "event": result.get("event"),
    }
