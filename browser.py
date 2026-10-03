import asyncio
import logging
import re
import ipaddress
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from config import MAX_GLOBAL_CONCURRENT_TABS, PAGE_TIMEOUT_MS, POST_SUBMIT_WAIT_MS

logger = logging.getLogger(__name__)

# Global semaphore — limits total concurrent browser tabs across ALL users.
_tab_semaphore: asyncio.Semaphore | None = None

# ────────────────────────────────────────────────────────────────────────────
# SSRF / private-IP guard
# ────────────────────────────────────────────────────────────────────────────
_BLOCKED_PATTERNS = re.compile(
    r'(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|169\.254\.'
    r'|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+'
    r'|192\.168\.\d+\.\d+)',
    re.IGNORECASE,
)

def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if _BLOCKED_PATTERNS.search(host):
            return False
        # Also block cloud metadata endpoints
        if host in ("metadata.google.internal", "169.254.169.254"):
            return False
        # Reject bare IPs that are private
        try:
            addr = ipaddress.ip_address(host)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                return False
        except ValueError:
            pass  # it's a domain name, fine
        return True
    except Exception:
        return False

# ────────────────────────────────────────────────────────────────────────────
# Field-classifier helpers
# ────────────────────────────────────────────────────────────────────────────
_FIELD_MAP = {
    "full_name": [
        "full.name", "fullname", "your.name", "contact.name",
        "name", "your_name",
    ],
    "first_name": ["first.name", "firstname", "fname", "given.name"],
    "last_name":  ["last.name", "lastname", "lname", "surname", "family.name"],
    "email": [
        "email", "e.mail", "email.address", "emailaddress",
        "contact.email", "your.email",
    ],
    "phone": [
        "phone", "telephone", "tel", "mobile", "cell",
        "mobile.number", "contact.number", "phone.number",
    ],
    "company": [
        "company", "company.name", "organization", "organisation",
        "business", "business.name",
    ],
    "subject": ["subject", "topic", "inquiry.subject", "re"],
    "message": [
        "message", "messages", "comment", "comments", "inquiry",
        "description", "your.message", "body", "content",
    ],
    "website": ["website", "url", "web", "site", "homepage"],
}

def _classify_field(name: str, field_id: str, placeholder: str,
                    aria_label: str, autocomplete: str) -> str | None:
    """Return the profile key that best matches this input, or None."""
    combined = " ".join([name, field_id, placeholder, aria_label, autocomplete]).lower()
    # Replace punctuation with space for matching
    combined = re.sub(r"[-_./]", " ", combined)

    for profile_key, keywords in _FIELD_MAP.items():
        for kw in keywords:
            kw_normalised = kw.replace(".", " ")
            if kw_normalised in combined:
                return profile_key
    return None

# ────────────────────────────────────────────────────────────────────────────
# Contact-form scorer
# ────────────────────────────────────────────────────────────────────────────
_IGNORE_TYPES = {"hidden", "submit", "button", "checkbox", "radio",
                 "file", "image", "reset", "color", "range"}

_IGNORE_NAMES = re.compile(
    r"(search|password|pass|login|coupon|promo|discount|newsletter"
    r"|date|credit.card|card.number|cvv|zip|postal)",
    re.IGNORECASE,
)

async def _score_form(form) -> int:
    """Higher = more likely a contact form."""
    score = 0
    html = (await form.inner_html()).lower()
    if "email"   in html: score += 5
    if "message" in html: score += 4
    if "contact" in html: score += 3
    if "name"    in html: score += 2
    if "phone"   in html: score += 1
    if "submit"  in html or "send" in html: score += 2
    # Penalise clearly wrong forms
    if "password" in html: score -= 10
    if "search"   in html: score -= 5
    if "payment"  in html or "credit" in html: score -= 15
    return score

