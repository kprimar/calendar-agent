"""
Main orchestration loop for the calendar agent.

Flow:
  1. Load processed email IDs and stored event records
  2. Reconcile stored event records against the live calendar (recreate/fix missing or outdated events)
  3. Fetch inbox emails (excluding Social / Promotions)
  4. Classify each new email with Claude
  5. Create / update / delete calendar events as needed
  6. Send a summary email of all changes
  7. Save processed IDs and updated event records
"""

import json
import os
from datetime import datetime, date
from pathlib import Path

from colorama import Fore, Style, init

from auth.google_auth import get_gmail_service, get_calendar_service
from agent.email_fetcher import fetch_inbox_emails
from agent.email_classifier import classify_email, extract_bulk_events
from agent import calendar_manager as cal
from agent.notifier import send_summary

init(autoreset=True)

PROCESSED_FILE = Path(__file__).parent.parent / "data" / "processed.json"


def _load_processed() -> tuple[set, dict]:
    if PROCESSED_FILE.exists():
        data = json.loads(PROCESSED_FILE.read_text())
        return set(data.get("ids", [])), data.get("events", {})
    return set(), {}


def _save_processed(ids: set, events: dict) -> None:
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_FILE.write_text(json.dumps({"ids": list(ids), "events": events}, indent=2))


def _format_datetime(event_details: dict) -> str:
    return event_details.get("start_datetime") or "unknown date"


def _datetimes_match(stored: str, calendar_str: str) -> bool:
    """True if two ISO datetime strings represent the same date/time, ignoring timezone."""
    a = cal._parse_dt(stored)
    b = cal._parse_dt(calendar_str)
    if a is None or b is None:
        return stored[:10] == calendar_str[:10]
    if cal._is_date_only(a) or cal._is_date_only(b):
        a_date = a if cal._is_date_only(a) else a.date()
        b_date = b if cal._is_date_only(b) else b.date()
        return a_date == b_date
    return a.replace(tzinfo=None) == b.replace(tzinfo=None)


def _reconcile_events(calendar_service, event_records: dict) -> list[dict]:
    """
    For every active stored event record, verify it exists on the calendar with
    accurate details. Recreates missing events and updates stale ones.
    Mutates event_records in-place to reflect new calendar event IDs.
    """
    changes = []
    active = {eid: rec for eid, rec in event_records.items() if rec.get("status") == "active"}

    if not active:
        return changes

    print(f"Reconciling {len(active)} tracked event(s) against calendar...")

    for email_id, record in active.items():
        title = record.get("title", "Untitled")
        cal_event_id = record.get("calendar_event_id")

        existing = cal.get_event(calendar_service, cal_event_id) if cal_event_id else None

        if existing is None:
            print(f"  {Fore.YELLOW}Missing: {title} — recreating...{Style.RESET_ALL}")
            try:
                created = cal.create_event(calendar_service, record)
                record["calendar_event_id"] = created["id"]
                print(f"  {Fore.GREEN}Recreated: {created.get('htmlLink', '')}{Style.RESET_ALL}")
                changes.append({
                    "action": "recreate",
                    "email_subject": f"(restored from prior email)",
                    "email_sender": "",
                    "event_title": title,
                    "event_datetime": record.get("start_datetime", ""),
                    "detail": "Event was missing from calendar — recreated",
                })
            except Exception as exc:
                print(f"  {Fore.YELLOW}Could not recreate {title}: {exc}{Style.RESET_ALL}")
            continue

        # Event exists — check title, start datetime, and location for accuracy
        needs_update = False
        issues = []

        cal_title = existing.get("summary", "")
        if title.lower() != cal_title.lower():
            needs_update = True
            issues.append(f"title: expected \"{title}\", got \"{cal_title}\"")

        cal_start = existing.get("start", {})
        cal_start_str = cal_start.get("dateTime") or cal_start.get("date", "")
        stored_start = record.get("start_datetime", "")
        if stored_start and not _datetimes_match(stored_start, cal_start_str):
            needs_update = True
            issues.append(f"start: expected {stored_start}, got {cal_start_str}")

        stored_location = record.get("location") or ""
        cal_location = existing.get("location") or ""
        if stored_location and stored_location.lower() != cal_location.lower():
            needs_update = True
            issues.append(f"location: expected \"{stored_location}\", got \"{cal_location}\"")

        if needs_update:
            print(f"  {Fore.CYAN}Outdated: {title} ({'; '.join(issues)}) — updating...{Style.RESET_ALL}")
            try:
                updated = cal.update_event(calendar_service, existing["id"], record)
                print(f"  {Fore.CYAN}Updated: {updated.get('htmlLink', '')}{Style.RESET_ALL}")
                changes.append({
                    "action": "update",
                    "email_subject": f"(corrected from prior email)",
                    "email_sender": "",
                    "event_title": title,
                    "event_datetime": record.get("start_datetime", ""),
                    "detail": f"Event details were inaccurate — fixed ({'; '.join(issues)})",
                })
            except Exception as exc:
                print(f"  {Fore.YELLOW}Could not update {title}: {exc}{Style.RESET_ALL}")

    return changes


