import pytest

from autoapp.evaluation.schema import Requirement
from autoapp.llm.base import UsageMeter
from autoapp.llm.fake import FakeLLM
from autoapp.writing import letter_format
from autoapp.writing.claims import check_claims, evidence_parts, unsupported_numbers
from autoapp.writing.prompt import (
    FINAL_REMINDER,
    JobContext,
    WritingContext,
    app_facts,
    build_system,
    build_user,
    evidence_sources,
    start_line,
)
from autoapp.writing.schema import Claim, LetterDraft
from autoapp.writing.writer import LetterFrame, LetterWriter, StyleSettings

CV = (
    "Backend Developer, Muster GmbH, 2022 bis heute. Entwicklung von Python-Services mit FastAPI. "
    "Maintained CI pipelines used by 4 teams. Kenntnisse: Python, PostgreSQL, AWS Lambda."
)
PROFILE = "name: Max Mustermann\ncity: Berlin\nextra_skills:\n  - Docker (basic)"
INSTRUCTIONS = "Mention that I can start within 4 weeks. I used Terraform in personal projects."


def req(text, status, proficiency="unspecified"):
    return Requirement(text=text, proficiency=proficiency, core=False, status=status)


def job(**kw):
    base = dict(
        title="Backend Engineer (m/w/d)",
        company="Beispiel AG",
        description="Wir suchen Backend Engineer. Python, Grundkenntnisse in Kubernetes, CI/CD, Terraform.",
        language="de",
        requirements=[
            req("Python", "matched"),
            req("Grundkenntnisse in Kubernetes", "bridgeable", "basic"),
            req("Grundkenntnisse in Helm", "bridgeable", "basic"),
            req("5+ Jahre Go", "missing", "advanced"),
        ],
        decision="apply",
        max_words=300,
        max_assumed_skills=5,
    )
    base.update(kw)
    return JobContext(**base)


WCTX = WritingContext(profile_text=PROFILE, cv_text=CV, instructions=INSTRUCTIONS, writing_samples=["Mein alter Brief."])


# --- 7.3 prompt --------------------------------------------------------------------


def test_system_prompt_parts_and_order():
    blocks = build_system(WCTX)
    texts = [b.text for b in blocks]
    joined = "\n".join(texts)
    for needle in ("Truth policy", "Persuasive framing", "Skills the CV does not mention", "Style", "Format",
                   "<candidate_profile>", "<candidate_cv>", "<writing_samples>", "<candidate_instructions>"):
        assert needle in joined
    assert texts[-1] == FINAL_REMINDER
    order = [joined.index(x) for x in ("Truth policy", "<candidate_cv>", "<writing_samples>", "<candidate_instructions>", FINAL_REMINDER)]
    assert order == sorted(order)
    assert all(b.stable for b in blocks)
    assert "Never copy sentences" in joined


def test_quoting_rules_in_prompt_and_schema():
    joined = "\n".join(b.text for b in build_system(WCTX))
    assert 'separate the quotes with " | "' in joined
    assert "Never paraphrase, shorten inside or translate a quote" in joined
    assert "are not claims" in joined
    assert "' | '" in Claim.model_fields["evidence"].description
    assert "Never paraphrase or translate" in Claim.model_fields["evidence"].description
    assert "are not claims" in LetterDraft.model_fields["claims"].description


def test_user_prompt_lists_bridgeable_only_for_apply_and_hides_salary_by_default():
    u = build_user(job())
    assert "Grundkenntnisse in Kubernetes" in u and "at most 5" in u
    assert "Do not mention salary." in u
    assert "5+ Jahre Go" not in u.split("<job_ad>")[0]
    u2 = build_user(job(decision="maybe"))
    assert "Bridgeable requirements you may cover: none" in u2
    u3 = build_user(job(salary_line="Meine Gehaltsvorstellung liegt bei 65.000 EUR brutto im Jahr.",
                        profile_instructions="Use a more informal tone"))
    assert "65.000" in u3 and "Use a more informal tone" in u3


