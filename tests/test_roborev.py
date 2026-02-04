"""Tests for roborev integration - models and CLI parsing."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import claudechic.features.roborev.cli as roborev_cli
from claudechic.commands import _format_verdict, _is_user_command
from claudechic.features.roborev.cli import list_reviews, show_review
from claudechic.features.roborev.models import ReviewDetail, ReviewJob
from claudechic.widgets.layout.reviews import (
    ReviewItem,
    _SPINNER_FRAMES,
    has_running_reviews,
)


# =============================================================================
# ReviewJob.from_dict
# =============================================================================


class TestReviewJobFromDict:
    def test_normal_data(self):
        job = ReviewJob.from_dict(
            {
                "id": 42,
                "git_ref": "abc1234def",
                "branch": "main",
                "agent": "codex",
                "status": "done",
                "verdict": "pass",
                "addressed": False,
                "commit_subject": "Fix bug",
            }
        )
        assert job.id == "42"
        assert job.git_ref == "abc1234def"
        assert job.branch == "main"
        assert job.verdict == "pass"
        assert job.addressed is False
        assert job.commit_subject == "Fix bug"

    def test_null_fields(self):
        """Null JSON values should become empty strings, not 'None'."""
        job = ReviewJob.from_dict(
            {
                "id": None,
                "git_ref": None,
                "verdict": None,
                "commit_subject": None,
            }
        )
        assert job.id == ""
        assert job.git_ref == ""
        assert job.verdict == ""
        assert job.commit_subject == ""

    def test_missing_fields(self):
        """Missing keys should use defaults."""
        job = ReviewJob.from_dict({"id": 1})
        assert job.id == "1"
        assert job.git_ref == ""
        assert job.verdict == ""
        assert job.addressed is False

    def test_string_id(self):
        """String IDs should pass through."""
        job = ReviewJob.from_dict({"id": "abc-123"})
        assert job.id == "abc-123"


# =============================================================================
# ReviewDetail.from_dict
# =============================================================================


class TestReviewDetailFromDict:
    def test_with_nested_job(self):
        detail = ReviewDetail.from_dict(
            {
                "id": 99,
                "job_id": 42,
                "agent": "codex",
                "output": "Looks good",
                "addressed": True,
                "job": {
                    "id": 42,
                    "branch": "main",
                    "verdict": "pass",
                },
            }
        )
        assert detail.id == "99"
        assert detail.job_id == "42"
        assert detail.output == "Looks good"
        assert detail.addressed is True
        assert detail.job is not None
        assert detail.job.branch == "main"

    def test_null_job(self):
        detail = ReviewDetail.from_dict(
            {
                "id": 1,
                "job": None,
            }
        )
        assert detail.job is None

    def test_null_ids(self):
        detail = ReviewDetail.from_dict(
            {
                "id": None,
                "job_id": None,
            }
        )
        assert detail.id == ""
        assert detail.job_id == ""


# =============================================================================
# list_reviews CLI parsing
# =============================================================================


class TestListReviews:
    def test_bare_array(self, tmp_path):
        """Parses a bare JSON array from roborev list --json."""
        payload = json.dumps(
            [
                {"id": 1, "branch": "main", "status": "done", "verdict": "pass"},
                {"id": 2, "branch": "main", "status": "running"},
            ]
        )
        mock_result = MagicMock(returncode=0, stdout=payload, stderr="")
        with (
            patch(
                "claudechic.features.roborev.cli.is_roborev_available",
                return_value=True,
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            reviews = list_reviews(tmp_path, branch="main")
        assert len(reviews) == 2
        assert reviews[0].id == "1"
        assert reviews[0].verdict == "pass"
        assert reviews[1].status == "running"

    def test_empty_array(self, tmp_path):
        mock_result = MagicMock(returncode=0, stdout="[]", stderr="")
        with (
            patch(
                "claudechic.features.roborev.cli.is_roborev_available",
                return_value=True,
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            reviews = list_reviews(tmp_path)
        assert reviews == []

    def test_roborev_not_available(self, tmp_path):
        with patch(
            "claudechic.features.roborev.cli.is_roborev_available", return_value=False
        ):
            reviews = list_reviews(tmp_path)
        assert reviews == []

    def test_invalid_json(self, tmp_path):
        mock_result = MagicMock(returncode=0, stdout="not json", stderr="")
        with (
            patch(
                "claudechic.features.roborev.cli.is_roborev_available",
                return_value=True,
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            reviews = list_reviews(tmp_path)
        assert reviews == []

    def test_filters_addressed(self, tmp_path):
        """Only unaddressed reviews are returned."""
        payload = json.dumps(
            [
                {"id": 1, "status": "done", "verdict": "F", "addressed": False},
                {"id": 2, "status": "done", "verdict": "P", "addressed": True},
                {"id": 3, "status": "running", "addressed": False},
            ]
        )
        mock_result = MagicMock(returncode=0, stdout=payload, stderr="")
        with (
            patch(
                "claudechic.features.roborev.cli.is_roborev_available",
                return_value=True,
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            reviews = list_reviews(tmp_path)
        assert len(reviews) == 2
        assert reviews[0].id == "1"
        assert reviews[1].id == "3"

    def test_limit_applied_after_filter(self, tmp_path):
        """Limit is applied after filtering out addressed reviews.

        Addressed reviews are interleaved within the first `limit` window so
        that if an implementation naively sliced before filtering, the test
        would fail — proving the limit is applied *after* filtering.
        """
        # 8 reviews total: 3 addressed items in positions 0, 2, 4 (inside
        # a naive limit=5 window), and 5 unaddressed items (ids 1, 3, 5, 6, 7).
        items = [
            {"id": 0, "status": "done", "addressed": True},
            {"id": 1, "status": "done", "addressed": False},
            {"id": 2, "status": "done", "addressed": True},
            {"id": 3, "status": "done", "addressed": False},
            {"id": 4, "status": "done", "addressed": True},
            {"id": 5, "status": "done", "addressed": False},
            {"id": 6, "status": "done", "addressed": False},
            {"id": 7, "status": "done", "addressed": False},
        ]
        payload = json.dumps(items)
        mock_result = MagicMock(returncode=0, stdout=payload, stderr="")
        with (
            patch(
                "claudechic.features.roborev.cli.is_roborev_available",
                return_value=True,
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            reviews = list_reviews(tmp_path, limit=5)
        # All 5 unaddressed reviews should be returned
        assert len(reviews) == 5
        returned_ids = [r.id for r in reviews]
        assert returned_ids == ["1", "3", "5", "6", "7"]
        # Verify that id 7 (beyond a naive first-5 slice) is included
        assert "7" in returned_ids

    def test_nonzero_exit(self, tmp_path):
        mock_result = MagicMock(returncode=1, stdout="", stderr="error")
        with (
            patch(
                "claudechic.features.roborev.cli.is_roborev_available",
                return_value=True,
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            reviews = list_reviews(tmp_path)
        assert reviews == []


# =============================================================================
# show_review CLI parsing
# =============================================================================


class TestShowReview:
    def test_returns_detail(self, tmp_path):
        payload = json.dumps(
            {
                "id": 99,
                "job_id": 42,
                "agent": "codex",
                "output": "No issues found.",
                "job": {"id": 42, "verdict": "pass", "branch": "main"},
            }
        )
        mock_result = MagicMock(returncode=0, stdout=payload, stderr="")
        with (
            patch(
                "claudechic.features.roborev.cli.is_roborev_available",
                return_value=True,
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            detail = show_review("42", tmp_path)
        assert detail is not None
        assert detail.id == "99"
        assert detail.job is not None
        assert detail.job.verdict == "pass"

    def test_not_found(self, tmp_path):
        mock_result = MagicMock(returncode=1, stdout="", stderr="not found")
        with (
            patch(
                "claudechic.features.roborev.cli.is_roborev_available",
                return_value=True,
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            detail = show_review("999", tmp_path)
        assert detail is None


# =============================================================================
# ReviewItem rendering
# =============================================================================


def _make_item(verdict: str = "", status: str = "done", **kwargs) -> ReviewItem:
    """Create a ReviewItem without mounting it."""
    data = {
        "id": 1,
        "git_ref": "abc1234",
        "commit_subject": "test",
        "status": status,
        "verdict": verdict,
        **kwargs,
    }
    return ReviewItem(ReviewJob.from_dict(data))


class TestReviewItemRender:
    def test_pass_long(self):
        text = _make_item(verdict="pass").render()
        assert text.plain.startswith("P ")

    def test_pass_short(self):
        text = _make_item(verdict="P").render()
        assert text.plain.startswith("P ")

    def test_fail_long(self):
        text = _make_item(verdict="fail").render()
        assert text.plain.startswith("F ")

    def test_fail_short(self):
        text = _make_item(verdict="F").render()
        assert text.plain.startswith("F ")

    def test_unknown_verdict(self):
        text = _make_item(verdict="").render()
        assert text.plain.startswith("? ")

    def test_job_id_shown(self):
        text = _make_item(verdict="P").render()
        assert "#1" in text.plain

    def test_running_shows_spinner(self):
        """Running status shows a spinner frame, not a verdict."""
        item = _make_item(verdict="", status="running")
        text = item.render()
        assert text.plain[0] in _SPINNER_FRAMES

    def test_queued_shows_spinner(self):
        item = _make_item(verdict="", status="queued")
        text = item.render()
        assert text.plain[0] in _SPINNER_FRAMES


# =============================================================================
# has_running_reviews
# =============================================================================


class TestHasRunningReviews:
    def _job(self, status: str = "done") -> ReviewJob:
        return ReviewJob(id="1", status=status)

    def test_empty_list(self):
        assert has_running_reviews([]) is False

    def test_all_done(self):
        assert has_running_reviews([self._job("done"), self._job("done")]) is False

    def test_running(self):
        assert has_running_reviews([self._job("done"), self._job("running")]) is True

    def test_queued(self):
        assert has_running_reviews([self._job("queued")]) is True

    def test_pending(self):
        assert has_running_reviews([self._job("pending")]) is True

    def test_case_insensitive(self):
        assert has_running_reviews([self._job("Running")]) is True
        assert has_running_reviews([self._job("QUEUED")]) is True

    def test_none_status(self):
        """None status should not crash — treated as not running."""
        job = ReviewJob(id="1", status=None)  # type: ignore[arg-type]
        assert has_running_reviews([job]) is False

    def test_empty_status(self):
        assert has_running_reviews([self._job("")]) is False

    def test_non_string_status(self):
        """Truthy non-string status should not crash — treated as not running."""
        job = ReviewJob(id="1", status=123)  # type: ignore[arg-type]
        assert has_running_reviews([job]) is False


# =============================================================================
# _is_user_command — colon-to-hyphen skill lookup
# =============================================================================


class TestIsUserCommand:
    def test_hyphenated_skill_dir(self, tmp_path):
        """Colon command matches hyphenated skill directory."""
        skill_dir = tmp_path / ".claude" / "skills" / "roborev-fix"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# skill")

        # _is_user_command checks cwd/.claude/skills/... so pass tmp_path as cwd
        assert _is_user_command("/roborev:fix", tmp_path) is True

    def test_colon_skill_dir(self, tmp_path):
        """Colon command also matches colon-named directory if it exists."""
        skill_dir = tmp_path / ".claude" / "skills" / "roborev:fix"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# skill")

        assert _is_user_command("/roborev:fix", tmp_path) is True

    def test_no_skill_dir(self, tmp_path):
        """Returns False when no matching skill directory exists."""
        # Patch Path.home so it doesn't find real skills in ~/.claude/skills/
        with patch("claudechic.commands.Path.home", return_value=tmp_path / "fakehome"):
            assert _is_user_command("/roborev:fix", tmp_path) is False

    def test_simple_skill_no_colon(self, tmp_path):
        """Non-colon skill still works normally."""
        skill_dir = tmp_path / ".claude" / "skills" / "myplugin"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# skill")

        assert _is_user_command("/myplugin", tmp_path) is True


# =============================================================================
# ReviewItem — job ID edge cases
# =============================================================================


class TestReviewItemJobId:
    def test_zero_id_shown(self):
        """Job ID 0 should render as #0, not #?."""
        item = _make_item(verdict="P", id=0)
        text = item.render()
        assert "#0 " in text.plain

    def test_empty_id_shows_fallback(self):
        """Empty/missing ID should render as #?."""
        item = _make_item(verdict="P", id=None)
        text = item.render()
        assert "#? " in text.plain

    def test_explicit_empty_string_id(self):
        """Explicit empty string ID should render as #?."""
        job = ReviewJob(id="", verdict="P", git_ref="abc1234", commit_subject="test")
        item = ReviewItem(job)
        text = item.render()
        assert "#? " in text.plain


