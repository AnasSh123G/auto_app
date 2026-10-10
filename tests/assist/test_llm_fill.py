from autoapp.assist.fields import FieldInfo
from autoapp.assist.llm_fill import fill_unmatched, is_free_text
from autoapp.evaluation.schema import Requirement
from autoapp.llm.base import UsageMeter
from autoapp.llm.fake import FakeLLM
from autoapp.writing.prompt import JobContext, WritingContext

WCTX = WritingContext(profile_text="name: Max", cv_text="Entwicklung von Python-Services mit FastAPI seit 2022.")
JOB = JobContext(title="Backend Developer", company="Muster GmbH", description="Python.", language="de",
                 requirements=[Requirement(text="Python", proficiency="unspecified", core=True, status="matched")],
                 decision="apply", max_words=300, max_assumed_skills=5)
FACTS = {"how_found": "Über StepStone", "salutation": "Herr"}


def f(fid, label, type_="text", **kw):
    return FieldInfo(field_id=fid, frame_index=0, selector=f"#{fid}", tag="textarea" if type_ == "textarea" else "input",
                     type=type_, label=label, **kw)


def test_free_text_answer_respects_maxlength_and_rules():
    long = "Ich entwickle seit 2022 Python-Services mit FastAPI und möchte das bei Ihnen fortsetzen. " * 12
    llm = FakeLLM(script={"form_fill": [
        {"answer": long, "claims": [{"text": "Python seit 2022", "type": "cv", "evidence": "Python-Services mit FastAPI"}]},
        {"answer": "Ich entwickle seit 2022 Python-Services mit FastAPI und möchte das bei Ihnen fortsetzen.",
         "claims": [{"text": "Python seit 2022", "type": "cv", "evidence": "Python-Services mit FastAPI"}]},
    ]})
    q = f("f0-1", "Warum möchten Sie bei uns arbeiten?", "textarea", maxlength=500)
    res = fill_unmatched([q], client=llm, meter=UsageMeter(10), wctx=WCTX, job=JOB, facts=FACTS)
    assert len(res.assignments) == 1 and len(res.assignments[0].value) <= 500
    assert res.needs_review == [] and len(llm.calls) == 2  # one rewrite for the over-long first answer


def test_choices_are_grounded_in_options_and_facts():
    sel = f("f0-2", "Wie haben Sie von uns erfahren", "select", options=[("1", "Jobbörse"), ("2", "Freunde")])
    txt = f("f0-3", "Quelle (Freitext kurz)", "text", maxlength=50)
    txt2 = f("f0-4", "Lieblingsfarbe", "text", maxlength=50)
    llm = FakeLLM(script={"form_fill": [{"choices": [
        {"field_id": "f0-2", "action": "select", "value": "Jobbörse", "fact": "how_found"},
        {"field_id": "f0-3", "action": "fill", "value": "Über StepStone"},
        {"field_id": "f0-4", "action": "fill", "value": "Blau"},  # not a fact: dropped
    ]}]})
    res = fill_unmatched([sel, txt, txt2], client=llm, meter=UsageMeter(10), wctx=WCTX, job=JOB, facts=FACTS)
    got = {(a.field_id, a.kind, a.value) for a in res.assignments}
    assert got == {("f0-2", "select", "1"), ("f0-3", "fill", "Über StepStone")}
    assert "Lieblingsfarbe" in res.skipped


def test_files_checkboxes_and_consent_never_reach_the_model():
    fields = [f("a", "Lebenslauf", "file"), f("b", "Ich habe die Datenschutzerklärung gelesen", "checkbox"),
              f("c", "Newsletter", "checkbox")]
    llm = FakeLLM(script={})
    res = fill_unmatched(fields, client=llm, meter=UsageMeter(10), wctx=WCTX, job=JOB, facts=FACTS)
    assert res.assignments == [] and llm.calls == []


def test_free_text_detection():
    assert is_free_text(f("x", "Ihre Motivation", "textarea"))
    assert is_free_text(f("x", "Was reizt Sie an der Stelle?", "text", maxlength=1000))
    assert not is_free_text(f("x", "Vorname", "text"))


def test_unknown_licence_optional_options_and_free_text_stay_empty():
    fields = [
        f("a", "Führerschein vorhanden?", "select", options=[("yes", "Ja"), ("no", "Nein")]),
        f("b", "Beschreiben Sie Ihren Führerschein", "textarea"),
        f("c", "Haben Sie eine unbekannte Qualifikation?", "textarea"),
    ]
    llm = FakeLLM(script={"form_fill": [{"choices": [
        {"field_id": "a", "action": "select", "value": "Ja", "fact": "availability"}
    ]}]})
    res = fill_unmatched(fields, client=llm, meter=UsageMeter(10), wctx=WCTX, job=JOB,
                         facts={"availability": "ab Januar"})
    assert res.assignments == []
    assert set(res.skipped) == {item.label for item in fields}
    assert len(llm.calls) == 1