def run(max_emails: int = 50) -> None:
    print(f"{Fore.CYAN}=== Calendar Agent Starting ==={Style.RESET_ALL}\n")

    processed_ids, event_records = _load_processed()
    gmail = get_gmail_service()
    calendar = get_calendar_service()

    # Step 1: reconcile previously tracked events against the live calendar
    changes: list[dict] = _reconcile_events(calendar, event_records)
    if changes:
        print()

    # Step 2: process new emails
    print(f"Fetching up to {max_emails} inbox emails...")
    emails = fetch_inbox_emails(gmail, max_results=max_emails)
    new_emails = [e for e in emails if e["id"] not in processed_ids]
    print(f"Found {len(emails)} emails, {len(new_emails)} new.\n")

    ignored_count = 0

    for email in new_emails:
        subject = email["subject"]
        print(f"  Classifying: {subject[:70]}")

        if subject.startswith("Calendar Agent:"):
            bulk_changes = _handle_bulk_email(calendar, email, event_records)
            changes.extend(bulk_changes)
            processed_ids.add(email["id"])
            continue

        try:
            result = classify_email(email)
        except Exception as exc:
            print(f"    {Fore.YELLOW}Classification error: {exc}{Style.RESET_ALL}")
            processed_ids.add(email["id"])
            continue

        action = result["action"]
        reason = result["reason"]
        event = result.get("event")

        if action == "ignore":
            print(f"    {Fore.WHITE}→ Ignored: {reason}{Style.RESET_ALL}")
            ignored_count += 1
            processed_ids.add(email["id"])
            continue

        print(f"    {Fore.CYAN}→ {action.upper()}: {event.get('title', '?')} — {reason}{Style.RESET_ALL}")

        try:
            change = _apply_action(calendar, action, event, email)
            if change:
                changes.append(change)
                if action == "create":
                    event_records[email["id"]] = {
                        "title": event.get("title", "Untitled"),
                        "start_datetime": event.get("start_datetime", ""),
                        "end_datetime": event.get("end_datetime") or "",
                        "location": event.get("location") or "",
                        "description": event.get("description") or "",
                        "calendar_event_id": change["calendar_event_id"],
                        "status": "active",
                    }
                elif action == "cancel":
                    # Mark the matching create record as cancelled so reconciliation won't restore it
                    title = event.get("title", "").lower()
                    event_date = (event.get("old_start_datetime") or event.get("start_datetime", ""))[:10]
                    for rec in event_records.values():
                        if (rec.get("status") == "active"
                                and rec.get("title", "").lower() == title
                                and rec.get("start_datetime", "")[:10] == event_date):
                            rec["status"] = "cancelled"
                            break
        except Exception as exc:
            print(f"    {Fore.YELLOW}Calendar error: {exc}{Style.RESET_ALL}")

        processed_ids.add(email["id"])

    _save_processed(processed_ids, event_records)

    # Send summary email
    if changes:
        print(f"\n{Fore.GREEN}Made {len(changes)} calendar change(s). Sending summary email...{Style.RESET_ALL}")
        try:
            send_summary(gmail, changes, ignored_count)
            print(f"{Fore.GREEN}Summary email sent.{Style.RESET_ALL}")
        except Exception as exc:
            print(f"{Fore.YELLOW}Could not send summary email: {exc}{Style.RESET_ALL}")
    else:
        print(f"\n{Fore.WHITE}No calendar changes made. Summary email skipped.{Style.RESET_ALL}")

    print(f"\n{Fore.CYAN}=== Done ==={Style.RESET_ALL}")