# =============================================================================
# ReviewItem — non-string status/verdict resilience
# =============================================================================


class TestReviewItemNonStringFields:
    def test_non_string_status_renders(self):
        """Non-string status should not crash rendering."""
        job = ReviewJob(
            id="1",
            status=123,  # pyright: ignore[reportArgumentType]
            verdict="P",
            git_ref="abc1234",
            commit_subject="test",
        )
        item = ReviewItem(job)
        text = item.render()
        assert "#1 " in text.plain

    def test_none_verdict_renders(self):
        """None verdict should not crash rendering."""
        job = ReviewJob(id="1", verdict=None, git_ref="abc1234", commit_subject="test")  # type: ignore[arg-type]
        item = ReviewItem(job)
        text = item.render()
        assert "? " in text.plain

    def test_non_string_verdict_renders(self):
        """Non-string verdict should not crash rendering."""
        job = ReviewJob(id="1", verdict=42, git_ref="abc1234", commit_subject="test")  # type: ignore[arg-type]
        item = ReviewItem(job)
        text = item.render()
        assert "? " in text.plain


# =============================================================================
# Verdict coercion in /reviews table
# =============================================================================


class TestFormatVerdict:
    """Test _format_verdict from commands.py (used by /reviews table)."""

    def test_normal_pass(self):
        assert _format_verdict("pass") == "P"

    def test_normal_fail(self):
        assert _format_verdict("F") == "F"

    def test_none_verdict(self):
        assert _format_verdict(None) == "…"

    def test_int_verdict(self):
        assert _format_verdict(42) == "…"

    def test_empty_string(self):
        assert _format_verdict("") == "…"


