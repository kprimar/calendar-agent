"""
Fetches unread emails from the Gmail inbox, excluding Social and Promotions tabs.
"""

import base64
from typing import Optional


def fetch_inbox_emails(gmail_service, max_results: int = 50) -> list[dict]:
    """
    Return a list of email dicts from the inbox, excluding Social and Promotions.
    Each dict has: id, subject, sender, date, body, snippet.
    """
    # Gmail query: inbox only, skip social and promotions categories
    query = "in:inbox -category:social -category:promotions"

    results = gmail_service.users().messages().list(
        userId="me",
        q=query,
        maxResults=max_results,
    ).execute()

    messages = results.get("messages", [])
    emails = []

    for msg_ref in messages:
        msg = gmail_service.users().messages().get(
            userId="me",
            id=msg_ref["id"],
            format="full",
        ).execute()

        email = _parse_message(msg)
        if email:
            emails.append(email)

    return emails


def _parse_message(msg: dict) -> Optional[dict]:
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}

    subject = headers.get("subject", "(no subject)")
    sender = headers.get("from", "")
    date = headers.get("date", "")
    snippet = msg.get("snippet", "")

    body = _extract_body(msg["payload"])

    return {
        "id": msg["id"],
        "subject": subject,
        "sender": sender,
        "date": date,
        "snippet": snippet,
        "body": body,
    }


def _extract_body(payload: dict) -> str:
    """Recursively extract plain-text body from a MIME payload, preferring text/plain."""
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    if mime_type.startswith("multipart/"):
        # For multipart/alternative, prefer text/plain; otherwise take the first non-empty part
        parts = payload.get("parts", [])
        plain = next((p for p in parts if p.get("mimeType") == "text/plain"), None)
        if plain:
            return _extract_body(plain)
        for part in parts:
            text = _extract_body(part)
            if text:
                return text

    return ""
