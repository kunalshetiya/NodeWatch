"""Unit tests for nodeiq.cli.shell.

Every test mocks `detect_platform` and/or `answer_question` (the exact
seams `nodeiq.cli.main`'s own tests already mock) and Python's builtin
`input()`, so none of these depend on a real terminal, a real snapshot,
or a real OpenAI call, per PROJECT_RULES.md Section 11.
"""

from nodeiq.cli import shell
from nodeiq.core.exceptions import SnapshotError

_LINUX = {
    "system": "Linux",
    "machine": "aarch64",
    "description": "Ubuntu 24.04.4 LTS",
    "is_linux": True,
}
_MACOS = {
    "system": "Darwin",
    "machine": "arm64",
    "description": "macOS 15.5",
    "is_linux": False,
}


def _fake_input(monkeypatch, lines):
    """Feed `lines` to every call to the builtin `input()`; once
    exhausted, subsequent calls raise EOFError — mirroring a real
    terminal's behavior when its input stream closes.
    """
    remaining = iter(lines)

    def _input(prompt=""):
        try:
            return next(remaining)
        except StopIteration:
            raise EOFError() from None

    monkeypatch.setattr("builtins.input", _input)


# --- Unsupported platform ------------------------------------------------------------


def test_unsupported_platform_shows_friendly_message_and_exits_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(shell, "detect_platform", lambda: _MACOS)

    def _input_should_never_be_called(prompt=""):
        raise AssertionError("input() must never be called on an unsupported platform")

    monkeypatch.setattr("builtins.input", _input_should_never_be_called)

    exit_code = shell.run_shell()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "macOS 15.5" in out
    assert "Architecture: arm64" in out
    assert "NodeIQ v1 currently supports Linux systems only." in out
    assert "planned for future versions" in out


def test_unsupported_platform_never_enters_the_prompt_loop(monkeypatch, capsys):
    monkeypatch.setattr(shell, "detect_platform", lambda: _MACOS)
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(AssertionError))

    shell.run_shell()

    assert shell._PROMPT not in capsys.readouterr().out


# --- Banner rendering ----------------------------------------------------------------


def test_linux_platform_shows_startup_banner(monkeypatch, capsys):
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)
    _fake_input(monkeypatch, ["exit"])

    shell.run_shell()

    out = capsys.readouterr().out
    assert "NodeIQ v1" in out
    assert "AI Linux Diagnostics Assistant" in out
    assert "Ubuntu 24.04.4 LTS detected" in out
    assert "Type 'help' for commands." in out
    assert "Type 'exit' to quit." in out


def test_banner_uses_the_shared_separator(monkeypatch, capsys):
    from nodeiq.cli.presentation import SEPARATOR

    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)
    _fake_input(monkeypatch, ["exit"])

    shell.run_shell()

    assert SEPARATOR in capsys.readouterr().out


# --- exit / quit -----------------------------------------------------------------------


def test_exit_command_ends_the_loop_cleanly(monkeypatch):
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)
    _fake_input(monkeypatch, ["exit"])

    assert shell.run_shell() == 0


def test_quit_command_ends_the_loop_cleanly(monkeypatch):
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)
    _fake_input(monkeypatch, ["quit"])

    assert shell.run_shell() == 0


def test_exit_command_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)
    _fake_input(monkeypatch, ["EXIT"])

    assert shell.run_shell() == 0


def test_eof_exits_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)
    _fake_input(monkeypatch, [])  # immediately exhausted -> EOFError

    exit_code = shell.run_shell()

    assert exit_code == 0


def test_keyboard_interrupt_exits_cleanly_without_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)

    def _raise_interrupt(prompt=""):
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", _raise_interrupt)

    exit_code = shell.run_shell()

    assert exit_code == 0
    assert "Traceback" not in capsys.readouterr().out


