"""Smoke tests for autoapp."""

import pytest


def test_import_autoapp():
    """Test that autoapp can be imported and has a __version__ string."""
    import autoapp

    assert isinstance(autoapp.__version__, str)


@pytest.mark.live(env="AUTOAPP_DEFINITELY_UNSET_VAR")
def test_live_unset_var():
    """Test marked as live with an unset environment variable.

    This test should be skipped, not run.
    """
    raise AssertionError("This should not be executed")


@pytest.mark.browser
def test_browser_chromium():
    """Test that Playwright can launch Chromium and set page content."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.set_content("<p>ok</p>")
        text = page.text_content("p")

        assert text == "ok"

        browser.close()
