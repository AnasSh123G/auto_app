"""Shared builders for tests that need a complete workspace."""

from __future__ import annotations

from pathlib import Path

from autoapp.config.workspace import Workspace

CV_TEXT = (
    "Max Mustermann. Backend Developer, Muster GmbH, 2022 bis heute. "
    "Entwicklung von Python-Services mit FastAPI. Maintained CI pipelines used by 4 teams. "
    "Kenntnisse: Python, PostgreSQL, AWS Lambda. Sprachen: Deutsch C1, Englisch C1."
)

CONFIG = """\
search_profiles:
  - name: backend
    keywords: [Backend Developer]
    locations:
      - name: Berlin
        radius_km: 30
limits:
  max_llm_calls_per_run: 50
  max_evaluate_per_run: 20
  max_prepare_per_run: 5
mail:
  sending:
    preset: gmail
    address: max.mustermann@example.com
"""

PROFILE = """\
personal:
  first_name: Max
  last_name: Mustermann
  email: max.mustermann@example.com
  phone: "+49 30 1234567"
  street: Musterstraße 1
  postal_code: "10115"
  city: Berlin
  linkedin_url: https://www.linkedin.com/in/max-mustermann
availability:
  earliest_start: "1. Dezember 2026"
  notice_period: "4 Wochen zum Monatsende"
work_authorization: EU-Bürger, keine Arbeitserlaubnis nötig
language_levels:
  de: C1
  en: C1
documents:
  - file: Lebenslauf_de.pdf
    role: cv
    language: de
  - file: Zeugnis_Muster.pdf
    role: certificate
salary:
  target_eur: 65000
  min_eur: 58000
extra_skills:
  - name: Docker
    level: basic
    context: private Projekte
"""


def _pdf(path: Path, text: str) -> None:
    """A minimal one-page text PDF."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def _with_alert_mode(config: str, mode: str) -> str:
    """``config`` with ``sources.alerts.mode`` set to ``mode`` unless it names a mode itself."""
    import io

    from ruamel.yaml import YAML

    yaml = YAML()
    data = yaml.load(config)
    alerts = data.setdefault("sources", {}).setdefault("alerts", {})
    alerts.setdefault("mode", mode)
    out = io.StringIO()
    yaml.dump(data, out)
    return out.getvalue()


def make_workspace(tmp_path: Path, *, config: str = CONFIG, profile: str = PROFILE,
                   instructions: str = "Kurz und direkt schreiben.", alert_mode: str = "platform") -> Workspace:
    """Test workspaces keep the earlier ``platform`` alert mode unless a test asks for ``search_first``."""
    ws = Workspace(tmp_path / "ws")
    ws.ensure_layout()
    ws.config_path.write_text(_with_alert_mode(config, alert_mode), encoding="utf-8")
    ws.profile_path.write_text(profile, encoding="utf-8")
    ws.writing_instructions_path.write_text(instructions, encoding="utf-8")
    _pdf(ws.documents_dir / "Lebenslauf_de.pdf", CV_TEXT)
    _pdf(ws.documents_dir / "Zeugnis_Muster.pdf", "Arbeitszeugnis Muster GmbH")
    return ws