def test_keyboard_interrupt_during_a_question_exits_cleanly(monkeypatch, capsys):
    # A KeyboardInterrupt raised while answer_question() is in flight
    # (e.g. waiting on a slow OpenAI call) must exit the shell cleanly,
    # not just one raised while waiting at the prompt itself.
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)

    def _raise(question):
        raise KeyboardInterrupt()

    monkeypatch.setattr(shell, "answer_question", _raise)
    _fake_input(monkeypatch, ["a slow question"])

    exit_code = shell.run_shell()

    assert exit_code == 0
    assert "Traceback" not in capsys.readouterr().out


# --- help --------------------------------------------------------------------------------


def test_help_command_prints_help_and_continues(monkeypatch, capsys):
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)
    _fake_input(monkeypatch, ["help", "exit"])

    exit_code = shell.run_shell()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Type a question in plain English" in out
    assert "help   Show this message" in out
    assert "clear  Clear the screen" in out


def test_help_command_is_case_insensitive(monkeypatch, capsys):
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)
    _fake_input(monkeypatch, ["HELP", "exit"])

    shell.run_shell()

    assert "Type a question in plain English" in capsys.readouterr().out


# --- clear ---------------------------------------------------------------------------------


def test_clear_command_emits_the_clear_escape_sequence_and_continues(monkeypatch, capsys):
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)
    _fake_input(monkeypatch, ["clear", "exit"])

    exit_code = shell.run_shell()

    assert exit_code == 0
    assert shell._CLEAR_SEQUENCE in capsys.readouterr().out


# --- empty input -----------------------------------------------------------------------------


def test_blank_line_reprompts_without_calling_answer_question(monkeypatch):
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)
    calls = []
    monkeypatch.setattr(shell, "answer_question", lambda q: calls.append(q) or "unused")
    _fake_input(monkeypatch, ["   ", "", "exit"])

    shell.run_shell()

    assert calls == []


# --- questions -------------------------------------------------------------------------------


def test_question_calls_answer_question_and_renders_the_answer(monkeypatch, capsys):
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)
    seen = []

    def _fake_answer_question(question):
        seen.append(question)
        return {"answer": "Nothing has failed.", "snapshot_metadata": {}}

    monkeypatch.setattr(shell, "answer_question", _fake_answer_question)
    _fake_input(monkeypatch, ["Is anything wrong?", "exit"])

    shell.run_shell()

    out = capsys.readouterr().out
    assert seen == ["Is anything wrong?"]
    assert "Question: Is anything wrong?" in out
    assert "Answer:" in out
    assert "Nothing has failed." in out


def test_multiple_questions_in_one_session_all_answered(monkeypatch, capsys):
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)
    monkeypatch.setattr(
        shell, "answer_question", lambda q: {"answer": f"answer to: {q}", "snapshot_metadata": {}}
    )
    _fake_input(monkeypatch, ["first question", "second question", "exit"])

    shell.run_shell()

    out = capsys.readouterr().out
    assert "answer to: first question" in out
    assert "answer to: second question" in out


def test_question_error_is_handled_gracefully_and_session_continues(monkeypatch, capsys):
    monkeypatch.setattr(shell, "detect_platform", lambda: _LINUX)

    def _raise(question):
        raise SnapshotError("no snapshot files found in snapshots")

    monkeypatch.setattr(shell, "answer_question", _raise)
    _fake_input(monkeypatch, ["what failed?", "exit"])

    exit_code = shell.run_shell()

    err = capsys.readouterr().err
    assert exit_code == 0  # the session itself still exits cleanly via "exit"
    assert "No snapshot found" in err
    assert "nodeiq scan" in err


def test_no_second_prompt_construction_path_exists():
    # nodeiq.cli.shell must call the same answer_question() nodeiq.cli.main
    # calls -- not rebuild a prompt itself. Asserting the imported name is
    # identical (not merely equivalent) is a direct, structural check
    # against a duplicated orchestration path.
    from nodeiq.llm.ask import answer_question as real_answer_question

    assert shell.answer_question is real_answer_question
