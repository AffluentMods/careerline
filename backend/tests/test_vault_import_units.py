# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from resume_fixtures import CLASSIC_TEXT, PDF_LINES, make_docx, make_pdf, make_zip

from waypoint.vault.importer.extract import extract_text_sync
from waypoint.vault.importer.guards import (
    DOCX_MIME,
    PDF_MIME,
    RejectedFile,
    check_type,
    clean_filename,
    sniff,
)
from waypoint.vault.importer.parse import PLACEHOLDER_TITLE, parse_resume
from waypoint.vault.schemas import parse_data


def _by_kind(text: str) -> dict[str, list[dict[str, object]]]:
    out: dict[str, list[dict[str, object]]] = {}
    for d in parse_resume(text).drafts:
        out.setdefault(d.kind, []).append(d.data)
    return out


# --- parser ------------------------------------------------------------------------------


def test_parse_classic_resume() -> None:
    parsed = parse_resume(CLASSIC_TEXT)
    assert parsed.sections == ["summary", "experience", "education", "certifications", "skills"]
    assert parsed.suggestions == {
        "full_name": "Jordan Rivera",
        "headline": "Security Engineer",
        "location_text": "Springfield, ST",
        "email": "jordan@example.com",
        "phone": "(555) 010-0100",
        "summary": "Blue team generalist with five years in detection and response.",
    }
    kinds = _by_kind(CLASSIC_TEXT)
    assert kinds["role"][0] == {
        "title": "Security Analyst",
        "employer": "Northwind Tools",
        "location": "Springfield, ST",
        "start": "2021-01",
        "end": None,
    }
    assert kinds["role"][1]["title"] == "Help Desk Technician"
    assert kinds["role"][1]["employer"] == "Contoso Ltd"
    assert (kinds["role"][1]["start"], kinds["role"][1]["end"]) == ("2018-06", "2020-12")
    # A wrapped PDF line is joined back onto its bullet.
    assert str(kinds["achievement"][0]["text"]).endswith("no longer matched the environment.")
    assert kinds["responsibility"][0]["text"] == "Triaged phishing reports from employees."
    assert kinds["education"] == [
        {
            "institution": "Example State University",
            "credential": "Bachelor of Science",
            "field": "Information Systems",
            "end": "2018-05",
        }
    ]
    certs = kinds["certification"]
    assert certs[0] == {
        "name": "Example Security+",
        "issuer": "Example Cert Body",
        "status": "earned",
        "earned_on": "2021",
    }
    assert certs[1]["status"] == "in_progress" and certs[1]["target_date"] == "2027"
    assert [s["name"] for s in kinds["skill"]] == [
        "Python",
        "PowerShell",
        "Bash",
        "Splunk",
        "Wireshark",
        "Nmap",
    ]
    assert kinds["link"] == [{"label": "LinkedIn", "url": "https://linkedin.com/in/jordan-example"}]


def test_bullets_attach_to_their_role() -> None:
    parsed = parse_resume(CLASSIC_TEXT)
    roles = {d.key: d.data["title"] for d in parsed.drafts if d.kind == "role"}
    bullets = [d for d in parsed.drafts if d.kind in {"achievement", "responsibility"}]
    assert [roles[d.parent] for d in bullets if d.parent is not None] == [
        "Security Analyst",
        "Security Analyst",
        "Help Desk Technician",
        "Help Desk Technician",
    ]


def test_every_draft_passes_schema_validation() -> None:
    for text in (CLASSIC_TEXT, "\n".join(PDF_LINES)):
        for d in parse_resume(text).drafts:
            parse_data(d.kind, d.data)


def test_values_come_from_the_text() -> None:
    """The parser must not invent facts: every string value appears in the source."""
    flat = " ".join(CLASSIC_TEXT.split())
    for d in parse_resume(CLASSIC_TEXT).drafts:
        for key, value in d.data.items():
            if key in {"status", "start", "end", "earned_on", "target_date"} or value is None:
                continue
            if d.kind == "link" and key == "label":
                continue
            needle = str(value).removeprefix("https://")
            assert needle in flat, (d.kind, key, value)


def test_no_sections_found() -> None:
    parsed = parse_resume("Just a paragraph about me.\nAnother line.")
    assert parsed.sections == []
    assert [d.kind for d in parsed.drafts] == []


def test_dated_role_without_title_gets_placeholder() -> None:
    kinds = _by_kind("Experience\n2019 - 2020\n• Did 3 things")
    assert kinds["role"][0]["title"] == PLACEHOLDER_TITLE


