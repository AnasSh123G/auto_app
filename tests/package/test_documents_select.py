from __future__ import annotations

from autoapp.config.models import DocumentEntry, Personal, Profile
from autoapp.package.documents_select import select_documents


def make_profile(*docs: DocumentEntry) -> Profile:
    personal = Personal(
        first_name="A", last_name="B", email="a@b.de", phone="1", street="s", postal_code="1", city="c"
    )
    return Profile(personal=personal, documents=list(docs))


CV_DE = DocumentEntry(file="cv_de.pdf", role="cv", language="de")
CV_EN = DocumentEntry(file="cv_en.pdf", role="cv", language="en")
CERT1 = DocumentEntry(file="z1.pdf", role="certificate")
CERT2 = DocumentEntry(file="z2.pdf", role="certificate")
SAMPLE = DocumentEntry(file="letter.pdf", role="writing_sample", language="de")
PHOTO = DocumentEntry(file="foto.jpg", role="photo")


def test_german_ad_picks_german_cv():
    sel = select_documents(make_profile(CV_EN, CV_DE), "de", [], False)
    assert sel.cv == CV_DE
    assert sel.missing == []


def test_english_ad_picks_english_cv():
    assert select_documents(make_profile(CV_DE, CV_EN), "en", [], False).cv == CV_EN


def test_fallback_to_any_cv():
    assert select_documents(make_profile(CV_DE), "en", [], False).cv == CV_DE


def test_no_cv():
    assert select_documents(make_profile(CERT1), "de", [], False).cv is None


def test_missing_portfolio_listed():
    sel = select_documents(make_profile(CV_DE), "de", ["Portfolio"], False)
    assert sel.missing == ["portfolio"]


def test_portfolio_found_by_label_or_file():
    labeled = DocumentEntry(file="work.pdf", role="other", label="My Portfolio")
    named = DocumentEntry(file="portfolio_2024.pdf", role="other")
    wrong_role = DocumentEntry(file="portfolio.pdf", role="writing_sample")
    assert select_documents(make_profile(CV_DE, labeled), "de", ["portfolio"], False).extras == [labeled]
    assert select_documents(make_profile(CV_DE, named), "de", ["portfolio"], False).missing == []
    assert select_documents(make_profile(CV_DE, wrong_role), "de", ["portfolio"], False).missing == [
        "portfolio"
    ]


def test_writing_samples_never_attached():
    sel = select_documents(make_profile(CV_DE, SAMPLE, CERT1), "de", ["certificates", "photo"], True)
    assert SAMPLE not in [sel.cv, *sel.certificates, *sel.extras, sel.photo]


def test_certificates_only_when_requested_or_flag():
    p = make_profile(CV_DE, CERT1, CERT2)
    assert select_documents(p, "de", [], False).certificates == []
    assert select_documents(p, "de", ["portfolio"], False).certificates == []
    assert select_documents(p, "de", ["certificates"], False).certificates == [CERT1, CERT2]
    assert select_documents(p, "de", ["References"], False).certificates == [CERT1, CERT2]
    assert select_documents(p, "de", [], True).certificates == [CERT1, CERT2]


def test_requested_certificates_but_none_exist():
    sel = select_documents(make_profile(CV_DE), "de", ["certificates"], False)
    assert sel.certificates == [] and sel.missing == ["certificates"]
    assert select_documents(make_profile(CV_DE), "de", [], True).missing == []


def test_photo_only_when_requested():
    p = make_profile(CV_DE, PHOTO)
    assert select_documents(p, "de", [], False).photo is None
    assert select_documents(p, "de", ["photo"], False).photo == PHOTO
    assert select_documents(make_profile(CV_DE), "de", ["photo"], False).missing == ["photo"]


def test_cv_and_cover_letter_requests_not_missing():
    sel = select_documents(make_profile(CV_DE), "de", ["CV", "cover letter", "Lebenslauf"], False)
    assert sel.missing == []


def test_language_specific_documents_follow_the_letter_and_any_is_always_sent():
    de_cert = DocumentEntry(file="zeugnis_de.pdf", role="certificate", language="de")
    en_cert = DocumentEntry(file="cert_en.pdf", role="certificate", language="en")
    anywhere = DocumentEntry(file="degree.pdf", role="certificate", language="any")
    profile = make_profile(CV_DE, CV_EN, de_cert, en_cert, anywhere, CERT1)
    german = select_documents(profile, "de", ["certificates"], False)
    english = select_documents(profile, "en", ["certificates"], False)
    assert german.certificates == [de_cert, anywhere, CERT1]
    assert english.certificates == [en_cert, anywhere, CERT1]  # unset behaves like any


def test_any_language_cv_is_the_fallback_after_the_matching_one():
    any_cv = DocumentEntry(file="cv.pdf", role="cv", language="any")
    assert select_documents(make_profile(CV_DE, any_cv), "en", [], False).cv == any_cv
    assert select_documents(make_profile(any_cv, CV_EN), "en", [], False).cv == CV_EN
