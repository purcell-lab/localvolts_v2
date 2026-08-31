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

# Values used only to prove the detector fires. They must sit outside ALLOWED,
# so they cannot be the placeholder, and they must not be anybody's meter, so
# they take the same unallocated leading digit 1 the placeholder relies on.
PIN_NMI = "1000000001"
PIN_WITH_CHECKSUM = "10000000012"

PINS = {PIN_NMI, PIN_WITH_CHECKSUM}

REPO_ROOT = Path(__file__).resolve().parent.parent

SEARCHED = ("*.py", "*.md", "*.json", "*.yaml", "*.yml")

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}

# Ten or eleven digits that are not part of a longer number and not part of a
# decimal. The decimal exclusion matters: a value such as 0.3076478598 is a
# price, not an identifier, and matching it would make this test useless noise.
NMI_SHAPED = re.compile(r"(?<![\d.])(\d{10,11})(?![\d.])")


# This file is excluded from the repository-wide scan below, because it has to
# hold values the detector rejects in order to prove the detector works. That
# exclusion was previously used to justify keeping the real NMI here, which
# defeated the whole point: the one file the guard could not see was the file
# holding the thing it was meant to find. The pins are synthetic now, and
# test_this_file_holds_only_its_own_pins covers the file that the scan skips.
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
    assert _offenders(f'CONF_NMI: "{PIN_NMI}"') == {PIN_NMI}
    assert _offenders(f'nmi = "{PIN_WITH_CHECKSUM}"') == {PIN_WITH_CHECKSUM}


def test_this_file_holds_only_its_own_pins():
    """Cover the one file the repository scan deliberately skips.

    Without this, pasting a working NMI into this file is invisible to the
    guard, which is exactly how a real one survived here for eleven releases.
    """
    offenders = _offenders(SELF.read_text(encoding="utf-8"))
    assert offenders <= PINS, (
        "This file may only contain its own pin values. "
        f"Unexpected NMI-shaped values: {sorted(offenders - PINS)}"
    )


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
