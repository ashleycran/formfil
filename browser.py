"""
browser.py — Playwright-based async contact-form automation module.

Improvements over the original:
  1  Contact page discovery (common paths + anchor-link scan)
  2  JS-rendered form handling (networkidle + wait_for_selector)
  3  iframe form detection (HubSpot, Typeform, JotForm …)
  4  Shadow DOM detection/logging
  5  Better form scoring (async, richer signals, lower threshold, single-form fallback)
  6  Expanded multi-language _FIELD_MAP
  7  Label-based field classification
  8  Select / dropdown handling (country, salutation, subject, generic)
  9  Privacy / terms checkbox auto-accept
 10  Expanded multi-language submit-button detection
 11  Post-submit URL-change detection
 12  Form-disappearance detection
 13  Anti-bot evasion (random typing delay, scrollIntoView, micro-sleeps)
 14  Validation-error detection after submit
 15  HTTP retry on SSL / TLS failures
"""

import asyncio
import logging
import random
import re
import ipaddress
from urllib.parse import urlparse, urljoin

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from config import MAX_GLOBAL_CONCURRENT_TABS, PAGE_TIMEOUT_MS, POST_SUBMIT_WAIT_MS

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Global semaphore — limits total concurrent browser tabs across ALL users.
# ──────────────────────────────────────────────────────────────────────────────
_tab_semaphore: asyncio.Semaphore | None = None


# ──────────────────────────────────────────────────────────────────────────────
# SSRF / private-IP guard  (kept exactly as original)
# ──────────────────────────────────────────────────────────────────────────────
_BLOCKED_PATTERNS = re.compile(
    r'(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|169\.254\.'
    r'|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+'
    r'|192\.168\.\d+\.\d+)',
    re.IGNORECASE,
)