def test_app_facts_and_sources():
    assert app_facts(job()) == []
    j = job(earliest_start="01.11.2026", salary_line="Meine Gehaltsvorstellung liegt bei 65.000 EUR brutto im Jahr.")
    assert app_facts(j) == [
        "earliest_start: 01.11.2026",
        start_line("01.11.2026"),
        "Meine Gehaltsvorstellung liegt bei 65.000 EUR brutto im Jahr.",
    ]
    assert start_line("01.11.2026") in build_user(job(earliest_start="01.11.2026"))
    for fact in app_facts(j):
        assert j.description not in fact and j.title not in fact and j.company not in fact
    sources = evidence_sources(WCTX, job(profile_instructions="I led a student team of 3"))
    assert INSTRUCTIONS in sources["instructions"] and "I led a student team of 3" in sources["instructions"]


# --- 7.4 claims ---------------------------------------------------------------------

SOURCES = {"cv": CV, "profile": PROFILE, "instructions": INSTRUCTIONS}
BRIDGE = ["Grundkenntnisse in Docker", "Grundkenntnisse in Kubernetes"]


def _check(claims, decision="apply", cap=5, bridge=BRIDGE):
    return check_claims(claims, sources=SOURCES, bridgeable=bridge, decision=decision, max_assumed=cap)


def test_invented_aws_years_rejected():
    probs = _check([Claim(text="5 Jahre Erfahrung mit AWS", type="cv", evidence="5 Jahre AWS")])
    assert probs and "does not appear in the CV" in probs[0]
    assert unsupported_numbers("Ich habe 5 Jahre Erfahrung mit AWS.", [CV, PROFILE, INSTRUCTIONS]) == ["5"]


def test_cv_backed_claim_accepted_with_whitespace_and_case_differences():
    assert _check([Claim(text="CI für vier Teams", type="cv", evidence="maintained  CI pipelines used by 4 TEAMS")]) == []


def test_basic_docker_allowed_and_kubernetes_years_rejected():
    assert _check([Claim(text="Docker-Grundlagen", type="assumed_basic", requirement="Grundkenntnisse in Docker")]) == []
    probs = _check([Claim(text="Kubernetes", type="assumed_basic", requirement="5+ Jahre Kubernetes")])
    assert probs


def test_cap_of_five_with_seven_bridgeable():
    bridge = [f"Grundkenntnisse in Tool{i}" for i in range(7)]
    claims = [Claim(text=f"Tool{i}", type="assumed_basic", requirement=b) for i, b in enumerate(bridge)]
    probs = _check(claims, bridge=bridge)
    assert any("7 skills outside the CV" in p and "at most 5" in p for p in probs)
    assert _check(claims[:5], bridge=bridge) == []


def test_assumed_skills_only_for_apply():
    probs = _check([Claim(text="Docker", type="assumed_basic", requirement="Grundkenntnisse in Docker")], decision="maybe")
    assert probs and "close match" in probs[0]


def test_terraform_fact_from_instructions():
    assert _check([Claim(text="Terraform privat", type="instructions", evidence="I used Terraform in personal projects")]) == []


def test_evidence_parts_split_and_normalize():
    assert evidence_parts("A b\nC; D | E…F...G") == ["a b", "c", "d", "e", "f", "g"]
    assert evidence_parts("  ") == []
    assert evidence_parts(None) == []
    assert evidence_parts("Master of Science | Uni") == ["master of science", "uni"]


# --- claim evidence: several quotes, accepted sources ----------------------------------