def _event_date_key(event: dict) -> str:
    """Return a YYYY-MM-DD string for grouping — works for both dateTime and date events."""
    start = event.get("start", {})
    dt_str = start.get("dateTime") or start.get("date", "")
    return dt_str[:10]  # first 10 chars is always YYYY-MM-DD


def run_dedup() -> None:
    print(f"{Fore.CYAN}=== Calendar Deduplication ==={Style.RESET_ALL}\n")

    calendar = get_calendar_service()

    print("Fetching calendar events (past 30 days → next 365 days)...")
    all_events = cal.list_all_events(calendar)
    events = [
        e for e in all_events
        if e.get("extendedProperties", {}).get("private", {}).get("created_by") == "calendar-agent"
    ]
    print(f"Found {len(all_events)} total event(s), {len(events)} created by this agent.\n")

    # Group by (normalised title, date)
    groups: dict[tuple, list] = {}
    for event in events:
        title = event.get("summary", "").strip().lower()
        key = (title, _event_date_key(event))
        groups.setdefault(key, []).append(event)

    dupes_found = 0
    deleted_count = 0

    for (title, date_key), group in groups.items():
        if len(group) < 2:
            continue
        dupes_found += 1
        keep = group[0]
        to_delete = group[1:]
        print(f"  Duplicate: \"{group[0].get('summary')}\" on {date_key} — {len(group)} copies")
        print(f"    Keeping : {keep.get('htmlLink', keep['id'])}")
        for dupe in to_delete:
            try:
                cal.delete_event(calendar, dupe["id"])
                print(f"    {Fore.RED}Deleted : {dupe.get('htmlLink', dupe['id'])}{Style.RESET_ALL}")
                deleted_count += 1
            except Exception as exc:
                print(f"    {Fore.YELLOW}Could not delete {dupe['id']}: {exc}{Style.RESET_ALL}")

    if dupes_found == 0:
        print(f"{Fore.GREEN}No duplicates found.{Style.RESET_ALL}")
    else:
        print(f"\n{Fore.GREEN}Done — removed {deleted_count} duplicate(s) across {dupes_found} event(s).{Style.RESET_ALL}")

    print(f"\n{Fore.CYAN}=== Done ==={Style.RESET_ALL}")


def _handle_bulk_email(calendar_service, email: dict, event_records: dict) -> list[dict]:
    """
    Process a 'Calendar Agent:' command email — extract every event it lists
    and create each one, applying the same duplicate detection as normal emails.
    Returns a list of change dicts for the summary email.
    """
    print(f"  {Fore.MAGENTA}→ BULK: parsing events from command email...{Style.RESET_ALL}")

    try:
        events = extract_bulk_events(email)
    except Exception as exc:
        print(f"    {Fore.YELLOW}Could not parse bulk events: {exc}{Style.RESET_ALL}")
        return []

    if not events:
        print(f"    {Fore.YELLOW}No events found in email.{Style.RESET_ALL}")
        return []

    print(f"    Found {len(events)} event(s) to create.")
    changes = []

    for i, event in enumerate(events):
        title = event.get("title", "Untitled")
        dt_str = event.get("start_datetime", "unknown date")
        print(f"    [{i + 1}/{len(events)}] {title} — {dt_str}")

        try:
            change = _apply_action(calendar_service, "create", event, email)
            if change:
                changes.append(change)
                event_records[f"{email['id']}:{i}"] = {
                    "title": event.get("title", "Untitled"),
                    "start_datetime": event.get("start_datetime", ""),
                    "end_datetime": event.get("end_datetime") or "",
                    "location": event.get("location") or "",
                    "description": event.get("description") or "",
                    "calendar_event_id": change["calendar_event_id"],
                    "status": "active",
                }
        except Exception as exc:
            print(f"      {Fore.YELLOW}Calendar error: {exc}{Style.RESET_ALL}")

    return changes


