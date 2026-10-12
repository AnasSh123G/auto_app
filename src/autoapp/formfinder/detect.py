import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from autoapp.employers.hints import clean_title, short_name

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


# A heading is identity evidence only when it names a role or explicitly names
# the vacancy/employer. Generic form and personal-information headings are neutral.
_ROLE = re.compile(r"\b(?:developer|engineer|assistant|manager|consultant|analyst|designer|"
                   r"specialist|administrator|architect|technician|intern|entwickler|ingenieur|"
                   r"assistent|berater|werkstudent|praktikant|fachkraft|sachbearbeiter)\w*\b", re.I)


def _heading_conflict(soup, title: str | None, company: str | None) -> bool:
    if not title:
        return False
    wanted = set(re.findall(r"\w+", clean_title(title).casefold()))
    for node in soup.find_all("h1"):
        heading = clean_title(node.get_text(" ", strip=True))
        employer = re.search(r"\b(?:at|bei)\s+(.+)$", heading, re.I)
        if company and employer:
            actual = set(re.findall(r"\w+", short_name(employer.group(1)).casefold()))
            expected = set(re.findall(r"\w+", short_name(company).casefold()))
            if actual and actual not in ({"uns"}, {"us"}, {"our", "company"}) and actual != expected:
                return True
        words = set(re.findall(r"\w+", heading.casefold()))
        vacancy = re.search(r"(?:application|apply|bewerbung)\s+(?:for|für|als)\s+(.+?)(?:\s+(?:at|bei)\s+|$)", heading, re.I)
        named = set(re.findall(r"\w+", vacancy.group(1).casefold())) if vacancy else set()
        generic = {"this", "the", "a", "an", "our", "your", "any", "open", "available", "advertised",
                   "job", "role", "position", "vacancy", "diese", "diesen", "die", "der", "das", "eine",
                   "einen", "unsere", "offene", "ausgeschriebene", "stelle", "stellen"}
        if ((_ROLE.search(heading) and not wanted <= words)
                or (named - generic and not wanted <= named)):
            return True
    return False


def _creates_account(text: str) -> bool:
    # Evaluate one clause at a time: an email alternative in the next clause
    # cannot negate what clicking Continue does.
    pattern = r"(?:mit (?:dem )?klick|by clicking|by continuing).{0,180}?(?:erstellst|erstellen|creat\w*).{0,100}?(?:profil|konto|account|profile)"
    negative = (r"without(?: needing to| having to| the need to)?\s+creat\w*|"
                r"(?:do not|don't|not|never)\s+creat\w*|(?:ohne|nicht)\s+(?:zu\s+)?erstell\w*")
    account_free = re.search(r"(?:apply|bewerben|bewerbung).{0,50}(?:without|ohne).{0,30}(?:account|profile|konto|profil)", text, re.I)
    for clause in re.split(r"[.;!?]", text):
        if account_free and re.search(r"by clicking\s+[\"'‘’“”]*(?:sign up|register)|mit (?:dem )?klick.{0,20}registrier", clause, re.I):
            continue
        for match in re.finditer(pattern, clause, re.I):
            if not re.search(negative, match.group(), re.I):
                return True
    return False


def detect(html: str, *, reference: str | None = None, company: str | None = None,
           title: str | None = None, visible_text: str | None = None) -> Detection:
    soup = BeautifulSoup(html, "html.parser")
    styled = bool(soup.select_one("style, link[rel=stylesheet]"))
    for node in soup.select("script, style, nav, header, footer"):
        node.decompose()
    for node in list(soup.find_all(True)):
        if node.attrs is not None and (node.has_attr("hidden") or node.has_attr("inert") or node.get("aria-hidden") == "true"
                                      or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", str(node.get("style", "")), re.I)):
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
    if (not soup.select_one("input, textarea, select")
            and soup.select_one('.g-recaptcha, .hcaptcha, .h-captcha, iframe[src*="recaptcha"], iframe[src*="hcaptcha"]')):
        return Detection("captcha", "blocking CAPTCHA challenge")
    account_text = text if visible_text is None else " ".join(visible_text.split())
    if re.search(r"create account|konto erstellen|sign in|anmelden|registrieren", account_text, re.I) and not APPLICATION.search(account_text):
        return Detection("login_required", "login or account creation required")
    if _creates_account(account_text):
        if styled and visible_text is None:
            return Detection("needs_browser", "account/profile requirement needs rendered confirmation")
        return Detection("login_required", "continuing creates an account/profile; continue manually")
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
        if _heading_conflict(soup, title, company):
            return Detection("partial", "conflicting vacancy heading; check the job manually")
        if node.name == "body":
            scope = "body"
        else:
            # The browser adapter assigns a stable scope attribute to every candidate.
            scope = f'[data-autoapp-scope="{node.get("data-autoapp-scope", index)}"]'
        return Detection("found", "application form found", scope)
    return Detection()