def _is_safe_url(url: str) -> bool:
    """Return False for private / loopback / link-local targets (SSRF guard)."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if _BLOCKED_PATTERNS.search(host):
            return False
        if host in ("metadata.google.internal", "169.254.169.254"):
            return False
        try:
            addr = ipaddress.ip_address(host)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                return False
        except ValueError:
            pass  # domain name — fine
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Field-classifier helpers  (improvement 6 — expanded multi-language map)
# ──────────────────────────────────────────────────────────────────────────────
_FIELD_MAP: dict[str, list[str]] = {
    "full_name": [
        "full name", "fullname", "your name", "contact name",
        "name", "your name", "ihr name", "dein name", "full name",
    ],
    "first_name": [
        "first name", "firstname", "fname", "given name", "vorname",
        "prenom", "prénom", "voornaam", "nombre",
    ],
    "last_name": [
        "last name", "lastname", "lname", "surname", "family name",
        "nachname", "familienname", "nom", "achternaam", "apellido",
    ],
    "email": [
        "email", "e mail", "email address", "emailaddress",
        "contact email", "your email", "e-mail", "mail", "courriel",
        "e mail",
    ],
    "phone": [
        "phone", "telephone", "tel", "mobile", "cell",
        "mobile number", "contact number", "phone number", "telefon",
        "handy", "mobilnummer", "rufnummer", "telefonnummer",
        "téléphone", "portable", "telefono", "telefoon",
    ],
    "company": [
        "company", "company name", "organization", "organisation",
        "business", "business name", "firma", "unternehmen", "betrieb",
        "firmenname", "gesellschaft", "entreprise", "societe", "société",
        "bedrijf", "empresa",
    ],
    "subject": [
        "subject", "topic", "inquiry subject", "re", "betreff",
        "thema", "sujet", "objet", "asunto", "onderwerp", "titel",
    ],
    "message": [
        "message", "messages", "comment", "comments", "inquiry",
        "description", "your message", "body", "content",
        "nachricht", "mitteilung", "anliegen", "anfrage", "text",
        "demande", "bericht", "mensaje",
    ],
    "website": [
        "website", "url", "web", "site", "homepage", "webseite",
        "internet",
    ],
}

# Input types we never try to fill
_IGNORE_TYPES = frozenset({
    "hidden", "submit", "button", "checkbox", "radio",
    "file", "image", "reset", "color", "range",
})

# Name/id patterns that indicate a non-contact field
_IGNORE_NAMES = re.compile(
    r"(search|password|pass|login|coupon|promo|discount|newsletter"
    r"|date|credit.card|card.number|cvv|zip|postal)",
    re.IGNORECASE,
)


def _classify_field(
    name: str,
    field_id: str,
    placeholder: str,
    aria_label: str,
    autocomplete: str,
    label_text: str = "",   # improvement 7
) -> str | None:
    """Return the profile key that best matches this input, or None.

    Combines all available signal strings, normalises separators to spaces,
    then checks each _FIELD_MAP keyword list.
    """
    combined = " ".join([name, field_id, placeholder, aria_label, autocomplete, label_text]).lower()
    # Normalise separators (-, _, ., /) to space (improvement 6 note)
    combined = re.sub(r"[-_./]", " ", combined)

    for profile_key, keywords in _FIELD_MAP.items():
        for kw in keywords:
            # Keywords already use spaces; just check membership
            if kw in combined:
                return profile_key
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Async form scorer  (improvement 5 — richer scoring, page context)
# ──────────────────────────────────────────────────────────────────────────────
async def _score_form(form, page=None) -> int:
    """Score a <form> element; higher = more likely to be a contact form."""
    score = 0
    try:
        html = (await form.inner_html()).lower()
    except Exception:
        return 0

    # Positive signals
    try:
        if await form.locator("input[type='email']").count():
            score += 8
    except Exception:
        if "email" in html:
            score += 5

    try:
        if await form.locator("textarea").count():
            score += 5
    except Exception:
        pass

    # Text inputs with message/name keywords in attributes
    msg_kws = re.compile(r"(message|nachricht|comment|inquiry|anfrage|demande|bericht|mensaje)")
    if msg_kws.search(html):
        score += 4

    # Form action attribute
    try:
        action = (await form.get_attribute("action") or "").lower()
        if any(k in action for k in ("contact", "mail", "send", "submit", "message")):
            score += 5
    except Exception:
        pass

    # Page-level context (requires page argument)
    if page is not None:
        try:
            title = (await page.title()).lower()
            page_html_snippet = ""
            try:
                # Only inspect headings — cheap
                for sel in ("h1", "h2"):
                    els = await page.locator(sel).all()
                    for el in els[:3]:
                        page_html_snippet += (await el.inner_text()).lower() + " "
            except Exception:
                pass
            context_text = title + " " + page_html_snippet
            if any(k in context_text for k in ("contact", "kontakt", "anfrage", "contactez", "contacto")):
                score += 3
        except Exception:
            pass

    if "name" in html:
        score += 2
    if "phone" in html or "telefon" in html or "téléphone" in html:
        score += 1
    if "submit" in html or "send" in html or "senden" in html or "envoyer" in html:
        score += 2

    # Negative signals
    if "password" in html:
        score -= 10
    if "search" in html:
        score -= 5
    if "payment" in html or "credit" in html:
        score -= 15
    if "login" in html or "signin" in html:
        score -= 8

    return score


# ──────────────────────────────────────────────────────────────────────────────
# Contact page discovery helper  (improvement 1)
# ──────────────────────────────────────────────────────────────────────────────
_CONTACT_PATHS = [
    "/contact",
    "/contact-us",
    "/kontakt",
    "/kontaktformular",
    "/nous-contacter",
    "/contactez-nous",
    "/impressum",
    "/contact.html",
    "/contacto",
    "/get-in-touch",
    "/reach-us",
]

_CONTACT_LINK_KEYWORDS = re.compile(
    r"(contact|kontakt|schreiben|anfrage|contactez|nous contacter"
    r"|get in touch|reach us|write to us)",
    re.IGNORECASE,
)


async def _find_contact_page(page, base_url: str) -> list[str]:
    """Return a prioritised list of candidate contact-page URLs to try."""
    candidates: list[str] = []
    seen: set[str] = set()

    parsed_base = urlparse(base_url)
    origin = f"{parsed_base.scheme}://{parsed_base.netloc}"

    # 1. Common well-known paths
    for path in _CONTACT_PATHS:
        candidate = origin + path
        if _is_safe_url(candidate) and candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)

    # 2. Anchor-link scan on the current page (up to 3 links)
    link_hits: list[str] = []
    try:
        anchors = await page.locator("a[href]").all()
        for a in anchors:
            try:
                href = (await a.get_attribute("href") or "").strip()
                text = (await a.inner_text()).strip()
                if _CONTACT_LINK_KEYWORDS.search(href) or _CONTACT_LINK_KEYWORDS.search(text):
                    full = urljoin(base_url, href)
                    if _is_safe_url(full) and full not in seen:
                        link_hits.append(full)
                        seen.add(full)
                        if len(link_hits) >= 3:
                            break
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"Anchor scan failed: {e}")

    # Prepend link hits — they are more reliable than guessed paths
    return link_hits + candidates


# ──────────────────────────────────────────────────────────────────────────────
# iframe form detection  (improvement 3)
# ──────────────────────────────────────────────────────────────────────────────
async def _find_form_in_frames(page):
    """Search all iframes for a scoreable contact form.

    Returns (form_element, frame) or (None, None).
    """
    try:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                forms = await frame.locator("form").all()
                if not forms:
                    continue
                scored = []
                for f in forms:
                    s = await _score_form(f)
                    scored.append((s, f))
                scored.sort(key=lambda x: x[0], reverse=True)
                best_score, best_form = scored[0]
                if best_score >= 3 or (len(scored) == 1 and best_score >= 0):
                    logger.info(f"Found form in iframe {frame.url!r} with score {best_score}")
                    return best_form, frame
            except Exception as e:
                logger.debug(f"Frame scan error: {e}")
    except Exception as e:
        logger.debug(f"_find_form_in_frames failed: {e}")
    return None, None


# ──────────────────────────────────────────────────────────────────────────────
# Shadow DOM detection  (improvement 4)
# ──────────────────────────────────────────────────────────────────────────────
async def _check_shadow_dom(page) -> int:
    """Return the count of <form> elements hidden inside shadow roots."""
    try:
        shadow_count = await page.evaluate("""
            () => {
                let count = 0;
                document.querySelectorAll('*').forEach(el => {
                    if (el.shadowRoot) {
                        count += el.shadowRoot.querySelectorAll('form').length;
                    }
                });
                return count;
            }
        """)
        return int(shadow_count or 0)
    except Exception:
        return 0


# ──────────────────────────────────────────────────────────────────────────────
# CAPTCHA detector
# ──────────────────────────────────────────────────────────────────────────────
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


# ──────────────────────────────────────────────────────────────────────────────
# Success message detector
# ──────────────────────────────────────────────────────────────────────────────
_SUCCESS_SELECTORS = [
    ":has-text('thank you')",
    ":has-text('thanks')",
    ":has-text('message sent')",
    ":has-text('message received')",
    ":has-text('successfully submitted')",
    ":has-text('submission successful')",
    ":has-text('your inquiry has been received')",
    ":has-text('we will get back')",
    ":has-text('we will be in touch')",
    ":has-text('received your message')",
    ":has-text('your message has been sent')",
    ":has-text('your request has been sent')",
    ":has-text('your request has been received')",
    ":has-text('erfolgreich')",
    ":has-text('vielen dank')",
    ":has-text('danke')",
    ":has-text('nachricht erhalten')",
    ":has-text('merci')",
    ":has-text('envoyé')",
    ":has-text('bien reçu')",
    ":has-text('gracias')",
    ":has-text('bericht ontvangen')",
    ".success-message",
    ".success_message",
    "#success-message",
    "#success_message",
    "[class*='success']",
    "[class*='confirmation']",
    "[class*='thank']",
    "[class*='sent']",
    "[id*='success']",
    "[id*='thank']",
]


async def _has_success(page) -> bool:
    try:
        for sel in _SUCCESS_SELECTORS:
            if await page.locator(sel).count():
                return True
    except Exception:
        pass
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Validation-error detector  (improvement 14)
# ──────────────────────────────────────────────────────────────────────────────
_ERROR_SELECTORS = [
    ".error", ".errors", ".alert-danger", ".alert-error",
    "[class*='error']", "[class*='invalid']", "[class*='alert-danger']",
    ".form-error", ".field-error", ".input-error",
    ":has-text('required')", ":has-text('pflichtfeld')",
    ":has-text('invalid')", ":has-text('ungültig')",
    ":has-text('please fill')", ":has-text('bitte füllen')",
]


async def _has_error_message(page) -> bool:
    """Return True if visible validation-error elements are detected on the page."""
    try:
        for sel in _ERROR_SELECTORS:
            if await page.locator(sel).count():
                return True
    except Exception:
        pass
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Submit button selectors  (improvement 10)
# ──────────────────────────────────────────────────────────────────────────────
SUBMIT_SELECTORS = [
    "button[type='submit']",
    "input[type='submit']",
    "input[type='image']",
    # English
    "button:has-text('Submit')",
    "button:has-text('Send')",
    "button:has-text('Send Message')",
    "button:has-text('Send Inquiry')",
    "button:has-text('Contact Us')",
    "button:has-text('Get In Touch')",
    "button:has-text('Send Request')",
    # German
    "button:has-text('Absenden')",
    "button:has-text('Senden')",
    "button:has-text('Anfrage senden')",
    "button:has-text('Nachricht senden')",
    "button:has-text('Anfrage stellen')",
    "button:has-text('Jetzt anfragen')",
    "button:has-text('Abschicken')",
    "button:has-text('Weiter')",
    # French
    "button:has-text('Envoyer')",
    "button:has-text('Soumettre')",
    "button:has-text('Envoyer le message')",
    "button:has-text('Envoyer la demande')",
    # Spanish
    "button:has-text('Enviar')",
    "button:has-text('Enviar mensaje')",
    # Dutch
    "button:has-text('Verzenden')",
    "button:has-text('Versturen')",
    # Role-based
    "[role='button'][type='submit']",
]


async def _find_submit_button(contact_form, page=None):
    """Return the best submit button locator inside contact_form, or None."""
    for sel in SUBMIT_SELECTORS:
        try:
            loc = contact_form.locator(sel)
            count = await loc.count()
            if count:
                btn = loc.first
                if await btn.is_visible() and await btn.is_enabled():
                    return btn
        except Exception:
            continue

    # Fallback: any button inside the form
    try:
        buttons = await contact_form.locator("button").all()
        if buttons:
            # Prefer the last button (typically "submit" in multi-step forms)
            return buttons[-1]
    except Exception:
        pass

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Core page-load helper with SSL fallback  (improvement 15)
# ──────────────────────────────────────────────────────────────────────────────
async def _safe_goto(page, url: str) -> str:
    """Navigate to url, retrying with http:// on SSL/TLS errors.

    Returns the final URL actually loaded (may differ if SSL fallback triggered).
    """
    try:
        await page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
        return url
    except PlaywrightTimeoutError:
        raise
    except Exception as e:
        err_str = str(e).lower()
        ssl_error = any(k in err_str for k in ("ssl", "certificate", "tls", "err_ssl", "net::err"))
        if ssl_error and url.startswith("https://"):
            http_url = "http://" + url[8:]
            if _is_safe_url(http_url):
                logger.info(f"SSL error on {url!r}, retrying with HTTP")
                try:
                    await page.goto(http_url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
                    return http_url
                except PlaywrightTimeoutError:
                    raise PlaywrightTimeoutError("Page load timeout (HTTP fallback)")
            # http_url would not be safe — re-raise original
        raise


# ──────────────────────────────────────────────────────────────────────────────
# JS-rendered form wait  (improvement 2)
# ──────────────────────────────────────────────────────────────────────────────
async def _wait_for_forms(page) -> None:
    """Give React / Vue / Angular sites time to hydrate their forms."""
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass
    try:
        await page.wait_for_selector("form", timeout=8000)
    except PlaywrightTimeoutError:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Select-element handler  (improvement 8)
# ──────────────────────────────────────────────────────────────────────────────
async def _handle_selects(contact_form, url: str, page) -> None:
    """Fill <select> dropdowns within the form with contextually appropriate values."""
    try:
        selects = await contact_form.locator("select").all()
    except Exception:
        return

    for sel_el in selects:
        try:
            sel_name = (await sel_el.get_attribute("name") or "").lower()
            sel_id   = (await sel_el.get_attribute("id")   or "").lower()
            sel_combined = f"{sel_name} {sel_id}"

            # Country select
            if any(k in sel_combined for k in ("country", "land", "pays", "pais")):
                tld = urlparse(url).hostname.rsplit(".", 1)[-1] if url else ""
                country_map = {
                    "de": ["Germany", "Deutschland", "DE"],
                    "fr": ["France", "FR"],
                    "nl": ["Netherlands", "Nederland", "NL"],
                    "es": ["Spain", "España", "Spanien", "ES"],
                }
                options_to_try = country_map.get(tld, ["Germany", "Deutschland"])
                for opt in options_to_try:
                    try:
                        await sel_el.select_option(label=opt, timeout=2000)
                        break
                    except Exception:
                        pass

            # Title / salutation select
            elif any(k in sel_combined for k in ("salut", "title", "anrede", "gender", "geschlecht")):
                for opt in ("Herr", "Mr", "Mr.", "Monsieur"):
                    try:
                        await sel_el.select_option(label=opt, timeout=2000)
                        break
                    except Exception:
                        pass

            # Subject / department — pick first meaningful option
            elif any(k in sel_combined for k in ("subject", "betreff", "department", "abteilung", "topic", "reason")):
                options = await sel_el.locator("option").all()
                for opt in options:
                    val  = (await opt.get_attribute("value") or "").strip()
                    text = (await opt.inner_text()).strip()
                    if val and val.lower() not in ("", "0", "none", "select") and text:
                        try:
                            await sel_el.select_option(value=val, timeout=2000)
                        except Exception:
                            pass
                        break

            # Generic: skip first (usually placeholder), pick second
            else:
                options = await sel_el.locator("option").all()
                for opt in options[1:]:
                    val = (await opt.get_attribute("value") or "").strip()
                    if val and val not in ("", "0"):
                        try:
                            await sel_el.select_option(value=val, timeout=2000)
                        except Exception:
                            pass
                        break

        except Exception as e:
            logger.debug(f"Select handling skipped: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Checkbox handler  (improvement 9)
# ──────────────────────────────────────────────────────────────────────────────
_PRIVACY_KEYWORDS = [
    "datenschutz", "agb", "terms", "privacy", "agree", "akzeptiere",
    "accept", "consent", "gdpr", "dsgvo", "policy", "richtlinie",
]


async def _handle_checkboxes(contact_form, page) -> None:
    """Auto-accept privacy / terms checkboxes within the form."""
    try:
        checkboxes = await contact_form.locator("input[type='checkbox']").all()
    except Exception:
        return

    for cb in checkboxes:
        try:
            cb_name = (await cb.get_attribute("name") or "").lower()
            cb_id   = (await cb.get_attribute("id")   or "").lower()

            cb_label = ""
            cb_id_val = await cb.get_attribute("id") or ""
            if cb_id_val:
                try:
                    cb_label = await page.locator(f"label[for='{cb_id_val}']").inner_text(timeout=1000)
                except Exception:
                    pass

            cb_combined = f"{cb_name} {cb_id} {cb_label}".lower()

            if any(kw in cb_combined for kw in _PRIVACY_KEYWORDS):
                is_checked = await cb.is_checked()
                if not is_checked:
                    await cb.check(timeout=3000)

        except Exception as e:
            logger.debug(f"Checkbox handling skipped: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Label resolution helper  (improvement 7)
# ──────────────────────────────────────────────────────────────────────────────
async def _get_label_text(inp, page) -> str:
    """Try to find the visible label text associated with a given input element."""
    # 1. <label for="id">
    try:
        input_id = await inp.get_attribute("id") or ""
        if input_id:
            label_text = await page.locator(f"label[for='{input_id}']").inner_text(timeout=2000)
            if label_text:
                return label_text.strip()
    except Exception:
        pass

    # 2. Input wrapped inside a <label>
    try:
        label_text = await inp.evaluate("""el => {
            let node = el.parentElement;
            while (node) {
                if (node.tagName === 'LABEL') return node.innerText;
                node = node.parentElement;
            }
            return '';
        }""")
        if label_text:
            return label_text.strip()
    except Exception:
        pass

    return ""


# ──────────────────────────────────────────────────────────────────────────────
# Core form-fill routine
# ──────────────────────────────────────────────────────────────────────────────
async def _fill_form(contact_form, profile: dict, page, url: str) -> list[str]:
    """Fill all input/textarea fields in contact_form.

    Returns a list of missing-required field names (empty = all filled).
    """
    missing_required: list[str] = []
    inputs = await contact_form.locator("input, textarea").all()

    for inp in inputs:
        try:
            inp_type = (await inp.get_attribute("type") or "text").lower()
            if inp_type in _IGNORE_TYPES:
                continue

            name_attr  = await inp.get_attribute("name")         or ""
            id_attr    = await inp.get_attribute("id")           or ""
            ph         = await inp.get_attribute("placeholder")  or ""
            aria       = await inp.get_attribute("aria-label")   or ""
            autocomp   = await inp.get_attribute("autocomplete") or ""
            required   = (await inp.get_attribute("required")) is not None

            # Skip clearly unrelated fields
            if _IGNORE_NAMES.search(f"{name_attr} {id_attr}"):
                continue

            # Improvement 7: enrich classification with label text
            label_text = await _get_label_text(inp, page)

            key = _classify_field(name_attr, id_attr, ph, aria, autocomp, label_text)
            value = profile.get(key) if key else None

            # Fallback split for first/last name from full_name
            if not value and key == "first_name" and profile.get("full_name"):
                parts = profile["full_name"].split()
                value = parts[0] if parts else None
            if not value and key == "last_name" and profile.get("full_name"):
                parts = profile["full_name"].split()
                value = parts[-1] if len(parts) > 1 else None

            if value:
                str_value = str(value)
                # Improvement 13: scroll into view + micro-sleep
                try:
                    await inp.scroll_into_view_if_needed()
                    await asyncio.sleep(0.05)
                except Exception:
                    pass

                # Improvement 13: human-like typing (but keep fill() for email
                # to avoid mid-type validation triggers)
                if inp_type == "email":
                    await inp.fill(str_value, timeout=5000)
                else:
                    await inp.type(str_value, delay=random.randint(30, 80))

                # Improvement 13: small pause between fields
                await asyncio.sleep(random.uniform(0.05, 0.15))

            elif required:
                missing_required.append(name_attr or id_attr or key or "unknown")

        except Exception as e:
            logger.debug(f"Field fill skipped: {e}")

    return missing_required


# ──────────────────────────────────────────────────────────────────────────────
# Attempt to process a single page (find form, fill, submit)
# ──────────────────────────────────────────────────────────────────────────────
async def _try_page(page, url: str, profile: dict) -> dict | None:
    """Attempt to find, fill, and submit a contact form on the current page.

    Returns a result dict if decisive (SUCCESS / CAPTCHA / MISSING_INFORMATION /
    error conditions), or None if no good form was found.
    """
    # Improvement 2: wait for JS-rendered forms
    await _wait_for_forms(page)

    # Early CAPTCHA check
    if await _has_captcha(page):
        return {"status": "CAPTCHA", "reason": "CAPTCHA on page load"}

    # ── Find best contact form ─────────────────────────────────────────────
    contact_form = None
    frame = None  # None means main page

    # a) Main page forms
    forms = await page.locator("form").all()
    if forms:
        scored = []
        for f in forms:
            s = await _score_form(f, page)
            scored.append((s, f))
        scored.sort(key=lambda x: x[0], reverse=True)

        best_score, best_form = scored[0]
        # Improvement 5: threshold 3, single-form fallback
        if best_score >= 3:
            contact_form = best_form
        elif len(scored) == 1 and best_score >= 0:
            logger.info(f"Single-form fallback (score={best_score}) on {url!r}")
            contact_form = best_form

    # b) Improvement 3: iframe search if still no form
    if contact_form is None:
        iframe_form, iframe_frame = await _find_form_in_frames(page)
        if iframe_form is not None:
            contact_form = iframe_form
            frame = iframe_frame

    # c) Improvement 4: log shadow DOM presence
    if contact_form is None:
        shadow_count = await _check_shadow_dom(page)
        if shadow_count > 0:
            logger.info(f"Shadow DOM contains {shadow_count} form(s) on {url!r} — cannot auto-fill")

    if contact_form is None:
        return None  # signal: no good form on this page

    # ── Fill text/email/textarea fields ───────────────────────────────────
    # Use the correct page-like object for label lookups (frame or page)
    label_page = frame if frame is not None else page
    missing_required = await _fill_form(contact_form, profile, label_page, url)

    # Improvement 8: handle select dropdowns
    await _handle_selects(contact_form, url, label_page)

    # Improvement 9: handle privacy checkboxes
    await _handle_checkboxes(contact_form, label_page)

    if missing_required:
        return {
            "status": "MISSING_INFORMATION",
            "reason": f"Required fields missing: {', '.join(missing_required[:5])}",
        }

    # ── Find submit button ─────────────────────────────────────────────────
    submit = await _find_submit_button(contact_form, page)
    if submit is None:
        return {"status": "FAILED", "reason": "Submit button not found"}

    # Improvement 11: record URL before submit
    url_before = page.url

    # Improvement 12: check form visibility before submit
    try:
        form_visible_before = await contact_form.is_visible()
    except Exception:
        form_visible_before = True

    # Improvement 13: scroll to button + pre-click pause
    try:
        await submit.scroll_into_view_if_needed()
        await asyncio.sleep(random.uniform(0.1, 0.3))
    except Exception:
        pass

    await submit.click(timeout=10000)
    await page.wait_for_timeout(POST_SUBMIT_WAIT_MS)

    # ── Post-submit signal detection ───────────────────────────────────────

    # Improvement 11: URL change detection
    url_after = page.url
    if url_before != url_after:
        path = urlparse(url_after).path.lower()
        success_paths = [
            "thank", "danke", "merci", "success", "confirm",
            "submit", "sent", "done", "erfolgreich",
        ]
        if any(p in path for p in success_paths):
            return {"status": "SUCCESS", "reason": "Redirected to success page"}
        return {"status": "SUCCESS", "reason": f"Form redirect to {url_after[:80]}"}

    # Improvement 12: form disappearance
    try:
        form_visible_after = await contact_form.is_visible()
    except Exception:
        form_visible_after = False
    if form_visible_before and not form_visible_after:
        return {"status": "SUCCESS", "reason": "Form disappeared after submission"}

    # CAPTCHA appeared after submit?
    if await _has_captcha(page):
        return {"status": "CAPTCHA", "reason": "CAPTCHA appeared after submission"}

    # Generic success message?
    if await _has_success(page):
        return {"status": "SUCCESS", "reason": "Form submitted successfully"}

    # Improvement 14: validation errors?
    if await _has_error_message(page):
        return {"status": "FAILED", "reason": "Form validation error — data rejected"}

    return {"status": "FAILED", "reason": "Could not confirm submission"}


# ──────────────────────────────────────────────────────────────────────────────
# BrowserManager  (public class — interface unchanged)
# ──────────────────────────────────────────────────────────────────────────────
class BrowserManager:
    """Manages a single shared Chromium browser instance with async safety."""

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
                        "--ignore-certificate-errors",
                        "--ignore-ssl-errors",
                        "--allow-running-insecure-content",
                        "--disable-web-security",
                    ],
                )

    async def stop(self):
        """Gracefully stop the browser and playwright instance."""
        async with self._lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None

    async def process_website(self, url: str, profile: dict) -> dict:
        """Open url, find and fill the best contact form, submit it.

        Returns {"status": <STATUS_STRING>, "reason": <str>}.

        Status strings: SUCCESS | FAILED | ERROR | TIMEOUT | NO_FORM |
                        CAPTCHA | MISSING_INFORMATION | BLOCKED
        Profile keys  : full_name, first_name, last_name, email, phone,
                        company, website, subject, message
        """
        global _tab_semaphore

        # ── Safety check ──────────────────────────────────────────────────
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
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                java_script_enabled=True,
                ignore_https_errors=True,
            )
            page = await context.new_page()

            try:
                # ── 1. Navigate (improvement 15: SSL fallback) ─────────────
                try:
                    url = await _safe_goto(page, url)
                except PlaywrightTimeoutError:
                    return {"status": "TIMEOUT", "reason": "Page load timeout"}
                except Exception as e:
                    return {"status": "ERROR", "reason": str(e)[:120]}

                # ── 2–7. Try to fill form on the landing page ──────────────
                result = await _try_page(page, url, profile)
                if result is not None:
                    return result

                # ── 8. Improvement 1: contact page discovery ───────────────
                candidates = await _find_contact_page(page, url)
                logger.info(f"No form on landing page; trying {len(candidates)} contact page(s)")

                for candidate_url in candidates:
                    try:
                        candidate_url = await _safe_goto(page, candidate_url)
                    except PlaywrightTimeoutError:
                        logger.debug(f"Timeout loading {candidate_url!r}")
                        continue
                    except Exception as e:
                        logger.debug(f"Error loading {candidate_url!r}: {e}")
                        continue

                    result = await _try_page(page, candidate_url, profile)
                    if result is not None:
                        return result

                # ── 9. Nothing worked ──────────────────────────────────────
                return {"status": "NO_FORM", "reason": "No contact form found on this site"}

            except PlaywrightTimeoutError:
                return {"status": "TIMEOUT", "reason": "Timed out during interaction"}
            except Exception as e:
                return {"status": "ERROR", "reason": str(e)[:120]}
            finally:
                await page.close()
                await context.close()


# ──────────────────────────────────────────────────────────────────────────────
# Module-level initialisation helpers
# ──────────────────────────────────────────────────────────────────────────────
def init_semaphore():
    """Initialise the global tab semaphore.  Must be called inside a running event loop."""
    global _tab_semaphore
    _tab_semaphore = asyncio.Semaphore(MAX_GLOBAL_CONCURRENT_TABS)


browser_manager = BrowserManager()
