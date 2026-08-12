from __future__ import annotations

from datetime import datetime, timezone

from src.validation.schemas import validate_research_paper


def _valid_payload(**overrides) -> dict:
    base = {
        "title": "A Real Paper Title",
        "authors": ["Jane Doe"],
        "paper_url": "https://arxiv.org/abs/2601.01234",
        "paper_external_id": "2601.01234",
        "github_url": None,
        "github_stars": None,
        "published_date": datetime(2026, 8, 9, tzinfo=timezone.utc),
        "source_name": "arxiv",
    }
    base.update(overrides)
    return base


def test_valid_record_passes():
    record, error = validate_research_paper(_valid_payload())
    assert error is None
    assert record is not None
    assert record.title == "A Real Paper Title"


def test_missing_title_rejected():
    payload = _valid_payload()
    del payload["title"]
    record, error = validate_research_paper(payload)
    assert record is None
    assert error is not None


def test_blank_title_rejected():
    record, error = validate_research_paper(_valid_payload(title="   "))
    assert record is None


def test_missing_source_name_rejected():
    payload = _valid_payload()
    del payload["source_name"]
    record, error = validate_research_paper(payload)
    assert record is None


def test_invalid_paper_url_rejected():
    record, error = validate_research_paper(_valid_payload(paper_url="not a url"))
    assert record is None


def test_null_github_fields_allowed():
    record, error = validate_research_paper(_valid_payload(github_url=None, github_stars=None))
    assert error is None
    assert record.github_url is None
    assert record.github_stars is None


def test_valid_github_url_and_stars_accepted():
    record, error = validate_research_paper(
        _valid_payload(github_url="https://github.com/foo/bar", github_stars=42)
    )
    assert error is None
    assert record.github_stars == 42


def test_negative_github_stars_rejected():
    record, error = validate_research_paper(
        _valid_payload(github_url="https://github.com/foo/bar", github_stars=-1)
    )
    assert record is None
    assert error is not None


def test_malformed_github_url_rejected():
    record, error = validate_research_paper(_valid_payload(github_url="https://gitlab.com/foo/bar"))
    assert record is None


def test_null_published_date_allowed():
    """A paper whose date couldn't be established must still be a
    structurally valid record -- freshness/date logic decides elsewhere
    whether to act on it, but the schema itself doesn't require a date."""
    record, error = validate_research_paper(_valid_payload(published_date=None))
    assert error is None
    assert record.published_date is None


def test_empty_authors_list_allowed():
    record, error = validate_research_paper(_valid_payload(authors=[]))
    assert error is None
    assert record.authors == []


def _valid_job_payload(**overrides) -> dict:
    from datetime import datetime, timezone

    base = {
        "title": "Software Engineer",
        "company": "Tech Corp",
        "url": "https://example.com/job",
        "posted_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
        "source_name": "remoteok",
        "is_remote": True,
        "role_family": "engineering",
        "raw_document_id": "12345678-1234-5678-1234-567812345678",
    }
    base.update(overrides)
    return base


def test_valid_job_record_passes():
    from src.validation.schemas import validate_job_record

    record, error = validate_job_record(_valid_job_payload())
    assert error is None
    assert record is not None
    assert record.title == "Software Engineer"


def test_job_raw_document_id_accepted():
    from src.validation.schemas import validate_job_record

    payload = _valid_job_payload(raw_document_id="12345678-1234-5678-1234-567812345678")
    record, error = validate_job_record(payload)
    assert error is None
    assert record.raw_document_id == "12345678-1234-5678-1234-567812345678"