# =============================================================================
# is_roborev_available TTL cache
# =============================================================================


class TestIsRoborevAvailableCache:
    def test_caches_within_ttl(self):
        """Result is cached — shutil.which is not called again within TTL."""
        # Reset cache state
        roborev_cli._roborev_available = None
        roborev_cli._roborev_checked_at = 0.0

        with patch(
            "claudechic.features.roborev.cli.shutil.which",
            return_value="/usr/bin/roborev",
        ) as mock_which:
            assert roborev_cli.is_roborev_available() is True
            assert roborev_cli.is_roborev_available() is True
            assert mock_which.call_count == 1  # cached, not called twice

        # Clean up
        roborev_cli._roborev_available = None
        roborev_cli._roborev_checked_at = 0.0

    def test_refreshes_after_ttl(self):
        """Cache refreshes after TTL expires."""
        roborev_cli._roborev_available = None
        roborev_cli._roborev_checked_at = 0.0

        with (
            patch(
                "claudechic.features.roborev.cli.shutil.which",
                return_value="/usr/bin/roborev",
            ) as mock_which,
            patch(
                "claudechic.features.roborev.cli.time.monotonic",
                side_effect=[0.0, 0.5, 61.0, 61.0],
            ),
        ):
            assert (
                roborev_cli.is_roborev_available() is True
            )  # monotonic=0.0, calls which
            assert roborev_cli.is_roborev_available() is True  # monotonic=0.5, cached
            assert (
                roborev_cli.is_roborev_available() is True
            )  # monotonic=61.0, expired, calls which again
            assert mock_which.call_count == 2

        roborev_cli._roborev_available = None
        roborev_cli._roborev_checked_at = 0.0
