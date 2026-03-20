"""
Run this script ONCE on your computer to generate your Google OAuth refresh token.
It will open a browser window for you to authorize Gmail and Calendar access.
The output is a JSON string to paste into GitHub as the GOOGLE_CREDENTIALS_JSON secret.

Requirements:
  pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client

Usage:
  python get_token.py
"""

import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar"
]

def main():
    print("This will open a browser window to authorize access.")
    print("Make sure you have downloaded your OAuth client credentials JSON from Google Cloud.")
    print()
    credentials_file = input("Enter the path to your downloaded credentials JSON file: ").strip()

    flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
    creds = flow.run_local_server(port=0)

    output = {
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token
    }

    print()
    print("=" * 60)
    print("Copy the entire JSON string below and save it as the")
    print("GOOGLE_CREDENTIALS_JSON secret in GitHub:")
    print("=" * 60)
    print(json.dumps(output))
    print("=" * 60)

if __name__ == "__main__":
    main()
