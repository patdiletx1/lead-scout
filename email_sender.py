#!/usr/bin/env python3
"""
Email Sender for ClientScout.

Sends cold outreach emails via SMTP (Gmail) or SendGrid API.
Integrates with the outreach pipeline — called when a draft is approved.

Configuration via environment variables:
  GMAIL_APP_EMAIL — Gmail address (e.g., patdiletx@gmail.com)
  GMAIL_APP_PASSWORD — Gmail App Password (not regular password)
  SENDGRID_API_KEY — SendGrid API key (optional, takes priority if set)

Setup Gmail App Password:
  1. Enable 2FA on your Google Account
  2. Go to https://myaccount.google.com/apppasswords
  3. Generate an App Password for "Mail"
  4. Set GMAIL_APP_PASSWORD to that value
"""

from __future__ import annotations

import os
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────

DEFAULT_FROM_NAME = "Patricio Diaz"
DEFAULT_FROM_EMAIL = os.environ.get("GMAIL_APP_EMAIL", "patdiletx@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")

# Signature block appended to all emails
SIGNATURE_ES = """
—
Patricio Diaz
Senior Full Stack Developer & Process Automation Consultant
+56 9 2820 7086 | patdilet.dev
Santiago, Chile
"""

SIGNATURE_EN = """
—
Patricio Diaz
Senior Full Stack Developer & Process Automation Consultant
+56 9 2820 7086 | patdilet.dev
Santiago, Chile
"""


class EmailSender:
    """Sends outreach emails via Gmail SMTP or SendGrid."""

    def __init__(self, from_email: str = "", from_name: str = "", smtp_password: str = ""):
        self.from_email = from_email or DEFAULT_FROM_EMAIL
        self.from_name = from_name or DEFAULT_FROM_NAME
        self.smtp_password = smtp_password or GMAIL_APP_PASSWORD
        self.sendgrid_key = SENDGRID_API_KEY

    def _is_configured(self) -> bool:
        """Check if any sending method is configured."""
        return bool(self.smtp_password) or bool(self.sendgrid_key)

    def send(self, to_email: str, subject: str, body: str, to_name: str = "",
             language: str = "en") -> dict:
        """
        Send an outreach email.

        Args:
            to_email: Recipient email address
            subject: Email subject line
            body: Email body (plain text)
            to_name: Recipient name (for personalization)
            language: 'en' or 'es' for signature

        Returns:
            dict with keys: ok, message_id, error, method
        """
        if not self._is_configured():
            return {"ok": False, "error": "No email configuration (GMAIL_APP_PASSWORD or SENDGRID_API_KEY)", "method": "none"}

        # Add signature
        signature = SIGNATURE_ES if language == "es" else SIGNATURE_EN
        full_body = body.strip() + signature

        # Try SendGrid first if configured
        if self.sendgrid_key:
            return self._send_via_sendgrid(to_email, subject, full_body, to_name)

        # Fall back to Gmail SMTP
        if self.smtp_password:
            return self._send_via_gmail(to_email, subject, full_body, to_name)

        return {"ok": False, "error": "No sending method available", "method": "none"}

    def _send_via_gmail(self, to_email: str, subject: str, body: str, to_name: str) -> dict:
        """Send via Gmail SMTP with App Password."""
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = to_email
            msg["Subject"] = subject
            msg["Message-ID"] = f"<clientscout-{datetime.now().strftime('%Y%m%d%H%M%S')}@patdilet.dev>"
            msg["Date"] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")

            # Plain text body
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.from_email, self.smtp_password)
                server.send_message(msg)

            return {
                "ok": True,
                "message_id": msg["Message-ID"],
                "method": "gmail_smtp",
            }

        except smtplib.SMTPAuthenticationError:
            return {"ok": False, "error": "Gmail authentication failed. Check GMAIL_APP_PASSWORD.", "method": "gmail_smtp"}
        except smtplib.SMTPRecipientsRefused:
            return {"ok": False, "error": f"Recipient refused: {to_email}", "method": "gmail_smtp"}
        except Exception as e:
            return {"ok": False, "error": str(e), "method": "gmail_smtp"}

    def _send_via_sendgrid(self, to_email: str, subject: str, body: str, to_name: str) -> dict:
        """Send via SendGrid API."""
        import httpx

        try:
            r = httpx.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {self.sendgrid_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "personalizations": [{
                        "to": [{"email": to_email, "name": to_name or to_email}],
                        "subject": subject,
                    }],
                    "from": {"email": self.from_email, "name": self.from_name},
                    "content": [{"type": "text/plain", "value": body}],
                },
                timeout=15,
            )

            if r.status_code in (200, 201, 202):
                return {
                    "ok": True,
                    "message_id": r.headers.get("X-Message-Id", ""),
                    "method": "sendgrid",
                }
            else:
                return {"ok": False, "error": f"SendGrid error {r.status_code}: {r.text[:200]}", "method": "sendgrid"}

        except Exception as e:
            return {"ok": False, "error": str(e), "method": "sendgrid"}

    def send_test(self, to_email: str) -> dict:
        """Send a test email to verify configuration."""
        return self.send(
            to_email=to_email,
            subject="ClientScout — Test Email",
            body="This is a test email from ClientScout to verify the email configuration is working correctly.",
            language="en",
        )


# ── CLI ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ClientScout Email Sender")
    subparsers = parser.add_subparsers(dest="command")

    test_parser = subparsers.add_parser("test", help="Send a test email")
    test_parser.add_argument("to", help="Recipient email address")

    send_parser = subparsers.add_parser("send", help="Send an outreach email")
    send_parser.add_argument("to", help="Recipient email")
    send_parser.add_argument("subject", help="Email subject")
    send_parser.add_argument("body", help="Email body (plain text)")
    send_parser.add_argument("--name", default="", help="Recipient name")
    send_parser.add_argument("--lang", default="en", choices=["en", "es"], help="Language for signature")

    args = parser.parse_args()
    sender = EmailSender()

    if not sender._is_configured():
        print("⚠️  Email not configured. Set GMAIL_APP_PASSWORD or SENDGRID_API_KEY env var.")
        print("   Gmail setup: https://myaccount.google.com/apppasswords")
        exit(1)

    if args.command == "test":
        print(f"Sending test email to {args.to}...")
        result = sender.send_test(args.to)
        if result["ok"]:
            print(f"✅ Test email sent via {result['method']} (ID: {result.get('message_id', 'N/A')})")
        else:
            print(f"❌ Failed: {result['error']}")

    elif args.command == "send":
        print(f"Sending to {args.to}...")
        result = sender.send(args.to, args.subject, args.body, args.name, args.lang)
        if result["ok"]:
            print(f"✅ Email sent via {result['method']}")
        else:
            print(f"❌ Failed: {result['error']}")

    else:
        parser.print_help()
