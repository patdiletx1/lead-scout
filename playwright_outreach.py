#!/usr/bin/env python3
"""
Playwright Outreach — LinkedIn automation for ClientScout.

Sends LinkedIn connection requests and direct messages using the
existing Playwright persistent session (reuses .playwright_sessions/linkedin).

Actions:
  - send_connection_request(linkedin_url, message)
  - send_dm(linkedin_url, message)
  - check_connection_status(linkedin_url) → connected | pending | not_connected

Uses human-like delays and anti-detection patterns from risk_manager.py.
"""

from __future__ import annotations

import os
import sys
import json
import time
import random
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Reuse existing session from the job search system
SESSION_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "quotedme_scraper", ".playwright_sessions")
if not os.path.isdir(SESSION_DIR):
    SESSION_DIR = os.path.join(SCRIPT_DIR, ".playwright_sessions")

LINKEDIN_SESSION = os.path.join(SESSION_DIR, "linkedin")
os.makedirs(LINKEDIN_SESSION, exist_ok=True)

# ── Rate Limits (conservative for outreach, not job applications) ──────

LINKEDIN_CONNECT_LIMIT_PER_DAY = 20
LINKEDIN_DM_LIMIT_PER_DAY = 10
MIN_DELAY_BETWEEN_ACTIONS = 45  # seconds
MAX_DELAY_BETWEEN_ACTIONS = 120  # seconds

# ── Output ─────────────────────────────────────────────────────────────

