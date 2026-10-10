"""Regressions from the first live discovery run (task 6.3), as small synthetic pages.

Each case reproduces a page shape seen on a real employer site; none of the real pages is copied.
"""

from __future__ import annotations

from autoapp.employers import listings as L
from autoapp.employers.match import SeedFacts, match_seed, place_key
from autoapp.employers.repo import Listing
from autoapp.employers.verify import Candidate, verify_candidates
from tests.employers.webfake import FakeWeb

# --- listings -----------------------------------------------------------------------------------


def test_static_listing_inside_noscript_is_read():
    html = ("<html><body><div id='__nuxt'></div><noscript><ul>"
            "<li><a href='https://careers.acme.example/jobs/ai-engineer/ab12'>AI Engineer (d/w/m) - im Bereich Consulting</a></li>"
            "<li><a href='https://careers.acme.example/jobs/analyst/cd34'>Supply Chain Analyst (d/w/m)</a></li>"
            "</ul></noscript></body></html>")
    titles = [x.title for x in L.listing_links(html, "https://careers.acme.example/search")]
    assert titles == ["AI Engineer (d/w/m) - im Bereich Consulting", "Supply Chain Analyst (d/w/m)"]


def test_job_cards_with_document_links_and_apply_for_place_buttons():
    card = ("<div class='c-job c-job--card'><div class='c-job__title'>{t}</div>"
            "<a class='c-job__action' href='/wp-content/uploads/{f}.pdf'><span>Bewerben für</span> <span>{c}</span></a></div>")
    html = "<html><body>" + card.format(t="AI &amp; Digital Solutions Engineer (m/w/d) für die Acme Gruppe", f="a", c="St. Ingbert") \
        + card.format(t="AI &amp; Digital Solutions Engineer (m/w/d) für die Acme Gruppe", f="b", c="Baiersdorf") + "</body></html>"
    found = L.listing_links(html, "https://www.acme.example/jobs/")
    assert [(x.title, x.location) for x in found] == [
        ("AI & Digital Solutions Engineer (m/w/d) für die Acme Gruppe", "St. Ingbert"),
        ("AI & Digital Solutions Engineer (m/w/d) für die Acme Gruppe", "Baiersdorf")]


def test_categories_stories_and_city_pages_are_not_openings():
    html = ("<html><body><main>"
            "<a href='/jobs/sales-jobs'>Sales</a><a href='/jobs/engineering-jobs'>Engineering</a>"
            "<a href='/de/jobs/muenchen'>München München</a>"
            "<a href='/karriere/mitarbeitenden-stories/story-myriam/'>Mitarbeitenden Story – Myriam</a>"
            "<a href='/karriere/benefits/'>Zu den Benefits</a>"
            "<a href='/jobs/backend-entwickler'>Backend-Entwickler*in</a>"
            "</main></body></html>")
    assert [x.title for x in L.listing_links(html, "https://acme.example/")] == ["Backend-Entwickler*in"]


def test_blog_feed_entries_are_not_openings():
    feed = ("<rss><channel><item><title>Pressemitteilung: neues Projekt</title><link>https://acme.example/news/1</link></item>"
            "<item><title>Data Engineer (m/w/d)</title><link>https://acme.example/jobs/7</link></item></channel></rss>")
    assert [x.title for x in L.parse_feed(feed, "https://acme.example/")] == ["Data Engineer (m/w/d)"]


# --- matching -----------------------------------------------------------------------------------


def test_linkedin_metropolitan_area_matches_the_german_city():
    assert place_key("Greater Munich Metropolitan Area") == place_key("München") == "muenchen"
    assert place_key("Cologne, North Rhine-Westphalia, Germany") == place_key("Köln")
    listing = Listing(url="https://acme.example/j3102.html", title="Softwareentwickler Java (m/w/d)", locations=["München"])
    res = match_seed(SeedFacts("Softwareentwickler Java (m/w/d)", "Greater Munich Metropolitan Area"), [listing])
    assert res.outcome == "matched"


def test_title_suffix_naming_the_employer_does_not_block_a_match():
    listings = [Listing(url=f"https://acme.example/{c}.pdf", title="AI & Digital Solutions Engineer (m/w/d) für die Acme Gruppe",
                        locations=[c]) for c in ("St. Ingbert", "Baiersdorf")]
    res = match_seed(SeedFacts("AI & Digital Solutions Engineer (m/w/d)", "St. Ingbert", company="Acme GmbH"), listings)
    assert (res.outcome, res.listing.url) == ("matched", "https://acme.example/St. Ingbert.pdf")
    other = match_seed(SeedFacts("AI & Digital Solutions Engineer (m/w/d)", "St. Ingbert", company="Other GmbH"), listings)
    assert other.outcome == "none"  # only the employer's own name is dropped


# --- identity -----------------------------------------------------------------------------------


def _site(host: str, name: str, city: str, *, legal_host: str | None = None, body: str = "") -> dict[str, str]:
    legal = f"https://{legal_host or host}/impressum"
    return {f"https://{host}/": f"<html><body><h1>{name}</h1>{body}<a href='{legal}'>Impressum</a></body></html>",
            legal: f"<html><body>Impressum: {name}, Hauptstr. 1, 12345 {city}</body></html>"}


def _verify(web: FakeWeb, urls: list[str], company: str, city: str | None, title: str | None = None):
    return verify_candidates(web, [Candidate(u) for u in urls], names=[company], city=city, title=title, max_pages=20)