CV_DE = (
    "Werkstudent Backend, Beispiel Energie GmbH\n"
    "Umsetzung neuer Anforderungen und Fehlerbehebung im Backend\n"
    "Pflege der internen Dokumentation\n"
    "Erstellung von Softwaretests (inklusive pytest)\n"
    "Werkstudent, Beispiel Web GmbH\n"
    "Entwicklung von REST APIs und Modellierung von PostgreSQL-Datenbanken\n"
    "Betreuung von Kundenanfragen\n"
    "Testen und Überprüfen von Code anderer Entwickler (Peer Code Reviews)\n"
    "Kenntnisse\nPytorch\nNumpy\nPandas\nDocker\nLinux\n"
    "Sprachen: Deutsch (C1), Englisch (C2)"
)
SOURCES_DE = {"cv": CV_DE, "profile": "name: Max\nearliest_start: 01.11.2026", "instructions": "Kurz schreiben."}


def _check_de(claims, app_facts=()):
    return check_claims(
        claims, sources=SOURCES_DE, bridgeable=[], decision="apply", max_assumed=5, app_facts=app_facts
    )


@pytest.mark.parametrize("sep", ["; ", "\n", " | ", " … "])
def test_quotes_from_several_cv_lines_pass(sep):
    evidence = (
        "Entwicklung von REST APIs und Modellierung von PostgreSQL-Datenbanken"
        + sep
        + "Testen und Überprüfen von Code anderer Entwickler (Peer Code Reviews)"
    )
    assert _check_de([Claim(text="REST APIs und Code Reviews", type="cv", evidence=evidence)]) == []


def test_english_letter_quotes_german_cv_lines():
    claim = Claim(
        text="At the energy company I implemented backend requirements and wrote pytest tests.",
        type="cv",
        evidence=(
            "Umsetzung neuer Anforderungen und Fehlerbehebung im Backend; "
            "Erstellung von Softwaretests (inklusive pytest)"
        ),
    )
    assert _check_de([claim]) == []


def test_skills_list_quotes_pass():
    claim = Claim(text="Skills", type="cv", evidence="Pytorch; Numpy; Pandas; Docker; Linux")
    assert _check_de([claim]) == []


def test_one_invented_part_fails_and_is_named():
    probs = _check_de(
        [
            Claim(
                text="REST APIs und AWS",
                type="cv",
                evidence="Entwicklung von REST APIs und Modellierung von PostgreSQL-Datenbanken | 5 Jahre AWS",
            )
        ]
    )
    assert len(probs) == 1
    assert "does not appear in the CV" in probs[0] and '"5 jahre aws"' in probs[0]
    probs = _check_de([Claim(text="Skills", type="cv", evidence="Pytorch; Numpy; Kubernetes")])
    assert len(probs) == 1 and "kubernetes" in probs[0]


def test_wrong_tag_still_passes():
    evidence = "Erstellung von Softwaretests (inklusive pytest)"
    claims = [
        Claim(text="Tests geschrieben", type="instructions", evidence=evidence),
        Claim(text="Tests geschrieben", type="profile", evidence=evidence),
    ]
    assert _check_de(claims) == []


def test_app_supplied_start_date_and_salary_pass():
    j = job(earliest_start="01.11.2026", salary_line="Meine Gehaltsvorstellung liegt bei 65.000 EUR brutto im Jahr.")
    claims = [
        Claim(text="Ich kann ab dem 01.11.2026 starten.", type="instructions", evidence=start_line("01.11.2026")),
        Claim(
            text="Gehalt 65.000",
            type="profile",
            evidence="Meine Gehaltsvorstellung liegt bei 65.000 EUR brutto im Jahr.",
        ),
    ]
    assert _check_de(claims, app_facts=app_facts(j)) == []
    assert len(_check_de(claims)) == 2


def test_ad_text_is_never_evidence():
    j = job()
    probs = check_claims(
        [Claim(text="Terraform-Erfahrung", type="cv", evidence="Grundkenntnisse in Kubernetes, CI/CD, Terraform")],
        sources=evidence_sources(WCTX, j),
        app_facts=app_facts(j),
        bridgeable=[],
        decision="apply",
        max_assumed=5,
    )
    assert len(probs) == 1
    probs = check_claims(
        [Claim(text="Backend Engineer", type="cv", evidence="Backend Engineer (m/w/d)")],
        sources=evidence_sources(WCTX, j),
        app_facts=app_facts(j),
        bridgeable=[],
        decision="apply",
        max_assumed=5,
    )
    assert len(probs) == 1