# ────────────────────────────────────────────────────────────────────────────
# BrowserManager
# ────────────────────────────────────────────────────────────────────────────
class BrowserManager:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _ensure_started(self):
        async with self._lock:
            if self._browser is None:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-extensions",
                    ],
                )

    async def stop(self):
        async with self._lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None

    async def process_website(self, url: str, profile: dict) -> dict:
        """Open one URL, find and fill the best contact form, submit it."""
        global _tab_semaphore

        result = {"status": "FAILED", "reason": "Unknown error"}

        # Safety check
        if not _is_safe_url(url):
            return {"status": "BLOCKED", "reason": "Private / unsafe URL blocked"}

        await self._ensure_started()

        if _tab_semaphore is None:
            return {"status": "ERROR", "reason": "Browser semaphore not initialised yet"}

        async with _tab_semaphore:
            context = await self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                java_script_enabled=True,
                ignore_https_errors=True,
            )
            page = await context.new_page()

            try:
                # ── 1. Navigate ──────────────────────────────────────────
                try:
                    await page.goto(url, timeout=PAGE_TIMEOUT_MS,
                                    wait_until="domcontentloaded")
                    # Give JS-rendered forms a moment
                    await page.wait_for_timeout(1500)
                except PlaywrightTimeoutError:
                    return {"status": "TIMEOUT", "reason": "Page load timeout"}

                # Early CAPTCHA / anti-bot screen check
                if await _has_captcha(page):
                    return {"status": "CAPTCHA", "reason": "CAPTCHA on page load"}

                # ── 2. Find best contact form ────────────────────────────
                forms = await page.locator("form").all()
                if not forms:
                    return {"status": "NO_FORM", "reason": "No <form> tag found"}

                scored = []
                for f in forms:
                    s = await _score_form(f)
                    scored.append((s, f))
                scored.sort(key=lambda x: x[0], reverse=True)

                best_score, contact_form = scored[0]
                if best_score < 4:
                    return {"status": "NO_FORM", "reason": "No contact form identified"}

                # ── 3. Fill fields ───────────────────────────────────────
                missing_required = []
                inputs = await contact_form.locator("input, textarea").all()

                for inp in inputs:
                    try:
                        inp_type   = (await inp.get_attribute("type")        or "text").lower()
                        if inp_type in _IGNORE_TYPES:
                            continue

                        name_attr  = await inp.get_attribute("name")        or ""
                        id_attr    = await inp.get_attribute("id")          or ""
                        ph         = await inp.get_attribute("placeholder") or ""
                        aria       = await inp.get_attribute("aria-label")  or ""
                        autocomp   = await inp.get_attribute("autocomplete") or ""
                        required   = await inp.get_attribute("required") is not None

                        # Skip obviously unrelated fields
                        if _IGNORE_NAMES.search(f"{name_attr} {id_attr}"):
                            continue

                        key = _classify_field(name_attr, id_attr, ph, aria, autocomp)
                        value = profile.get(key) if key else None

                        # Handle first_name / last_name fallback to full_name parts
                        if not value and key == "first_name" and profile.get("full_name"):
                            parts = profile["full_name"].split()
                            value = parts[0] if parts else None
                        if not value and key == "last_name" and profile.get("full_name"):
                            parts = profile["full_name"].split()
                            value = parts[-1] if len(parts) > 1 else None

                        if value:
                            await inp.fill(str(value), timeout=5000)
                        elif required:
                            missing_required.append(name_attr or id_attr or key or "unknown")
                    except Exception as e:
                        logger.debug(f"Field fill skipped: {e}")

                if missing_required:
                    return {
                        "status": "MISSING_INFORMATION",
                        "reason": f"Required fields missing: {', '.join(missing_required[:5])}",
                    }

                # ── 4. Find & click submit button ────────────────────────
                submit_locator = contact_form.locator(
                    "button[type='submit'], "
                    "input[type='submit'], "
                    "button:has-text('Submit'), "
                    "button:has-text('Send'), "
                    "button:has-text('Send Message'), "
                    "button:has-text('Send Inquiry'), "
                    "button:has-text('Contact Us'), "
                    "button:has-text('Get In Touch')"
                )

                if not await submit_locator.count():
                    return {"status": "FAILED", "reason": "Submit button not found"}

                submit = submit_locator.first
                await submit.click(timeout=10000)
                await page.wait_for_timeout(POST_SUBMIT_WAIT_MS)

                # ── 5. Detect result ─────────────────────────────────────
                if await _has_captcha(page):
                    result = {"status": "CAPTCHA", "reason": "CAPTCHA appeared after submission"}
                elif await _has_success(page):
                    result = {"status": "SUCCESS", "reason": "Form submitted successfully"}
                else:
                    result = {"status": "FAILED", "reason": "Could not confirm submission"}

            except PlaywrightTimeoutError:
                result = {"status": "TIMEOUT", "reason": "Timed out during interaction"}
            except Exception as e:
                result = {"status": "ERROR", "reason": str(e)[:120]}
            finally:
                await page.close()
                await context.close()

        return result


async def _has_captcha(page) -> bool:
    try:
        html = await page.content()
        html_low = html.lower()
        if "captcha" in html_low or "verify you are human" in html_low:
            return True
        if await page.locator(
            "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], "
            "iframe[title*='challenge'], .cf-challenge-running"
        ).count():
            return True
    except Exception:
        pass
    return False


async def _has_success(page) -> bool:
    _SUCCESS_SELECTORS = [
        ":has-text('thank you')",
        ":has-text('message sent')",
        ":has-text('successfully submitted')",
        ":has-text('submission successful')",
        ":has-text('your inquiry has been received')",
        ":has-text('we will get back')",
        ":has-text('received your message')",
        ".success-message",
        "#success-message",
        "[class*='success']",
        "[class*='confirmation']",
    ]
    try:
        for sel in _SUCCESS_SELECTORS:
            if await page.locator(sel).count():
                return True
        # Also check if the form disappeared (a common pattern)
    except Exception:
        pass
    return False


def init_semaphore():
    global _tab_semaphore
    _tab_semaphore = asyncio.Semaphore(MAX_GLOBAL_CONCURRENT_TABS)

browser_manager = BrowserManager()