SCREENSHOT_DIR = os.path.join(SCRIPT_DIR, "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


class LinkedInOutreach:
    """Handles LinkedIn connection requests and DMs via Playwright."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._context = None
        self._page = None
        self._playwright = None
        self.daily_connects = 0
        self.daily_dms = 0

    # ── Session Management ──────────────────────────────────────────

    def _launch_browser(self):
        """Launch persistent browser context with LinkedIn session."""
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()

        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=LINKEDIN_SESSION,
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--window-size=1280,800",
            ],
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        self._page = self._context.new_page()

    def _close_browser(self):
        """Close browser and cleanup."""
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._context = None
        self._page = None
        self._playwright = None

    def _ensure_logged_in(self) -> bool:
        """Check if the LinkedIn session is still valid (logged in)."""
        try:
            self._page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)

            # Check for logged-in indicators
            logged_in = (
                "feed" in self._page.url.lower() or
                self._page.locator(".feed-identity-module").count() > 0 or
                self._page.locator("[data-control-name='identity_welcome_message']").count() > 0 or
                self._page.locator(".global-nav__me").count() > 0
            )

            if not logged_in:
                # Check if we're on login page
                if "login" in self._page.url.lower():
                    return False
                # Might be on homepage without being logged in
                if self._page.locator("a[href*='/login']").count() > 0:
                    return False

            return True

        except Exception as e:
            print(f"     ⚠ Login check error: {e}")
            return False

    # ── Human-like Behavior ──────────────────────────────────────────

    def _human_delay(self, min_s: float = 1.0, max_s: float = 3.0):
        """Random delay to simulate human reading/thinking time."""
        time.sleep(random.uniform(min_s, max_s))

    def _human_scroll(self):
        """Random scroll to look more human."""
        try:
            amount = random.randint(100, 500)
            self._page.evaluate(f"window.scrollBy(0, {amount})")
            time.sleep(random.uniform(0.3, 1.0))
        except Exception:
            pass

    def _random_mouse_move(self):
        """Move mouse to a random position on the page."""
        try:
            x = random.randint(100, 700)
            y = random.randint(100, 500)
            self._page.mouse.move(x, y, steps=random.randint(5, 15))
        except Exception:
            pass

    def _screenshot(self, label: str):
        """Take a screenshot for debugging."""
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(SCREENSHOT_DIR, f"outreach_{label}_{ts}.png")
            self._page.screenshot(path=path)
            return path
        except Exception:
            return ""

    # ── Profile Navigation ───────────────────────────────────────────

    def _extract_profile_username(self, linkedin_url: str) -> str:
        """Extract the username/slug from a LinkedIn URL."""
        # Handle various URL formats:
        # https://www.linkedin.com/in/username/
        # https://linkedin.com/in/username
        # https://www.linkedin.com/company/companyname/
        parsed = urlparse(linkedin_url)
        path = parsed.path.strip("/")
        parts = path.split("/")
        return parts[-1] if parts else ""

    def _navigate_to_profile(self, linkedin_url: str) -> bool:
        """Navigate to a LinkedIn profile page. Returns True if successful."""
        try:
            # Normalize URL
            if not linkedin_url.startswith("http"):
                linkedin_url = f"https://www.linkedin.com/in/{linkedin_url}/"

            self._page.goto(linkedin_url, wait_until="domcontentloaded", timeout=15000)
            self._human_delay(2, 4)

            # Check if page loaded (not 404, not error)
            if self._page.locator("text=Page not found").count() > 0:
                print(f"     ⚠ Profile not found: {linkedin_url}")
                return False
            if self._page.locator("text=This page doesn't exist").count() > 0:
                print(f"     ⚠ Profile not found: {linkedin_url}")
                return False

            self._human_scroll()
            return True

        except Exception as e:
            print(f"     ⚠ Navigation error: {e}")
            return False

    # ── Connection Request ───────────────────────────────────────────

    def send_connection_request(self, linkedin_url: str, message: str) -> dict:
        """
        Send a LinkedIn connection request with a personalized message.

        Args:
            linkedin_url: Full LinkedIn profile URL or username
            message: Personalized connection note (max 300 chars)

        Returns:
            dict with keys: ok, action, error, screenshot
        """
        result = {"ok": False, "action": "connect", "error": "", "screenshot": ""}

        try:
            self._launch_browser()

            # Check login
            if not self._ensure_logged_in():
                result["error"] = "LinkedIn session expired. Please re-login."
                self._screenshot("login_failed")
                return result

            # Navigate to profile
            if not self._navigate_to_profile(linkedin_url):
                result["error"] = "Could not navigate to profile"
                return result

            # Look for the Connect button
            # LinkedIn has multiple button variations:
            connect_selectors = [
                "button[aria-label*='Connect']",
                "button[aria-label*='connect']",
                "button[aria-label*='Conectar']",
                "button:has-text('Connect'):not(:has-text('Connections'))",
                "button:has-text('Conectar')",
                "[data-control-name='connect']",
                "button.pvs-profile-actions__action",
            ]

            connect_btn = None
            for selector in connect_selectors:
                try:
                    btn = self._page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible():
                        btn_text = btn.inner_text().strip().lower()
                        if "connect" in btn_text or "conectar" in btn_text:
                            connect_btn = btn
                            break
                except Exception:
                    continue

            if not connect_btn:
                # Check if we're already connected or pending
                status = self._detect_connection_status()
                if status == "connected":
                    result["error"] = "Already connected"
                    result["ok"] = True
                    result["action"] = "already_connected"
                    return result
                elif status == "pending":
                    result["error"] = "Connection request already pending"
                    result["ok"] = True
                    result["action"] = "already_pending"
                    return result
                else:
                    result["screenshot"] = self._screenshot("no_connect_btn") or ""
                    result["error"] = "Connect button not found"
                    return result

            # Click Connect
            self._random_mouse_move()
            connect_btn.click()
            self._human_delay(1.5, 3)

            # Look for "Add a note" button (to personalize the invite)
            note_selectors = [
                "button[aria-label*='Add a note']",
                "button:has-text('Add a note')",
                "button:has-text('Agregar nota')",
                "button[aria-label*='Agregar nota']",
            ]

            add_note_btn = None
            for selector in note_selectors:
                try:
                    btn = self._page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible():
                        add_note_btn = btn
                        break
                except Exception:
                    continue

            if add_note_btn:
                add_note_btn.click()
                self._human_delay(0.5, 1.5)

            # Find the message textarea and fill it
            textarea_selectors = [
                "textarea#custom-message",
                "textarea[name='message']",
                "textarea[aria-label*='note']",
                "textarea[aria-label*='mensaje']",
                "textarea.send-invite__custom-message",
                "textarea.connect-button-send-invite__custom-message",
                "textarea",
            ]

            textarea = None
            for selector in textarea_selectors:
                try:
                    ta = self._page.locator(selector).first
                    if ta.count() > 0 and ta.is_visible():
                        textarea = ta
                        break
                except Exception:
                    continue

            if textarea:
                # Type the message (with human-like typing speed)
                textarea.click()
                self._human_delay(0.2, 0.5)
                textarea.fill(message)
                self._human_delay(0.5, 1.5)
            else:
                print("     ⚠ Could not find message textarea. Sending without note.")

            # Click the final Send button
            send_selectors = [
                "button[aria-label*='Send now']",
                "button[aria-label*='Send invitation']",
                "button[aria-label*='Enviar ahora']",
                "button[aria-label*='Enviar invitacion']",
                "button:has-text('Send now')",
                "button:has-text('Send')",
                "button:has-text('Enviar')",
            ]

            send_btn = None
            for selector in send_selectors:
                try:
                    btn = self._page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible():
                        send_btn = btn
                        break
                except Exception:
                    continue

            if not send_btn:
                # Try to find any submit button in the modal
                try:
                    send_btn = self._page.locator(
                        "div.send-invite button[type='submit'], "
                        "div[role='dialog'] button.artdeco-button--primary, "
                        "div[role='dialog'] button:has-text('Send')"
                    ).first
                except Exception:
                    pass

            if send_btn and send_btn.count() > 0 and send_btn.is_visible():
                send_btn.click()
                self._human_delay(1, 2)
                result["ok"] = True
                result["action"] = "connect_sent"
                print("     ✓ Connection request sent")
            else:
                result["screenshot"] = self._screenshot("no_send_btn") or ""
                result["error"] = "Send button not found"
                print(f"     ⚠ {result['error']}")

            self.daily_connects += 1

        except Exception as e:
            result["error"] = str(e)
            self._screenshot("connect_error")

        finally:
            self._close_browser()

        return result

    # ── Direct Message ───────────────────────────────────────────────

    def send_dm(self, linkedin_url: str, message: str) -> dict:
        """
        Send a LinkedIn direct message to a connection.

        Args:
            linkedin_url: Full LinkedIn profile URL
            message: Message body

        Returns:
            dict with keys: ok, action, error, screenshot
        """
        result = {"ok": False, "action": "dm", "error": "", "screenshot": ""}

        try:
            self._launch_browser()

            if not self._ensure_logged_in():
                result["error"] = "LinkedIn session expired."
                return result

            if not self._navigate_to_profile(linkedin_url):
                result["error"] = "Could not navigate to profile"
                return result

            # Look for Message button
            msg_selectors = [
                "button[aria-label*='Message']",
                "button[aria-label*='Mensaje']",
                "button:has-text('Message'):not(:has-text('Messages'))",
                "button:has-text('Mensaje')",
                "[data-control-name='message']",
            ]

            msg_btn = None
            for selector in msg_selectors:
                try:
                    btn = self._page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible():
                        msg_btn = btn
                        break
                except Exception:
                    continue

            if not msg_btn:
                result["error"] = "Message button not found. Are you connected?"
                result["screenshot"] = self._screenshot("no_msg_btn") or ""
                return result

            msg_btn.click()
            self._human_delay(2, 4)

            # LinkedIn opens a messaging overlay/dialog
            # Find the message textarea
            msg_textarea_selectors = [
                "div.msg-form__contenteditable[contenteditable='true']",
                "div[contenteditable='true'][role='textbox']",
                "div.msg-form__contenteditable p",
                "textarea.msg-form__textarea",
            ]

            textarea = None
            for selector in msg_textarea_selectors:
                try:
                    ta = self._page.locator(selector).first
                    if ta.count() > 0 and ta.is_visible():
                        textarea = ta
                        break
                except Exception:
                    continue

            if not textarea:
                # Last resort: click where the message box should be and type
                try:
                    self._page.click("div.msg-form__contenteditable")
                    textarea = self._page.locator("div.msg-form__contenteditable").first
                except Exception:
                    pass

            if textarea:
                textarea.click()
                self._human_delay(0.3, 0.8)
                # Use keyboard input for contenteditable divs
                self._page.keyboard.type(message, delay=random.randint(30, 80))
                self._human_delay(0.5, 1.5)
            else:
                result["error"] = "Message textarea not found"
                result["screenshot"] = self._screenshot("no_dm_textarea") or ""
                return result

            # Click Send
            send_selectors = [
                "button.msg-form__send-button",
                "button[type='submit']",
                "button:has-text('Send')",
                "button[aria-label*='Send']",
            ]

            send_btn = None
            for selector in send_selectors:
                try:
                    btn = self._page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible():
                        send_btn = btn
                        break
                except Exception:
                    continue

            if send_btn:
                # Use Enter key as fallback (LinkedIn sends on Enter)
                send_btn.click()
                self._human_delay(1, 2)
                result["ok"] = True
                result["action"] = "dm_sent"
                print("     ✓ DM sent")
            else:
                # Try pressing Enter
                try:
                    self._page.keyboard.press("Enter")
                    self._human_delay(1, 2)
                    result["ok"] = True
                    result["action"] = "dm_sent"
                    print("     ✓ DM sent (via Enter)")
                except Exception:
                    result["error"] = "Could not send DM"

            self.daily_dms += 1

        except Exception as e:
            result["error"] = str(e)
            self._screenshot("dm_error")

        finally:
            self._close_browser()

        return result

    # ── Connection Status ────────────────────────────────────────────

    def check_connection_status(self, linkedin_url: str) -> str:
        """
        Check connection status with a LinkedIn profile.
        Returns: 'connected', 'pending', 'not_connected', or 'error'
        """
        try:
            self._launch_browser()

            if not self._ensure_logged_in():
                return "error"

            if not self._navigate_to_profile(linkedin_url):
                return "error"

            return self._detect_connection_status()

        except Exception:
            return "error"
        finally:
            self._close_browser()

    def _detect_connection_status(self) -> str:
        """Detect connection status from current profile page."""
        try:
            page_text = self._page.inner_text("body").lower()

            # Check for various indicators
            if any(phrase in page_text for phrase in [
                "remove connection", "1st", "direct message",
            ]):
                # Check if there's a "Message" button (indicates connected)
                if self._page.locator("button:has-text('Message')").count() > 0:
                    return "connected"

            if any(phrase in page_text for phrase in [
                "pending", "withdraw", "invitation sent",
            ]):
                return "pending"

            if self._page.locator("button:has-text('Connect')").count() > 0:
                return "not_connected"
            if self._page.locator("button:has-text('Conectar')").count() > 0:
                return "not_connected"

            return "not_connected"

        except Exception:
            return "error"


# ── CLI ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LinkedIn Outreach via Playwright")
    subparsers = parser.add_subparsers(dest="command")

    # connect
    connect_parser = subparsers.add_parser("connect", help="Send connection request")
    connect_parser.add_argument("url", help="LinkedIn profile URL")
    connect_parser.add_argument("message", help="Connection note (max 300 chars)")

    # dm
    dm_parser = subparsers.add_parser("dm", help="Send direct message")
    dm_parser.add_argument("url", help="LinkedIn profile URL")
    dm_parser.add_argument("message", help="Message body")

    # status
    status_parser = subparsers.add_parser("status", help="Check connection status")
    status_parser.add_argument("url", help="LinkedIn profile URL")

    # test
    test_parser = subparsers.add_parser("test", help="Test session is valid")

    args = parser.parse_args()
    outreach = LinkedInOutreach(headless=False)  # Visible for testing

    if args.command == "connect":
        result = outreach.send_connection_request(args.url, args.message)
        print(json.dumps(result, indent=2))

    elif args.command == "dm":
        result = outreach.send_dm(args.url, args.message)
        print(json.dumps(result, indent=2))

    elif args.command == "status":
        status = outreach.check_connection_status(args.url)
        print(f"Status: {status}")

    elif args.command == "test":
        outreach._launch_browser()
        logged_in = outreach._ensure_logged_in()
        print(f"Logged in: {logged_in}")
        outreach._close_browser()

    else:
        parser.print_help()
