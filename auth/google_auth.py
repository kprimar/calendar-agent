"""
Google OAuth2 authentication for Gmail and Google Calendar.

Flow:
  1. First run: opens browser for user consent, saves token to token.json
  2. Subsequent runs: loads token.json, refreshes automatically if expired
"""

import os
import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from colorama import init, Fore, Style

init(autoreset=True)  # Windows color support

# Scopes needed for both Gmail (read/send) and Calendar (read/write)
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]

AUTH_DIR = Path(__file__).parent.parent / "credentials"
CREDENTIALS_FILE = AUTH_DIR / "credentials.json"
TOKEN_FILE = AUTH_DIR / "token.json"


def _ensure_credentials_exist() -> None:
    if not CREDENTIALS_FILE.exists():
        print(f"{Fore.RED}ERROR: credentials.json not found at {CREDENTIALS_FILE}")
        print(f"{Fore.YELLOW}Steps to fix:")
        print("  1. Go to https://console.cloud.google.com/")
        print("  2. Create a project (or select existing)")
        print("  3. Enable Gmail API and Google Calendar API")
        print("  4. Create OAuth 2.0 credentials (Desktop app)")
        print(f"  5. Download and save as: {CREDENTIALS_FILE}{Style.RESET_ALL}")
        raise FileNotFoundError(f"credentials.json not found at {CREDENTIALS_FILE}")


def get_credentials() -> Credentials:
    """Return valid OAuth2 credentials, running the browser flow if needed."""
    _ensure_credentials_exist()
    AUTH_DIR.mkdir(parents=True, exist_ok=True)

    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print(f"{Fore.CYAN}Refreshing expired token...")
            creds.refresh(Request())
        else:
            print(f"{Fore.CYAN}Starting Google sign-in...")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            print(f"\n{Fore.YELLOW}Copy the URL below into your browser:{Style.RESET_ALL}\n")
            creds = flow.run_local_server(
                port=0,
                open_browser=False,
                authorization_prompt_message="  {url}\n",
            )

        TOKEN_FILE.write_text(creds.to_json())
        print(f"{Fore.GREEN}Token saved to {TOKEN_FILE}")

    return creds


def get_gmail_service():
    """Return an authenticated Gmail API service."""
    creds = get_credentials()
    return build("gmail", "v1", credentials=creds)


def get_calendar_service():
    """Return an authenticated Google Calendar API service."""
    creds = get_credentials()
    return build("calendar", "v3", credentials=creds)


def revoke_token() -> None:
    """Delete the saved token so the next run triggers a fresh sign-in."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        print(f"{Fore.YELLOW}Token revoked. Next run will require sign-in.")
    else:
        print("No token file found.")


def check_auth_status() -> dict:
    """Return a dict describing the current credential state."""
    status = {
        "credentials_file_exists": CREDENTIALS_FILE.exists(),
        "token_file_exists": TOKEN_FILE.exists(),
        "token_valid": False,
        "token_expired": False,
        "scopes": [],
    }

    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            status["token_valid"] = creds.valid
            status["token_expired"] = creds.expired
            status["scopes"] = list(creds.scopes or [])
        except Exception as e:
            status["error"] = str(e)

    return status


if __name__ == "__main__":
    print(f"{Fore.CYAN}=== Google Auth Status ==={Style.RESET_ALL}")
    status = check_auth_status()

    print(f"credentials.json present : {status['credentials_file_exists']}")
    print(f"token.json present       : {status['token_file_exists']}")
    print(f"Token valid              : {status['token_valid']}")

    if not status["credentials_file_exists"]:
        print(f"\n{Fore.RED}Place credentials.json in: {CREDENTIALS_FILE}{Style.RESET_ALL}")
    elif not status["token_file_exists"] or not status["token_valid"]:
        print(f"\n{Fore.YELLOW}Running auth flow...{Style.RESET_ALL}")
        creds = get_credentials()
        print(f"{Fore.GREEN}Authentication successful!{Style.RESET_ALL}")

        # Quick smoke test
        gmail = get_gmail_service()
        profile = gmail.users().getProfile(userId="me").execute()
        print(f"\nGmail account : {profile['emailAddress']}")

        calendar = get_calendar_service()
        cal_list = calendar.calendarList().list(maxResults=3).execute()
        calendars = cal_list.get("items", [])
        print(f"Calendars found : {len(calendars)}")
        for cal in calendars:
            print(f"  - {cal['summary']}")
    else:
        print(f"{Fore.GREEN}Already authenticated!{Style.RESET_ALL}")
