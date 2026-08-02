"""Unit tests for nodeiq.routing.keyword_router.

Pure function, no I/O, no mocking needed — every test is a direct
question-in, routing-result-out check.
"""

import pytest

from nodeiq.routing import keyword_router


@pytest.mark.parametrize(
    "question,expected",
    [
        ("what is the cpu usage", ["cpu_memory"]),
        ("how much memory is being used", ["cpu_memory"]),
        ("are there any failed services", ["services"]),
        ("is the ssh daemon running", ["services"]),
        ("who is logged in", ["users"]),
        ("who am i", ["users"]),
        ("is disk space running low", ["disk"]),
        ("how many free inodes are there", ["disk"]),
        ("what cron jobs are scheduled", ["scheduled_jobs"]),
        ("what timers exist", ["scheduled_jobs"]),
        ("is /etc/shadow world writable", ["permissions"]),
        ("what ports are open", ["network"]),
        ("is the firewall enabled", ["network"]),
        ("give me the system logs", ["logs"]),
        ("were there any warnings", ["logs"]),
        ("what is the hostname", ["system"]),
        ("what is the system uptime", ["system"]),
        ("list the top processes", ["processes"]),
    ],
)
def test_route_resolves_unambiguous_single_category_questions(question, expected):
    assert keyword_router.route(question) == expected


def test_route_returns_none_for_a_question_with_no_recognizable_keyword():
    assert keyword_router.route("why is my system slow") is None


def test_route_returns_none_for_a_completely_unrelated_question():
    assert keyword_router.route("what is the weather today") is None


def test_route_returns_none_when_multiple_categories_match():
    # "disk" -> disk, "service" -> services: genuinely spans two
    # categories, so this must escalate rather than guess one.
    assert keyword_router.route("is the disk full and is the ssh service healthy") is None


def test_route_escalates_rather_than_guess_on_genuine_vocabulary_overlap():
    # "process" -> processes, but "memory" -> cpu_memory too: this
    # question's own vocabulary spans both, which is correct to
    # escalate on, not force onto one category arbitrarily.
    assert keyword_router.route("which process is consuming the most memory") is None


def test_route_is_case_insensitive():
    assert keyword_router.route("WHAT IS THE CPU USAGE") == ["cpu_memory"]


def test_route_matches_whole_words_only_not_substrings():
    # "cron" must not match inside an unrelated longer word.
    assert keyword_router.route("acronym test") is None


def test_route_empty_question_returns_none():
    assert keyword_router.route("") is None