def test_search_profile_instructions_are_evidence():
    j = job(profile_instructions="I led a student team of 3.")
    probs = check_claims(
        [Claim(text="Team geleitet", type="instructions", evidence="I led a student team of 3")],
        sources=evidence_sources(WCTX, j),
        app_facts=app_facts(j),
        bridgeable=[],
        decision="apply",
        max_assumed=5,
    )
    assert probs == []


def test_multi_quote_draft_needs_one_call():
    draft = {
        **GOOD,
        "claims": [
            {
                "text": "Python-Services seit 2022",
                "type": "cv",
                "evidence": "Entwicklung von Python-Services mit FastAPI | Backend Developer, Muster GmbH, 2022 bis heute",
            },
            *GOOD["claims"][1:],
        ],
    }
    w, _ = _writer([draft])
    out = w.write(WCTX, job(), FRAME)
    assert out.llm_calls == 1 and out.needs_review == []


def test_form_answer_accepts_multi_quote_evidence():
    from autoapp.writing.writer import FreeTextWriter

    llm = FakeLLM(script={"form_fill": [
        {"answer": "Ich baue seit 2022 Python-Services und möchte das bei Ihnen fortsetzen.", "claims": [
            {"text": "Python seit 2022", "type": "cv",
             "evidence": "Entwicklung von Python-Services mit FastAPI | Backend Developer, Muster GmbH, 2022 bis heute"}]},
    ]})
    out = FreeTextWriter(llm, UsageMeter(max_calls=5), sleep=lambda s: None).answer(
        WCTX, job(), "Warum möchten Sie bei uns arbeiten?", 500)
    assert out.needs_review == []
    assert len(llm.calls) == 1


# --- letter frame ---------------------------------------------------------------------


def test_salutations_and_subject():
    assert letter_format.salutation(None, "de") == "Sehr geehrte Damen und Herren,"
    assert letter_format.salutation("Frau Weber", "de") == "Sehr geehrte Frau Weber,"
    assert letter_format.salutation("Herr Dr. Klaus Meier", "de") == "Sehr geehrter Herr Dr. Meier,"
    assert letter_format.salutation(None, "en") == "Dear Hiring Team,"
    assert letter_format.subject("Software-Entwickler (m/w/d)", "2026-17", "de") == (
        "Bewerbung als Software-Entwickler, Kennziffer 2026-17"
    )
    from datetime import date

    assert letter_format.date_line("Berlin", date(2026, 10, 7), "de") == "Berlin, 7. Oktober 2026"


# --- 7.5 writing loop ----------------------------------------------------------------

GOOD = {
    "paragraphs": [
        "Bei der Muster GmbH entwickle ich seit 2022 Python-Services mit FastAPI.",
        "Ich verantworte die CI-Pipelines von 4 Teams und arbeite routiniert mit den Grundlagen von Kubernetes.",
        "Ich kann innerhalb von 4 Wochen starten und freue mich auf ein Gespräch.",
    ],
    "motivation_short": "Ihre Plattform passt zu meiner Arbeit mit Python-Services.",
    "email_body": "Guten Tag,\n\nanbei meine Bewerbung als Backend Engineer.\n\nViele Grüße\nMax Mustermann",
    "claims": [
        {"text": "Python-Services seit 2022", "type": "cv", "evidence": "Entwicklung von Python-Services mit FastAPI"},
        {"text": "CI von 4 Teams", "type": "cv", "evidence": "Maintained CI pipelines used by 4 teams"},
        {"text": "Kubernetes-Grundlagen", "type": "assumed_basic", "requirement": "Grundkenntnisse in Kubernetes"},
        {"text": "Start in 4 Wochen", "type": "instructions", "evidence": "start within 4 weeks"},
    ],
}

