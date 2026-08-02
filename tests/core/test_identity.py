"""Unit tests for nodeiq.core.identity.

Extracted during the Phase 3.7 refactoring sprint from three
near-identical UID/GID resolution implementations shared between
`processes.py` and `permissions.py` — see DECISIONS.md ADR-012.
"""

from nodeiq.core import identity


class _FakePasswdEntry:
    def __init__(self, name: str):
        self.pw_name = name


class _FakeGroupEntry:
    def __init__(self, name: str):
        self.gr_name = name


def test_resolve_username_returns_the_name_when_lookup_succeeds(monkeypatch):
    monkeypatch.setattr(identity.pwd, "getpwuid", lambda uid: _FakePasswdEntry("shruti"))

    assert identity.resolve_username(1000) == "shruti"


def test_resolve_username_falls_back_to_the_uid_string_when_lookup_fails(monkeypatch):
    def _raise_keyerror(uid):
        raise KeyError(uid)

    monkeypatch.setattr(identity.pwd, "getpwuid", _raise_keyerror)

    assert identity.resolve_username(99999) == "99999"


def test_resolve_groupname_returns_the_name_when_lookup_succeeds(monkeypatch):
    monkeypatch.setattr(identity.grp, "getgrgid", lambda gid: _FakeGroupEntry("staff"))

    assert identity.resolve_groupname(20) == "staff"


def test_resolve_groupname_falls_back_to_the_gid_string_when_lookup_fails(monkeypatch):
    def _raise_keyerror(gid):
        raise KeyError(gid)

    monkeypatch.setattr(identity.grp, "getgrgid", _raise_keyerror)

    assert identity.resolve_groupname(99999) == "99999"
