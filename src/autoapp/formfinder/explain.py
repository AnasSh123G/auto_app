"""Plain-language explanations of a form status for the person filling the form.

The stored notes (``form_note``) stay short and technical; everything shown in the terminal while
choosing or filling a form goes through :func:`explain`, which says what happened and what to do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Explanation:
    short: str  # a few words for the list of forms
    what: str  # what happened
    todo: str  # what to do next

    def lines(self) -> list[str]:
        return [self.what, f"What to do: {self.todo}"]


YOU_CLICK = ("In the browser window, click the apply button yourself. When the application form is showing, "
             "press [f] here and the app fills it.")

_PARTIAL = (
    ("navigation loop", "The page kept leading back to where it started, so the app stopped."),
    ("ambiguous links", "The page has several possible apply links and the app could not tell which one is right."),
    ("no apply button", "The app found no apply button it can safely click on this page."),
    ("no unambiguous safe navigation", "The app found no apply button it can safely click on this page."),
    ("no usable application continuation", "The app found no apply button it can safely click on this page."),
    ("untracked popup", "The apply button opens a new window, which the app does not follow on its own."),
    ("submission refused", "The next step would send data to the site, which the app never does on its own."),
    ("no form within", "The app followed {steps} pages without reaching an application form."),
    ("no way to apply online", "The app looked through the site and found no way to apply online{reason}."),
    ("the model sees a form", "The page seems to show the application form, but the app could not confirm it."),
    ("conflicting", "The form on this page seems to belong to a different job or company."),
    ("no usable application entry", "The job has no link the app can open."),
)


def explain(status: str | None, note: str | None = "") -> Explanation:
    """What a form status means and what to do, in words for the user (not for the log)."""
    note = note or ""
    if status == "found":
        return Explanation("form found", "The application form was found.",
                           "Press [f] to fill it, check everything, then submit it yourself.")
    if status in (None, ""):
        return Explanation("not checked yet", "The app has not looked for this application form yet.",
                           "Open it; the app looks for the form first.")
    if status == "login_required":
        return Explanation("needs your login", "The site asks you to log in or create an account first.",
                           "Log in in the browser window (it is remembered next time), open the application, "
                           "then press [f].")
    if status == "captcha":
        return Explanation("robot check", "The site shows a robot check (CAPTCHA), which the app never solves.",
                           "Solve it in the browser window, then press [f] once the form is showing.")
    if status == "needs_browser":
        return Explanation("blocked the quick check",
                           "The site refused the app's quick background check, which often still works in a real browser window.",
                           "Open it; if the page loads, click through to the form yourself and press [f].")
    if status == "no_employer_link":
        reason = note.split(":", 1)[1].strip() if ":" in note else ""
        return Explanation("employer ad not found",
                           "The job is on the Arbeitsagentur, but the app could not find the employer's own ad or "
                           "application page for it" + (f" ({reason})." if reason else "."),
                           "Your cover letter is ready. Open the job in your browser: if it has an apply link to the "
                           "employer, paste that address here and the app opens and fills the form. The app looks "
                           "again on later searches.")
    if status == "restricted" and "only on the Arbeitsagentur" in note:
        return Explanation("apply on the Arbeitsagentur",
                           "This ad is posted only on the Arbeitsagentur; the employer's own site has no ad for it.",
                           "Apply on the Arbeitsagentur page yourself, using the answers in apply.html.")
    if status == "restricted" and "employer ad not searched yet" in note:
        why = note.split("employer ad not searched yet:", 1)[1].strip()
        return Explanation("employer ad not searched",
                           "The job is on the Arbeitsagentur. The app has not looked for the employer's own ad yet "
                           f"({why}).",
                           "Fix that and search again (the app then finds the employer's application page), or open the "
                           "job in your browser and paste the employer's application address here.")
    if status == "restricted":
        host = re.search(r"restricted target: ([^\s;]+)", note)
        site = host.group(1).removeprefix("www.") if host else "this site"
        reason = note.split(";", 1)[1].strip() if ";" in note else ""
        todo = ("Open the job in your normal browser. If it has 'Apply on company website', click it and paste "
                "that address here: the app then opens and fills the employer's form. Otherwise apply there "
                "yourself with the answers in apply.html.")
        if "only takes applications on" in reason:
            return Explanation("apply on the site", f"The app read the job on {site} with your login: {reason}.",
                               "Apply there yourself (for example with Easy Apply), using the answers in apply.html.")
        if reason:
            return Explanation("apply yourself", f"The job is on {site}. Reading its company apply link with your "
                                                 f"login did not work: {reason}.", todo)
        return Explanation("apply yourself",
                           f"The job is on {site}, which the app never opens: these sites block or ban accounts that use "
                           "automated browsers (your list is assist.restricted_domains). The employer's own site was "
                           "not found for it either.",
                           todo + " You can also let the app read the company apply link with your login "
                           "(Settings > sources; off by default, against the site's terms).")
    if status == "unreachable":
        code = re.search(r"HTTP (\d{3})", note)
        gone = code and code.group(1) in ("404", "410")
        return Explanation("ad gone" if gone else "page not loading",
                           "The job page no longer exists, so the ad was probably taken down." if gone else
                           "The job page could not be loaded" + (f" (HTTP {code.group(1)})." if code else "."),
                           "Check the link in your own browser. If the job is gone, mark it as skipped.")
    for key, what in _PARTIAL:
        if key in note:
            steps = re.search(r"no form within (\d+)", note)
            count = steps.group(1) if steps else "4"
            reason = note.split(":", 1)[1].strip() if key == "no way to apply online" and ":" in note else ""
            what = what.format(steps={"4": "four"}.get(count, count), reason=f" ({reason})" if reason else "")
            todo = ("If the form is showing, press [f]; otherwise click through to it yourself and press [f]."
                    if key == "the model sees a form" else YOU_CLICK)
            return Explanation("click through yourself", what, todo)
    return Explanation("click through yourself", "The app did not find the application form on this page.", YOU_CLICK)
