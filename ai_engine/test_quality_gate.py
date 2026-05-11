"""Tests for landing_quality_gate.evaluate.

Builds tiny in-memory workspaces (just enough JSX for the regex scans
to find / not find what they need) and asserts the per-purpose checkers
flip pass/fail correctly.

Run from ai_engine/:
    python3 -m pytest test_quality_gate.py -v
or, without pytest:
    python3 test_quality_gate.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

# Load landing_quality_gate.py directly — going through `app.*` triggers
# the full FastAPI package init (Supabase / OpenHands / postgrest), which
# isn't available in a bare test env. The module under test has no
# in-package imports, so this is safe.
_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "app", "services", "landing_quality_gate.py",
)
_spec = importlib.util.spec_from_file_location("landing_quality_gate", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
evaluate = _mod.evaluate


def _scaffold(workspace: Path, files: dict[str, str]) -> None:
    """Write the given {relpath: content} into a fake workspace under src/."""
    for rel, content in files.items():
        full = workspace / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")


def _check(report: dict, name: str) -> dict | None:
    return next((c for c in report["checks"] if c["name"] == name), None)


# ── Hiring ────────────────────────────────────────────────────────────

HIRING_GOOD_HERO = """
'use client';
export default function Hero() {
  return (
    <section id="hero">
      <h1>Now hiring CDL-A drivers and Owner-Operators</h1>
      <p>Earn $0.65 CPM, sign-on bonus $1,500.</p>
      <a href="#apply">Apply Now</a>
    </section>
  );
}
"""

HIRING_GOOD_FORM = """
'use client';
export default function ApplyForm() {
  return (
    <section id="apply">
      <h2>Start application</h2>
      <form>
        <input name="name" required />
        <input name="email" type="email" required />
        <input name="phone" required />
        <select name="role" required>
          <option value="">Select a position…</option>
          <option value="cdl-a-driver">CDL-A Driver</option>
          <option value="owner-operator">Owner-Operator</option>
        </select>
        <button type="submit">Apply Now →</button>
      </form>
    </section>
  );
}
"""

HIRING_GOOD_TESTIMONIALS = """
export default function Testimonials() {
  return (
    <section>
      <blockquote>"I drive a 2024 Volvo, home every weekend." — Jamie, driver</blockquote>
    </section>
  );
}
"""

HIRING_BAD_HERO = """
export default function Hero() {
  return <section><h1>About Acme Logistics</h1><p>Founded in 1972.</p></section>;
}
"""

HIRING_BAD_FORM = """
export default function Contact() {
  return (
    <section><form><input name="email" /><button>Submit</button></form></section>
  );
}
"""


def test_hiring_passes_when_complete():
    intent = {
        "primary_purpose": "hiring",
        "named_roles": ["CDL-A Driver", "Owner-Operator"],
    }
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        _scaffold(ws, {
            "src/sections/Hero.jsx":          HIRING_GOOD_HERO,
            "src/sections/Apply.jsx":         HIRING_GOOD_FORM,
            "src/sections/Testimonials.jsx":  HIRING_GOOD_TESTIMONIALS,
        })
        report = evaluate(str(ws), intent)

    assert report["purpose"] == "hiring"
    assert _check(report, "has_application_form")["passed"] is True
    assert _check(report, "has_role_select")["passed"] is True
    assert _check(report, "has_pay_numbers")["passed"] is True
    assert _check(report, "named_roles_rendered")["passed"] is True
    assert _check(report, "testimonials_are_employees")["passed"] is True
    assert report["summary"]["blockers"] == 0


def test_hiring_fails_when_form_missing():
    intent = {
        "primary_purpose": "hiring",
        "named_roles": ["CDL-A Driver"],
    }
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        _scaffold(ws, {
            "src/sections/Hero.jsx": HIRING_BAD_HERO,  # no form, no pay, no role
        })
        report = evaluate(str(ws), intent)

    assert _check(report, "has_application_form")["passed"] is False
    assert _check(report, "has_pay_numbers")["passed"] is False
    assert _check(report, "named_roles_rendered")["passed"] is False
    assert report["summary"]["blockers"] >= 2


def test_hiring_warns_when_role_select_empty():
    """Form exists but doesn't expose the named roles — warning, not blocker."""
    intent = {
        "primary_purpose": "hiring",
        "named_roles": ["CDL-A Driver", "Dispatcher"],
    }
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        _scaffold(ws, {
            "src/sections/Hero.jsx":  HIRING_GOOD_HERO,
            "src/sections/Apply.jsx": HIRING_BAD_FORM,  # form but no role select
        })
        report = evaluate(str(ws), intent)

    assert _check(report, "has_application_form")["passed"] is True
    assert _check(report, "has_role_select")["passed"] is False
    assert _check(report, "has_role_select")["severity"] == "warning"


