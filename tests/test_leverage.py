"""Tests for the honesty guardrail.

`competing_disclosure` is the function that decides what Otto is allowed to claim
another dealer offered. Everything here is a case where a plausible-looking bug
would put a fabricated number in a real salesperson's ear.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="otto-test-")

from app import storage, strategy  # noqa: E402
from app.strategy import NO_COMPETING_QUOTE  # noqa: E402


def _quote(case_id, dealer_id, name, otd, reached=True):
    storage.save_json(case_id, f"quotes/{dealer_id}.json", {
        "dealer_id": dealer_id, "dealer_name": name,
        "otd_total_usd": otd, "reached": reached,
        "vehicle_described": "2021 Honda Civic EX",
    })


def test_no_quotes_at_all_denies_leverage():
    case = storage.new_case()
    assert strategy.competing_disclosure(case, "anyone") == NO_COMPETING_QUOTE


def test_own_quote_is_not_leverage_against_itself():
    case = storage.new_case()
    _quote(case, "honda_sf", "SF Honda", 24000)
    # Only one dealer on file, and it is the one we are calling.
    assert strategy.competing_disclosure(case, "honda_sf") == NO_COMPETING_QUOTE


def test_unreached_dealer_is_not_leverage():
    case = storage.new_case()
    _quote(case, "royal", "Royal Motor Sales", None, reached=False)
    assert strategy.competing_disclosure(case, "honda_sf") == NO_COMPETING_QUOTE


def test_quote_with_no_otd_is_not_leverage():
    """Reached, talked, but never gave an out-the-door number."""
    case = storage.new_case()
    _quote(case, "royal", "Royal Motor Sales", None)
    assert strategy.competing_disclosure(case, "honda_sf") == NO_COMPETING_QUOTE


def test_higher_competitor_price_is_not_leverage():
    """Citing a rival's HIGHER price hands the dealer the win."""
    case = storage.new_case()
    _quote(case, "royal", "Royal Motor Sales", 26000)
    out = strategy.competing_disclosure(case, "honda_sf", current_otd=24000)
    assert out == NO_COMPETING_QUOTE


def test_real_lower_quote_produces_a_complete_sentence():
    case = storage.new_case()
    _quote(case, "royal", "Royal Motor Sales", 22500)
    out = strategy.competing_disclosure(case, "honda_sf", current_otd=24000)
    assert "$22,500" in out
    assert "Royal Motor Sales" in out
    # The invariant that matters: never a bare number handed to a template.
    assert out.strip() != "$22,500"
    assert len(out.split()) > 8


def test_lowest_of_several_is_chosen():
    case = storage.new_case()
    _quote(case, "royal", "Royal Motor Sales", 23800)
    _quote(case, "sf_toyota", "San Francisco Toyota", 22100)
    _quote(case, "city_toyota", "City Toyota", 24900)
    out = strategy.competing_disclosure(case, "honda_sf", current_otd=25000)
    assert "$22,100" in out
    assert "$23,800" not in out


def test_disclosure_is_never_empty_or_a_bare_number():
    """The failure this function exists to prevent: a blank or bare-number
    variable dropped into a sentence that asserts a quote exists."""
    case = storage.new_case()
    for current in (None, 0, 24000, 1_000_000):
        for exclude in ("honda_sf", "royal", ""):
            out = strategy.competing_disclosure(case, exclude, current)
            assert out and out.strip(), "disclosure must never be empty"
            assert not out.strip().startswith("$"), "must never be a bare number"
            assert len(out.split()) > 5, "must be a sentence, not a fragment"


def test_private_maximum_is_stripped_before_any_dealer_call():
    from app.extraction import redact_private

    spec = {
        "buyer_name": "Omar",
        "buyer_stated_maximum_usd": 28000,
        "vehicle": {"make": "Honda", "model": "Civic"},
    }
    safe = redact_private(spec)
    assert "buyer_stated_maximum_usd" not in safe
    assert "28000" not in str(safe)
    assert safe["buyer_name"] == "Omar"


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✓ {name}")
                passed += 1
            except AssertionError as e:
                print(f"  ✗ {name}: {e}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
