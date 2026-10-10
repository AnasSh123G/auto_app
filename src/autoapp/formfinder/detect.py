import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

APPLICATION = re.compile(r"bewerb|application|apply|lebenslauf|resume|curriculum", re.I)
EXCLUDED = re.compile(r"newsletter|subscribe|kontaktformular|\bkontakt\b|\bcontact\b|search", re.I)
NAME = re.compile(r"vorname|nachname|first.?name|last.?name|full.?name|\bname\b", re.I)
EMAIL = re.compile(r"e.?mail", re.I)


@dataclass(frozen=True)
class Detection:
    status: str | None = None
    note: str = ""
    scope: str | None = None


def usable(control: Tag) -> bool:
    if control.get("type", "").lower() in ("hidden", "submit", "image", "button", "reset"):
        return False
    for node in [control, *control.parents]:
        if node.has_attr("hidden") or node.has_attr("disabled") or node.get("aria-hidden") == "true":
            return False
        if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", str(node.get("style", "")), re.I):
            return False
    return True


def detect(html: str, *, reference: str | None = None, company: str | None = None) -> Detection:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("script, style, nav, header, footer"):
        node.decompose()
    text = soup.get_text(" ", strip=True)
    logins = [node for node in soup.select('input[type="password"]') if usable(node)]
    if logins:
        # A customer-portal login box on an ad page (chefsculinar.de) is not a login wall: the rest is read on.
        for node in logins:
            (node.find_parent("form") or node).decompose()
        text = soup.get_text(" ", strip=True)
        if len(text) < 800:
            return Detection("login_required", "login or account creation required")
    if re.search(r"just a moment|verify you are human|checking your browser|cf-chl-|challenge-platform", html, re.I):
        return Detection("captcha", "blocking anti-bot challenge")
    if not soup.select_one("input, textarea, select") and re.search(r"recaptcha|hcaptcha", html, re.I):
        return Detection("captcha", "blocking CAPTCHA challenge")
    if re.search(r"create account|konto erstellen|sign in|anmelden|registrieren", text, re.I) and not APPLICATION.search(text):
        return Detection("login_required", "login or account creation required")
    for attr, expected in (("data-job-reference", reference), ("data-company", company)):
        identified = soup.select_one(f"[{attr}]")
        if expected and identified and str(identified.get(attr)).casefold() != expected.casefold():
            return Detection("partial", f"conflicting {attr.removeprefix('data-')}")
    identity = re.search(r"(?:referenz(?:nummer)?|job.?reference|job.?id)\s*[:#]\s*([\w.-]+)", text, re.I)
    if reference and identity and identity.group(1).casefold() != reference.casefold():
        return Detection("partial", "conflicting job reference")
    candidates = soup.select("form, [data-application], [role='form']")
    if not candidates and soup.body and not EXCLUDED.search(text):
        candidates = [soup.body]
    for index, node in enumerate(candidates):
        if not isinstance(node, Tag):
            continue
        context = " ".join([node.get_text(" ", strip=True), str(node.get("id", "")), str(node.get("action", ""))])
        heading = node.find(["h1", "h2", "h3", "legend"])
        identity = " ".join([str(node.get("id", "")), str(node.get("action", "")),
                             heading.get_text(" ", strip=True) if heading else ""])
        if EXCLUDED.search(identity) or (EXCLUDED.search(context) and not APPLICATION.search(context)):
            continue
        controls = [control for control in node.select("input, textarea, select") if usable(control)]
        fields = []
        for control in controls:
            label = soup.find("label", attrs={"for": control.get("id")}) if control.get("id") else None
            fields.append(" ".join([str(control.get("name", "")), str(control.get("id", "")),
                                    str(control.get("type", "")), label.get_text(" ", strip=True) if label else ""]))
        upload = any(control.get("type", "").lower() == "file" for control in controls)
        if not upload and not (any(NAME.search(f) for f in fields) and any(EMAIL.search(f) for f in fields)):
            continue
        if not APPLICATION.search(context) and not APPLICATION.search(text):
            continue
        if node.name == "body":
            scope = "body"
        else:
            # The browser adapter assigns a stable scope attribute to every candidate.
            scope = f'[data-autoapp-scope="{node.get("data-autoapp-scope", index)}"]'
        return Detection("found", "application form found", scope)
    return Detection()
