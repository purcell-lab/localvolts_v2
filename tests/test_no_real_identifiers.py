"""Guard against committing a real NMI.

A real National Metering Identifier was committed to this repository in test
fixtures and stayed there across several releases. An NMI identifies a specific
electricity connection point, so in a public repository it is a piece of
personal information about whoever lives there. Removing it once is not enough,
because the natural thing to do when writing a new test is to paste in an NMI
that is known to work, which is exactly how it arrived.

Every NMI in this repository must be the documentation placeholder. It is
deliberately unmistakable, and its leading digit is not used by any real meter:
the AEMO NMI allocation list assigns numeric blocks under leading digits 2
through 9, and none under 1.

https://www.aemo.com.au/-/media/files/electricity/nem/retail_and_metering/metering-procedures/nmi-allocation-list.pdf
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLACEHOLDER_NMI = "1234567890"
PLACEHOLDER_WITH_CHECKSUM = "12345678908"

ALLOWED = {PLACEHOLDER_NMI, PLACEHOLDER_WITH_CHECKSUM}

REPO_ROOT = Path(__file__).resolve().parent.parent

SEARCHED = ("*.py", "*.md", "*.json", "*.yaml", "*.yml")

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}

# Ten or eleven digits that are not part of a longer number and not part of a
# decimal. The decimal exclusion matters: a value such as 0.3076478598 is a
# price, not an identifier, and matching it would make this test useless noise.
NMI_SHAPED = re.compile(r"(?<![\d.])(\d{10,11})(?![\d.])")


# This file is excluded from its own scan. It has to contain real-looking NMIs
# to pin the detector, and those examples are not identifiers of anything: the
# digits below were the value being removed, so they exist here precisely so
# they cannot exist anywhere else.
SELF = Path(__file__).resolve()


def _searchable_files():
    for pattern in SEARCHED:
        for path in REPO_ROOT.rglob(pattern):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.resolve() == SELF:
                continue
            yield path


def _offenders(text: str) -> set[str]:
    return {match for match in NMI_SHAPED.findall(text) if match not in ALLOWED}


def test_no_nmi_other_than_the_placeholder_is_committed():
    """Fail with the file and the value, so the fix is obvious."""
    found: dict[str, set[str]] = {}
    for path in _searchable_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        offenders = _offenders(text)
        if offenders:
            found[str(path.relative_to(REPO_ROOT))] = offenders

    assert not found, (
        "NMI-shaped values other than the placeholder are committed. "
        f"Replace them with {PLACEHOLDER_NMI}. Found: {found}"
    )


def test_the_guard_actually_catches_a_real_looking_nmi():
    """A guard that cannot fail is worse than no guard.

    The scan above passes trivially on a clean tree, so on its own it proves
    nothing about whether the pattern works. This pins the detector itself.
    """
    assert _offenders('CONF_NMI: "4001247247"') == {"4001247247"}
    assert _offenders('nmi = "40012345678"') == {"40012345678"}


@pytest.mark.parametrize(
    "text",
    [
        f'CONF_NMI: "{PLACEHOLDER_NMI}"',
        f'CONF_NMI: "{PLACEHOLDER_WITH_CHECKSUM}"',
        "pytest.approx(0.3076478598)",  # a price, not an identifier
        "value = 123456789",  # too short to be an NMI
        "timestamp = 20260810100000",  # too long
    ],
)
def test_the_guard_does_not_fire_on_these(text):
    """Keep the guard from becoming something people learn to ignore."""
    assert _offenders(text) == set()
