"""Resolving UIDs and GIDs to human-readable names.

`processes.py` and `permissions.py` both need to turn a raw UID (and, for
`permissions.py`, a GID too) into a name a person would recognize, with
the same graceful fallback when no name can be resolved (e.g. an
LDAP-backed UID with no local mapping) — a raw numeric ID is still
useful evidence, so neither of these ever raises or returns `None`. This
module is the single, shared home for that lookup, extracted from that
evidence (see DECISIONS.md ADR-012's "three or more collectors"
threshold — here met by two collectors sharing three near-identical
implementations, since `permissions.py` alone had both a UID and a GID
version of the same pattern).
"""

import grp
import pwd


def resolve_username(uid: int) -> str:
    """Resolve a UID to a username via the system's user database.

    Falls back to the numeric UID as a string if no username can be
    resolved.
    """
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def resolve_groupname(gid: int) -> str:
    """Resolve a GID to a group name via the system's group database.

    Falls back to the numeric GID as a string if no group name can be
    resolved.
    """
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)