def test_a_job_ad_with_contact_in_its_address_is_not_a_legal_page():
    web = FakeWeb({"https://board.example/": "<html><body>Hyra jobs in Köln "
                   "<a href='/jobs/contact-channel-lead-1'>Contact Channel Lead</a></body></html>",
                   "https://board.example/jobs/contact-channel-lead-1": "<html><body>Hyra Köln</body></html>"})
    res = _verify(web, ["https://board.example/"], "Hyra", "Köln")
    assert res.status == "unverified"
    assert "https://board.example/jobs/contact-channel-lead-1" not in web.calls


def test_careers_subdomain_may_link_the_legal_notice_of_its_main_domain():
    web = FakeWeb(_site("karriere.vk.example", "Versorgungskammer AöR", "München", legal_host="www.vk.example"))
    res = _verify(web, ["https://karriere.vk.example/"], "Versorgungskammer", "Greater Munich Metropolitan Area")
    assert res.status == "verified" and res.evidence["legal_page"] == "https://www.vk.example/impressum"


def test_hosts_of_one_company_are_not_namesakes():
    pages = {**_site("careers.flow.example", "Flow GmbH", "Berlin", legal_host="www.flow.de"),
             **_site("www.flow.example", "Flow GmbH", "Berlin"), **_site("karriere.flow.de", "Flow GmbH", "Berlin")}
    web = FakeWeb(pages)
    res = _verify(web, ["https://careers.flow.example/", "https://www.flow.example/", "https://karriere.flow.de/"],
                  "Flow", "Düsseldorf")
    assert res.status == "unverified" and "no independent evidence" in res.reason  # not "ambiguous"


def test_vacancy_on_the_careers_site_verifies_without_the_city():
    pages = _site("careers.flow.example", "Flow GmbH", "Berlin", legal_host="www.flow.de",
                  body="<a href='/search'>Jobs</a>")
    pages["https://careers.flow.example/search"] = ("<html><body><noscript><a href='/jobs/x/1'>AI Engineer (d/w/m) - im Bereich "
                                                    "Consulting</a></noscript></body></html>")
    res = _verify(FakeWeb(pages), ["https://careers.flow.example/"], "Flow", "Düsseldorf",
                  "AI Engineer (d/w/m) - im Bereich Consulting")
    assert res.status == "verified" and res.evidence["corroboration"]["kind"] == "vacancy_title"


def test_namesake_in_the_same_town_loses_to_the_domain_named_like_the_employer():
    pages = {**_site("www.mittwald.example", "Mittwald CM Service GmbH", "Espelkamp"),
             **_site("www.mittwaldhotel.example", "Hotel Mittwald", "Espelkamp")}
    res = _verify(FakeWeb(pages), ["https://www.mittwaldhotel.example/", "https://www.mittwald.example/"],
                  "mittwald", "Espelkamp")
    assert res.status == "verified" and res.website == "https://www.mittwald.example/"


def test_a_recruiting_platform_is_not_the_hiring_employer():
    web = FakeWeb(_site("www.talentco.example", "TalentCo GmbH", "Frankfurt",
                        body="<p>TalentCo ist die Recruiting-Plattform mit kuratiertem Talentmarktplatz.</p>"))
    res = _verify(web, ["https://www.talentco.example/"], "TalentCo", "Frankfurt")
    assert res.status == "unverified" and "recruiting company or job platform" in res.reason


def test_a_job_category_called_legal_is_not_a_legal_page():
    web = FakeWeb({"https://board.example/": "<html><body>Hyra Software Engineer Köln <a href='/jobs/legal-jobs'>Legal</a></body></html>",
                   "https://board.example/jobs/legal-jobs": "<html><body>Hyra legal jobs in Köln</body></html>"})
    assert _verify(web, ["https://board.example/"], "Hyra", "Köln", "Software Engineer").status == "unverified"
    assert "https://board.example/jobs/legal-jobs" not in web.calls


def test_session_ids_do_not_make_one_posting_two():
    html = ("<html><body><ul class='joblist'>"
            "<li><a href='/Softwareentwickler-Java-mwd-de-j3102.html?sid=abc'>Softwareentwickler Java (m/w/d)</a></li>"
            "<li><a href='/Softwareentwickler-Java-mwd-de-j3102.html?sid=def'>Softwareentwickler Java (m/w/d)</a></li>"
            "</ul></body></html>")
    [link] = L.listing_links(html, "https://karriere.vk.example/")
    assert link.url == "https://karriere.vk.example/Softwareentwickler-Java-mwd-de-j3102.html"
    assert L.stable_url("https://x.example/job?id=7&sid=1#top") == "https://x.example/job?id=7"


def test_a_gender_tag_in_the_location_slot_is_not_a_place():
    html = ("<html><body><div class='job'><h3>DevOps Engineer</h3><span class='job-location'>(m/w/d)</span>"
            "<a href='/karriere/job/devops-engineer-1'>DevOps Engineer</a></div></body></html>")
    [link] = L.listing_links(html, "https://www.acme.example/karriere/")
    assert link.location is None


def test_a_site_that_only_mentions_the_name_is_not_a_namesake():
    pages = {**_site("www.reply-de.example", "Reply Deutschland SE", "Düsseldorf"),
             "https://www.reply.example/": "<html><body>Reply – Consulting, Systems Integration</body></html>"}
    res = _verify(FakeWeb(pages), ["https://www.reply.example/", "https://www.reply-de.example/"], "Reply", "Düsseldorf")
    assert res.status == "verified" and res.website == "https://www.reply-de.example/"
