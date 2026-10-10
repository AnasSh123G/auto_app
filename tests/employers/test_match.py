"""Conservative original-vacancy matching (task 4.1)."""

from __future__ import annotations

from autoapp.employers.match import SeedFacts, location_evidence, match_seed
from autoapp.employers.repo import Listing


def posting(title, ref=None, loc="Köln", url=None, **kw):
    return Listing(url=url or f"https://acme.example/job/{ref or title.replace(' ', '-')}", title=title, ref=ref,
                   locations=[loc] if loc else [], **kw)


def test_same_requisition_wins_even_with_changed_wording():
    listings = [posting("Data Engineer (m/w/d)", "R-100"), posting("Data Engineer (m/w/d)", "R-200")]
    res = match_seed(SeedFacts("Senior Data Engineer", "Köln", ref="R-200"), listings)
    assert (res.outcome, res.basis, res.listing.ref) == ("matched", "reference", "R-200")


def test_same_title_different_references_only_the_alerts_requisition_is_attached():
    listings = [posting("Data Engineer", "R-100"), posting("Data Engineer", "R-200")]
    res = match_seed(SeedFacts("Data Engineer", "Köln", ref="R-200"), listings)
    assert res.outcome == "matched" and res.listing.ref == "R-200"


def test_conflicting_reference_is_never_substituted():
    res = match_seed(SeedFacts("Data Engineer", "Köln", ref="R-999"), [posting("Data Engineer", "R-100")])
    assert res.outcome == "unresolved" and "different requisition" in res.reason


def test_unique_title_with_affirmative_location_matches():
    res = match_seed(SeedFacts("(Junior) Data Engineer (m/w/d)", "Köln, Deutschland"),
                     [posting("(Junior) Data Engineer (m/w/d)", "1", "Köln, Deutschland"), posting("Accountant", "2")])
    assert (res.outcome, res.basis) == ("matched", "title_location") and res.listing.ref == "1"


def test_gender_markers_and_case_do_not_matter():
    res = match_seed(SeedFacts("data engineer (w/m/d)", "Köln"), [posting("Data Engineer (m/w/d)", "1")])
    assert res.outcome == "matched"


def test_unknown_location_is_not_affirmative_evidence():
    res = match_seed(SeedFacts("Data Engineer", "Köln"), [posting("Data Engineer", "1", loc=None)])
    assert res.outcome == "unresolved" and "nothing confirms the location" in res.reason
    res = match_seed(SeedFacts("Data Engineer", None), [posting("Data Engineer", "1", "Köln")])
    assert res.outcome == "unresolved"


def test_other_city_is_not_the_alert_vacancy():
    res = match_seed(SeedFacts("Data Engineer", "Köln"), [posting("Data Engineer", "1", "Berlin")])
    assert res.outcome == "unresolved" and "other places" in res.reason


def test_two_plausible_postings_stay_unresolved_with_candidates():
    listings = [posting("Data Engineer", "1"), posting("Data Engineer", "2")]
    res = match_seed(SeedFacts("Data Engineer", "Köln"), listings)
    assert res.outcome == "unresolved" and len(res.candidates) == 2 and res.listing is None


def test_same_requisition_in_several_places_counts_once():
    listings = [posting("Data Engineer", "1", "Köln", url="https://a/1"), posting("Data Engineer", "1", "Berlin", url="https://a/2")]
    res = match_seed(SeedFacts("Data Engineer", "Berlin"), listings)
    assert res.outcome == "matched" and res.listing.url == "https://a/2"


def test_similar_but_different_roles_are_not_substituted():
    res = match_seed(SeedFacts("Data Engineer", "Köln"), [posting("Senior Data Engineer", "1"), posting("Data Scientist", "2")])
    assert res.outcome == "none"


def test_truncated_title_needs_unique_completion_and_corroboration():
    seed = SeedFacts("Softwareentwickler Python und Type", "Köln", title_truncated=True)
    one = [posting("Softwareentwickler Python und TypeScript (m/w/d)", "1")]
    assert match_seed(seed, one).outcome == "unresolved"  # no reference to confirm the completion
    with_ref = SeedFacts("Softwareentwickler Python und Type", "Köln", title_truncated=True, ref="1")
    assert match_seed(with_ref, one).outcome == "matched"
    two = one + [posting("Softwareentwickler Python und Typo3", "2")]
    assert match_seed(seed, two).outcome == "unresolved"


def test_remote_versus_onsite_conflict_blocks_a_match():
    res = match_seed(SeedFacts("Data Engineer", "Köln", work_mode="remote"),
                     [posting("Data Engineer", "1", work_mode="onsite")])
    assert res.outcome == "unresolved"


def test_location_evidence_levels():
    seed = SeedFacts("x", "Köln, Deutschland")
    assert location_evidence(seed, posting("x", loc="Köln")) == "affirmative"
    assert location_evidence(seed, posting("x", loc="Berlin")) == "conflict"
    assert location_evidence(seed, posting("x", loc=None)) == "unknown"