BAD = {
    **GOOD,
    "paragraphs": ["Hiermit bewerbe ich mich — mit 5 Jahren AWS-Erfahrung!", *GOOD["paragraphs"][1:]],
    "claims": [*GOOD["claims"], {"text": "5 Jahre AWS", "type": "cv", "evidence": "5 Jahre AWS"}],
}

FRAME = LetterFrame(contact_person="Frau Weber", reference="2026-17", signature="Max Mustermann")


def _writer(script):
    llm = FakeLLM(script={"write": script})
    return LetterWriter(llm, UsageMeter(max_calls=10), sleep=lambda s: None), llm


def test_clean_draft_needs_one_call():
    w, llm = _writer([GOOD])
    out = w.write(WCTX, job(), FRAME)
    assert out.llm_calls == 1 and out.needs_review == []
    assert out.letter.salutation == "Sehr geehrte Frau Weber,"
    assert out.letter.subject.endswith("Kennziffer 2026-17")
    assert out.letter.closing == "Mit freundlichen Grüßen"
    assert out.letter.assumed_skills == ["Grundkenntnisse in Kubernetes"]


def test_violations_fixed_by_rewrite():
    w, llm = _writer([BAD, GOOD])
    out = w.write(WCTX, job(), FRAME)
    assert out.llm_calls == 2 and out.needs_review == []
    rewrite_user = llm.calls[1][2]
    assert "hiermit bewerbe ich mich" in rewrite_user.lower()
    assert "5 Jahre AWS" in rewrite_user and "dash" in rewrite_user.lower()


def test_persistent_violation_flags_review_and_dashes_are_fixed():
    w, _ = _writer([BAD, BAD])
    out = w.write(WCTX, job(), FRAME)
    assert out.llm_calls == 2
    assert out.needs_review
    assert "—" not in out.letter.body and "!" not in out.letter.body
    assert any("hiermit bewerbe ich mich" in r.lower() for r in out.needs_review)
    assert any("5" in r for r in out.needs_review)


def test_user_banned_phrase_is_enforced():
    w, _ = _writer([GOOD, GOOD])
    out = w.write(WCTX, job(), FRAME, StyleSettings(banned_extra=["routiniert"]))
    assert any("routiniert" in r for r in out.needs_review)


def test_motivation_truncated_at_sentence_end():
    long = {**GOOD, "motivation_short": ("Ich arbeite gern mit Python-Services. " * 40).strip()}
    w, _ = _writer([long, long])
    out = w.write(WCTX, job(), FRAME)
    assert len(out.letter.motivation_short) <= 1000
    assert out.letter.motivation_short.endswith(".")


def test_shorten_uses_lower_word_limit():
    w, llm = _writer([GOOD])
    w.shorten(WCTX, job(), FRAME)
    assert "255 words" in llm.calls[0][2]


def test_motivation_dash_is_fixed():
    dashed = {**GOOD, "motivation_short": "Ihre Plattform passt zu mir — vor allem wegen Python-Services."}
    w, _ = _writer([dashed, dashed])
    out = w.write(WCTX, job(), FRAME)
    assert "—" not in out.letter.motivation_short


def test_free_text_answer_respects_limit_and_rules():
    from autoapp.writing.writer import FreeTextWriter

    llm = FakeLLM(script={"form_fill": [
        {"answer": "Weil ich Python-Services liebe — und zwar sehr! " * 20, "claims": []},
        {"answer": "Ich baue seit 2022 Python-Services und möchte das bei Ihnen fortsetzen.", "claims": [
            {"text": "Python seit 2022", "type": "cv", "evidence": "Entwicklung von Python-Services mit FastAPI"}]},
    ]})
    out = FreeTextWriter(llm, UsageMeter(max_calls=5), sleep=lambda s: None).answer(
        WCTX, job(), "Warum möchten Sie bei uns arbeiten?", 500)
    assert len(out.answer) <= 500 and out.needs_review == []
    assert len(llm.calls) == 2 and "Fix these problems" in llm.calls[1][2]
