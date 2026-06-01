"""
Sends a summary email via Gmail listing all calendar changes made.
"""

import base64
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_summary(gmail_service, changes: list[dict], ignored_count: int) -> None:
    """
    Send a summary email to the authenticated account listing all calendar changes.

    Each change dict has keys: action, email_subject, email_sender, event_title,
    event_datetime, detail (e.g. "event created", "event cancelled").
    """
    profile = gmail_service.users().getProfile(userId="me").execute()
    user_email = profile["emailAddress"]

    today = date.today().strftime("%B %d, %Y")
    subject = f"Calendar Agent Summary — {today}"

    html = _build_html(changes, ignored_count)

    msg = MIMEMultipart("alternative")
    msg["to"] = user_email
    msg["from"] = user_email
    msg["subject"] = subject
    msg.attach(MIMEText(html, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    gmail_service.users().messages().send(userId="me", body={"raw": raw}).execute()


def _build_html(changes: list[dict], ignored_count: int) -> str:
    action_emoji = {
        "create": "✅",
        "reschedule": "🔄",
        "cancel": "❌",
    }

    rows = ""
    for c in changes:
        emoji = action_emoji.get(c["action"], "•")
        action_label = c["action"].capitalize()
        rows += f"""
        <tr>
          <td style="padding:8px 12px; border-bottom:1px solid #eee;">{emoji} {action_label}</td>
          <td style="padding:8px 12px; border-bottom:1px solid #eee;"><strong>{c['event_title']}</strong></td>
          <td style="padding:8px 12px; border-bottom:1px solid #eee;">{c.get('event_datetime', '—')}</td>
          <td style="padding:8px 12px; border-bottom:1px solid #eee; color:#666; font-size:13px;">{c.get('detail', '')}</td>
        </tr>"""

    if not rows:
        changes_section = "<p style='color:#666;'>No calendar changes were made.</p>"
    else:
        changes_section = f"""
        <table style="width:100%; border-collapse:collapse; font-family:sans-serif; font-size:14px;">
          <thead>
            <tr style="background:#f5f5f5; text-align:left;">
              <th style="padding:8px 12px;">Action</th>
              <th style="padding:8px 12px;">Event</th>
              <th style="padding:8px 12px;">Date / Time</th>
              <th style="padding:8px 12px;">Note</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>"""

    return f"""
    <html><body style="font-family:sans-serif; max-width:700px; margin:0 auto; color:#222;">
      <h2 style="border-bottom:2px solid #4285f4; padding-bottom:8px;">📅 Calendar Agent Summary</h2>

      <h3>Changes Made ({len(changes)})</h3>
      {changes_section}

      <p style="margin-top:24px; color:#888; font-size:13px;">
        {ignored_count} email(s) were skipped (promotional, unrelated, or already processed).
      </p>

      <p style="color:#aaa; font-size:12px; border-top:1px solid #eee; padding-top:12px;">
        Sent by your Calendar Agent
      </p>
    </body></html>"""