def test_bullets_without_a_role_are_achievements() -> None:
    kinds = _by_kind("Experience\n• Wrote the runbook")
    assert kinds == {"achievement": [{"text": "Wrote the runbook"}]}


# --- guards ------------------------------------------------------------------------------


def test_sniff_and_type_check() -> None:
    pdf, docx_bytes = make_pdf(["x"]), make_docx()
    assert sniff(pdf) == "pdf"
    assert sniff(docx_bytes) == "docx"
    assert sniff(b"MZ\x90\x00") is None
    assert check_type(pdf, "application/pdf") == "pdf"
    assert check_type(docx_bytes, DOCX_MIME + "; charset=binary") == "docx"
    with pytest.raises(RejectedFile) as exc:
        check_type(pdf, DOCX_MIME)  # declared type doesn't match the content
    assert exc.value.status == 415
    with pytest.raises(RejectedFile):
        check_type(b"<html>", PDF_MIME)


@pytest.mark.parametrize(
    ("members", "status"),
    [
        ({"word/document.xml": b"\0" * (3 * 1024 * 1024)}, 422),  # extreme ratio
        ({f"f{i}.xml": b"x" for i in range(201)} | {"word/document.xml": b"x"}, 422),
        ({"../evil.xml": b"x", "word/document.xml": b"x"}, 422),
        ({"/abs.xml": b"x", "word/document.xml": b"x"}, 422),
        ({"xl/workbook.xml": b"x"}, 415),  # a zip, but not a Word document
    ],
)
def test_docx_archive_guards(members: dict[str, bytes], status: int) -> None:
    with pytest.raises(RejectedFile) as exc:
        check_type(make_zip(members), DOCX_MIME)
    assert exc.value.status == status


def test_docx_total_size_cap() -> None:
    big = {f"word/media/{i}.bin": bytes(range(256)) * 4096 for i in range(25)}
    with pytest.raises(RejectedFile):
        check_type(make_zip({"word/document.xml": b"x", **big}, 0), DOCX_MIME)


def test_clean_filename() -> None:
    assert clean_filename("..\\..\\C:\\evil<script>.pdf", "pdf") == "evil_script_.pdf"
    assert clean_filename(None, "docx") == "resume.docx"
    assert clean_filename("my resume", "pdf") == "my resume.pdf"
    assert len(clean_filename("a" * 500, "pdf")) <= 125


# --- sandboxed extraction ---------------------------------------------------------------


def test_extract_docx_includes_header_lists_and_tables() -> None:
    text = extract_text_sync(make_docx(), "docx")
    lines = text.splitlines()
    assert lines[0] == "Casey Lin | casey@example.com | 555-010-0199"
    assert "• Resolved 40 tickets per week on average" in lines
    assert "Windows, Active Directory" in lines


def test_extract_pdf() -> None:
    text = extract_text_sync(make_pdf(PDF_LINES), "pdf")
    assert "Network Engineer | Tailspin Networks | 2020 - Present" in text
    assert "riley@example.com" in text


def test_extract_rejects_corrupt_files() -> None:
    for data, kind in (
        (b"%PDF-1.4 nonsense", "pdf"),
        (make_zip({"word/document.xml": b"<"}), "docx"),
    ):
        with pytest.raises(RejectedFile) as exc:
            extract_text_sync(data, kind)  # type: ignore[arg-type]
        assert exc.value.code == "unreadable_file"


def test_extract_timeout_kills_worker() -> None:
    with pytest.raises(RejectedFile) as exc:
        extract_text_sync(make_pdf(["x"]), "pdf", timeout=0.001)
    assert "too long" in exc.value.message


def test_worker_gets_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from waypoint.vault.importer import extract

    monkeypatch.setenv("WAYPOINT_ENCRYPTION_KEYS", "1:secret")
    env = extract._child_env()
    assert not any(k.startswith("WAYPOINT") for k in env)


def test_parse_docx_end_to_end() -> None:
    parsed = parse_resume(extract_text_sync(make_docx(), "docx"))
    assert parsed.suggestions["full_name"] == "Casey Lin"
    assert parsed.suggestions["headline"] == "IT Support Specialist"
    roles = [d.data for d in parsed.drafts if d.kind == "role"]
    assert [(r["title"], r["employer"]) for r in roles] == [
        ("IT Support Specialist", "Fabrikam Services"),
        ("Desk Clerk", "Example City Library"),
    ]
    skills = [d.data["name"] for d in parsed.drafts if d.kind == "skill"]
    assert skills == ["Windows", "Active Directory", "Networking", "Ticketing"]
