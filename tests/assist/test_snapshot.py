"""The offline copy of a filled form."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoapp.assist.filler import fill_page
from autoapp.assist.snapshot import MARKER, NAME, save_snapshot, snapshot_html

browser = pytest.mark.browser


@browser
def test_the_copy_holds_the_filled_values_and_an_absolute_form_action(page, forms: Path, values) -> None:
    page.goto((forms / "plain.html").as_uri())
    fill_page(page, values, restricted_domains=[])
    page.evaluate("document.querySelector('#quelle').value = 'Über LinkedIn <b>'")
    html = snapshot_html(page.main_frame, url=page.url, title="Dev at Muster",
                         files=[("CV", "file:///tmp/cv.pdf")])

    assert "<script" not in html.lower()
    assert 'value="Max"' in html and "max@example.com" in html
    assert "Über LinkedIn &lt;b&gt;" in html  # escaped, as the textarea content
    assert 'href="file:///tmp/cv.pdf"' in html and "Offline copy of the application form for Dev at Muster" in html
    assert 'action="file:///' in html  # absolute
    # the live page is untouched
    assert page.evaluate("document.querySelectorAll('script').length") > 0
    assert page.input_value("#vorname") == "Max"

    # opened offline, the copy shows the same values
    out = save_snapshot(values.cv.parent, html, page.url)
    page.goto(out.as_uri())
    assert page.input_value("#vorname") == "Max"
    assert page.input_value("#quelle") == "Über LinkedIn <b>"


@browser
def test_checkbox_and_select_state_are_kept(page) -> None:
    page.set_content('<form action="/go" method="post"><input type="checkbox" id="c"><select id="s">'
                     '<option value="1">A</option><option value="2">B</option></select></form>')
    page.check("#c")
    page.select_option("#s", "2")
    html = snapshot_html(page.main_frame, url="https://example.com/form", title="X", files=[])
    page.set_content(html)
    assert page.is_checked("#c") and page.input_value("#s") == "2"
    assert page.evaluate("document.querySelector('form').method") == "post"


def test_a_copy_of_another_page_is_kept_not_overwritten(tmp_path: Path) -> None:
    save_snapshot(tmp_path, f"<html>one</html>\n{MARKER}https://x.test/step1 -->\n", "https://x.test/step1")
    save_snapshot(tmp_path, f"<html>one again</html>\n{MARKER}https://x.test/step1 -->\n", "https://x.test/step1")
    assert len(list(tmp_path.glob("filled_form*.html"))) == 1  # same page: replaced
    save_snapshot(tmp_path, f"<html>two</html>\n{MARKER}https://x.test/step2 -->\n", "https://x.test/step2")
    names = sorted(p.name for p in tmp_path.glob("filled_form*.html"))
    assert len(names) == 2 and NAME in names
    assert "two" in (tmp_path / NAME).read_text(encoding="utf-8")