def _apply_action(calendar_service, action: str, event: dict, email: dict) -> dict | None:
    title = event.get("title", "Untitled")
    dt_str = _format_datetime(event)

    if action == "create":
        start_dt = cal._parse_dt(event.get("start_datetime", ""))

        if start_dt is not None:
            if cal._is_date_only(start_dt):
                if start_dt < date.today():
                    print(f"    {Fore.YELLOW}Skipped (past date): {title} was on {start_dt}{Style.RESET_ALL}")
                    return None
            else:
                start_naive = start_dt.replace(tzinfo=None) if start_dt.tzinfo else start_dt
                if start_naive < datetime.utcnow():
                    print(f"    {Fore.YELLOW}Skipped (past date): {title} was on {start_dt}{Style.RESET_ALL}")
                    return None

        # Check by title first, then fall back to checking by start time (catches
        # same reservation sent as two confirmation emails with different subjects)
        existing = cal.find_all_events(calendar_service, title, start_dt)
        if not existing and start_dt:
            existing = cal.find_agent_events_at_time(calendar_service, start_dt)

        if len(existing) > 1:
            # Keep the first (earliest created), delete the rest
            to_delete = existing[1:]
            for dupe in to_delete:
                cal.delete_event(calendar_service, dupe["id"])
                print(f"    {Fore.RED}Deleted duplicate: {dupe.get('summary')} ({dupe.get('id')}){Style.RESET_ALL}")
            print(f"    {Fore.YELLOW}Kept existing event, removed {len(to_delete)} duplicate(s).{Style.RESET_ALL}")
            return None

        if len(existing) == 1:
            print(f"    {Fore.YELLOW}Duplicate — '{existing[0].get('summary')}' already exists at this time: {existing[0].get('htmlLink', '')}{Style.RESET_ALL}")
            return None

        created = cal.create_event(calendar_service, event)
        print(f"    {Fore.GREEN}Created: {created.get('htmlLink', '')}{Style.RESET_ALL}")
        return {
            "action": "create",
            "email_subject": email["subject"],
            "email_sender": email["sender"],
            "event_title": title,
            "event_datetime": dt_str,
            "detail": "New event added to calendar",
            "calendar_event_id": created["id"],
        }

    if action == "cancel":
        old_dt = event.get("old_start_datetime") or event.get("start_datetime")
        existing = cal.find_event(calendar_service, title, cal._parse_dt(old_dt))
        if existing:
            cal.delete_event(calendar_service, existing["id"])
            print(f"    {Fore.RED}Deleted event: {existing.get('summary')}{Style.RESET_ALL}")
            return {
                "action": "cancel",
                "email_subject": email["subject"],
                "email_sender": email["sender"],
                "event_title": title,
                "event_datetime": dt_str,
                "detail": "Event removed from calendar",
            }
        else:
            print(f"    {Fore.YELLOW}No matching event found to cancel.{Style.RESET_ALL}")
            return {
                "action": "cancel",
                "email_subject": email["subject"],
                "email_sender": email["sender"],
                "event_title": title,
                "event_datetime": dt_str,
                "detail": "Cancellation received — no matching event found on calendar",
            }

    if action == "reschedule":
        old_dt_str = event.get("old_start_datetime") or event.get("start_datetime")
        existing = cal.find_event(calendar_service, title, cal._parse_dt(old_dt_str))
        if existing:
            updated = cal.update_event(calendar_service, existing["id"], event)
            print(f"    {Fore.CYAN}Updated: {updated.get('htmlLink', '')}{Style.RESET_ALL}")
            return {
                "action": "reschedule",
                "email_subject": email["subject"],
                "email_sender": email["sender"],
                "event_title": title,
                "event_datetime": dt_str,
                "detail": f"Rescheduled from {old_dt_str}",
            }
        else:
            # No existing event found — create it instead
            created = cal.create_event(calendar_service, event)
            print(f"    {Fore.GREEN}No existing event found; created new: {created.get('htmlLink', '')}{Style.RESET_ALL}")
            return {
                "action": "reschedule",
                "email_subject": email["subject"],
                "email_sender": email["sender"],
                "event_title": title,
                "event_datetime": dt_str,
                "detail": "Rescheduled (no prior event found — created as new)",
            }

    return None