# ── Lead generation ───────────────────────────────────────────────────

LEAD_GOOD = """
export default function Quote() {
  return (
    <section>
      <h2>Get a quote</h2>
      <form>
        <input name="company" />
        <button>Request a Quote</button>
      </form>
      <p>Trusted by 200+ clients · ISO 9001 certified.</p>
    </section>
  );
}
"""

LEAD_NO_FORM = """
export default function Hero() {
  return <section><h1>We build software.</h1></section>;
}
"""


def test_lead_generation_passes():
    intent = {"primary_purpose": "lead_generation"}
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        _scaffold(ws, {"src/sections/Quote.jsx": LEAD_GOOD})
        report = evaluate(str(ws), intent)

    assert _check(report, "has_lead_form")["passed"] is True
    assert _check(report, "has_trust_signals")["passed"] is True


def test_lead_generation_fails_without_form():
    intent = {"primary_purpose": "lead_generation"}
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        _scaffold(ws, {"src/sections/Hero.jsx": LEAD_NO_FORM})
        report = evaluate(str(ws), intent)

    assert _check(report, "has_lead_form")["passed"] is False
    assert _check(report, "has_lead_form")["severity"] == "blocker"
    assert _check(report, "has_trust_signals")["passed"] is False


# ── Ecommerce ─────────────────────────────────────────────────────────

ECOMMERCE_GOOD = """
export default function Products() {
  return (
    <section>
      <article><h3>Linen Shirt</h3><p>$89.00</p><a>Shop Now</a></article>
      <article><h3>Wool Coat</h3><p>$245.00</p><a>View Details</a></article>
    </section>
  );
}
"""


def test_ecommerce_passes():
    intent = {"primary_purpose": "ecommerce"}
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        _scaffold(ws, {"src/sections/Products.jsx": ECOMMERCE_GOOD})
        report = evaluate(str(ws), intent)

    assert _check(report, "has_product_grid")["passed"] is True


def test_ecommerce_fails_without_prices():
    intent = {"primary_purpose": "ecommerce"}
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        _scaffold(ws, {"src/sections/Hero.jsx": "<h1>Welcome</h1>"})
        report = evaluate(str(ws), intent)

    assert _check(report, "has_product_grid")["passed"] is False


# ── Booking ───────────────────────────────────────────────────────────

BOOKING_GOOD = """
export default function Reserve() {
  return (
    <section>
      <h2>Book a table</h2>
      <form>
        <input name="date" type="date" />
        <button>Reserve →</button>
      </form>
    </section>
  );
}
"""


def test_booking_passes():
    intent = {"primary_purpose": "booking"}
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        _scaffold(ws, {"src/sections/Reserve.jsx": BOOKING_GOOD})
        report = evaluate(str(ws), intent)

    assert _check(report, "has_booking_form")["passed"] is True


def test_booking_fails_without_form():
    intent = {"primary_purpose": "booking"}
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        _scaffold(ws, {"src/sections/Menu.jsx": "<h1>Our menu</h1>"})
        report = evaluate(str(ws), intent)

    assert _check(report, "has_booking_form")["passed"] is False


# ── Edge cases ────────────────────────────────────────────────────────

def test_unknown_purpose_returns_empty_checks():
    intent = {"primary_purpose": "fundraising"}  # not in _CHECKERS
    with tempfile.TemporaryDirectory() as tmp:
        report = evaluate(tmp, intent)
    assert report["purpose"] == "fundraising"
    assert report["checks"] == []
    assert report["summary"]["total"] == 0


def test_missing_workspace_dir_does_not_crash():
    intent = {"primary_purpose": "hiring", "named_roles": []}
    # Path that doesn't exist — _read_workspace falls back to workspace_path itself
    report = evaluate("/tmp/__lucid_does_not_exist_12345__", intent)
    # Each blocker check should fail (no files found) but no exception.
    assert report["summary"]["blockers"] >= 1


# ── Manual runner ─────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_hiring_passes_when_complete,
        test_hiring_fails_when_form_missing,
        test_hiring_warns_when_role_select_empty,
        test_lead_generation_passes,
        test_lead_generation_fails_without_form,
        test_ecommerce_passes,
        test_ecommerce_fails_without_prices,
        test_booking_passes,
        test_booking_fails_without_form,
        test_unknown_purpose_returns_empty_checks,
        test_missing_workspace_dir_does_not_crash,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {t.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
