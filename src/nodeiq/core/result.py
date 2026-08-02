"""The result of running one external command.

A `dataclass` is a plain Python class whose whole job is to hold a fixed
set of named fields — here, everything you'd ever want to know about one
command that NodeIQ ran. Using a dataclass instead of a bare dict means
every field has a name and a type that your editor and `mypy` can check,
instead of having to remember magic string keys like `result["stdout"]`.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    """Everything about one attempt to run one external command.

    `frozen=True` makes instances immutable after creation — once a
    collector gets a `CommandResult` back from the runner, nothing (by
    accident or on purpose) can change what it recorded actually happened.
    """

    command: list[str]
    """The exact command that was run, e.g. `["df", "-h"]`."""

    returncode: int | None
    """The process's exit code. `None` if the command never actually ran
    (for example, the program didn't exist) — see `error` below."""

    stdout: str
    """Everything the command printed to standard output."""

    stderr: str
    """Everything the command printed to standard error."""

    duration_seconds: float
    """How long the command took to run, measured by the runner itself."""

    timed_out: bool = False
    """`True` if the command was still running when the timeout expired
    and had to be killed."""

    error: str | None = None
    """A human-readable explanation if the command could not be run at all
    (e.g. the program doesn't exist on this system). `None` when the
    command ran to completion, regardless of its exit code."""

    @property
    def succeeded(self) -> bool:
        """`True` only if the command ran to completion with exit code 0.

        A non-zero exit code, a timeout, and a failure to launch the
        command at all are all "not succeeded" — but they remain
        distinguishable from each other via `returncode`, `timed_out`, and
        `error`, so a caller can explain *why* something failed, not just
        that it did.
        """
        return self.error is None and not self.timed_out and self.returncode == 0
