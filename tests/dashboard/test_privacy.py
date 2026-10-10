"""No secret ever reaches the feed, the statistics, the stored events or the log (14.9)."""

from __future__ import annotations

from typer.testing import CliRunner

from autoapp.cli import app
from autoapp.dashboard.live import PlainPrinter
from autoapp.dashboard.screen import TaskScreen
from autoapp.mail.connection import FakeMailer, MailAuthError
from tests.helpers import make_workspace
from tests.test_cli_commands import CONFIG, SETUP

SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-PLANTEDsecret0123456789",
    "OPENROUTER_API_KEY": "sk-or-v1-PLANTEDsecretabcdef0123",
    "AUTOAPP_SMTP_PASSWORD": "planted-smtp-pass-9876",
    "AUTOAPP_ALERTS_PASSWORD": "planted-imap-pass-5432",
    "ADZUNA_APP_KEY": "plantedadzunakey00112233",
}


def test_no_secret_in_feed_stats_events_or_log(tmp_path, monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("AUTOAPP_NON_INTERACTIVE", "1")
    ws = make_workspace(tmp_path, config=CONFIG + SETUP)
    plain: list[str] = []
    screens: list[TaskScreen] = []

    from tests.steps.test_pipeline import EVAL, _source
    from tests.steps.test_prepare_step import LETTER, FakeRenderer, factory

    def pipeline(ctx, profile=None, **kw):
        from autoapp.llm.fake import FakeLLM
        from autoapp.sources.alerts.imap_reader import FakeMailbox
        from autoapp.steps import pipeline as real

        ctx.mode = "automatic"
        ctx.clients["evaluate"] = FakeLLM(script={"evaluate": [EVAL]})
        ctx.clients["write"] = FakeLLM(script={"write": [LETTER]})
        # A login failure whose server message echoes the password back.
        failing = FakeMailer(MailAuthError(f"535 login rejected for pass {SECRETS['AUTOAPP_SMTP_PASSWORD']}"))
        screens.append(TaskScreen(ctx.stats, capture=False))
        screens[0].watch(ctx.events, apply_stats=False)
        ctx.events.subscribe(PlainPrinter(plain.append))
        return real._run_pipeline_impl(ctx, profile, api_sources=[_source()], mailbox=FakeMailbox([]),
                                       renderer_factory=factory(FakeRenderer([1])), mailer=failing)

    import autoapp.steps.pipeline as pl

    monkeypatch.setattr(pl, "_run_pipeline_impl", pl.run_pipeline, raising=False)
    monkeypatch.setattr(pl, "run_pipeline", pipeline)
    r = CliRunner().invoke(app, ["-w", str(ws.root), "run", "--non-interactive"])
    stats = CliRunner().invoke(app, ["-w", str(ws.root), "stats", "--period", "all"])

    from autoapp.db.schema import open_db

    conn = open_db(ws.db_path)
    stored = "\n".join(" ".join(str(v) for v in tuple(row)) for row in conn.execute("SELECT * FROM run_events"))
    progress = "\n".join(str(row[0]) for row in conn.execute("SELECT progress FROM runs"))
    conn.close()
    logs = "\n".join(p.read_text() for p in ws.logs_dir.glob("*.log"))
    outputs = {"run output": r.output, "live view": "\n".join(line for e in screens[0].entries for line in screens[0]._entry_lines(e, 140))
               + "\n".join(screens[0]._stats_lines()), "plain feed": "\n".join(plain),
               "stats": stats.output, "run_events": stored, "progress": progress, "log": logs}
    assert "login rejected" in outputs["plain feed"] and "login rejected" in stored  # the failure is reported
    for where, text in outputs.items():
        for name, secret in SECRETS.items():
            assert secret not in text, f"{name} leaked into the {where}"