MATERIAL = "## CV\nMax Mustermann, Karlsruhe. Python seit 2022.\n## Earlier answers\nAnrede: Herr\nStadt: Karlsruhe"


def test_the_model_reads_the_form_and_fills_from_the_candidates_material():
    city = f("c", "Wohnort", "text", maxlength=30)
    sal = f("s", "Anrede", "select", options=[("1", "Herr"), ("2", "Frau")])
    colour = f("k", "Lieblingsfarbe", "text")
    made_up = f("m", "Ort", "text")
    too_long = f("l", "Kurz", "text", maxlength=3)
    llm = FakeLLM(script={"form_fill": [{"choices": [
        {"field_id": "c", "action": "fill", "value": "Karlsruhe", "source": "Karlsruhe"},
        {"field_id": "s", "action": "select", "value": "Herr", "source": "Anrede: Herr"},
        {"field_id": "k", "action": "fill", "value": "Blau", "source": "Lieblingsfarbe: Blau"},  # never written
        {"field_id": "m", "action": "fill", "value": "Berlin"},  # no source
        {"field_id": "l", "action": "fill", "value": "Karlsruhe", "source": "Karlsruhe"},  # too long
    ]}]})
    res = fill_unmatched([city, sal, colour, made_up, too_long], client=llm, meter=UsageMeter(10), wctx=WCTX,
                         job=JOB, facts=FACTS, material=MATERIAL)
    assert {(a.field_id, a.kind, a.value) for a in res.assignments} == {("c", "fill", "Karlsruhe"),
                                                                       ("s", "select", "1")}
    assert set(res.skipped) == {"Lieblingsfarbe", "Ort", "Kurz"}
    assert "Python seit 2022" in llm.calls[0][2]  # the CV is in the model's context


def test_a_value_must_come_from_the_material_not_just_its_quote():
    city = f("c", "Wohnort", "text")
    start = f("d", "Frühester Eintritt", "text")
    phone = f("p", "Telefon", "text")
    years = f("y", "Jahre Erfahrung", "text")
    material = MATERIAL + "\nTelefon: 0170 1234567\nVerfügbar ab 1. November 2026"
    llm = FakeLLM(script={"form_fill": [{"choices": [
        {"field_id": "c", "action": "fill", "value": "Berlin", "source": "Karlsruhe"},  # quote fine, value made up
        {"field_id": "d", "action": "fill", "value": "01.11.2026", "source": "1. November 2026"},  # reformatted
        {"field_id": "d", "action": "fill", "value": "Ab sofort", "source": "Karlsruhe"},  # a second one: ignored
        {"field_id": "p", "action": "fill", "value": "+49 170 1234567", "source": "0170 1234567"},
        {"field_id": "y", "action": "fill", "value": "7", "source": "seit 2022"},  # worked out, not written
    ]}]})
    res = fill_unmatched([city, start, phone, years], client=llm, meter=UsageMeter(10), wctx=WCTX, job=JOB,
                         facts=FACTS, material=material)
    assert {(a.field_id, a.value) for a in res.assignments} == {("d", "01.11.2026"), ("p", "+49 170 1234567")}
    assert set(res.skipped) == {"Wohnort", "Jahre Erfahrung"}


def test_reading_the_form_keeps_the_rules_values_and_prepared_texts():
    name = f("n", "Vorname", "text")
    motivation = f("m", "Warum möchten Sie bei uns arbeiten?", "textarea")  # the prepared text is used
    question = f("q", "Welche Programmiersprachen beherrschen Sie?", "textarea")  # factual: the model answers
    llm = FakeLLM(script={"form_fill": [{"choices": [
        {"field_id": "q", "action": "fill", "value": "Python", "source": "Python seit 2022"},
    ]}]})  # "Vorname" is skipped by the model: the rules' value stays and it is not reported as open
    res = fill_unmatched([name, motivation, question], client=llm, meter=UsageMeter(10), wctx=WCTX, job=JOB,
                         facts=FACTS, material=MATERIAL, rule_filled=frozenset({"n", "m"}))
    assert [(a.field_id, a.value) for a in res.assignments] == [("q", "Python")]
    assert res.skipped == [] and len(llm.calls) == 1  # no letter-writer call for the prepared motivation
    listed = llm.calls[0][2]
    assert "id=q" in listed and "id=n" in listed and "id=m" not in listed
