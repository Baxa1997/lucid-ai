"""Cheap pre-checks on the user's raw prompt before any Gemini call.

Two guards live here:

  • is_gibberish(text)  — detect keyboard-mash / random-character input.
    Catches "asdasdas", "qwertyqwe", "ksjdfksdf" cheaply (regex + word
    shape rules). Fast and free; runs before analyze_intent so we don't
    burn a Gemini call to discover the prompt is meaningless.

  • detect_scope_warnings(text) — detect requests for features outside
    the landing-page generator's current scope (multi-page apps, auth
    flows, admin panels, custom backends). The pipeline today produces
    ONE landing page; if the user described a full app, we want to be
    honest about it rather than silently generate just the landing.

Both functions are pure / synchronous / no I/O. Tested via the bottom of
this file; importable from landing_pipeline.py.
"""
from __future__ import annotations

import re
from typing import Iterable


# ── Gibberish detector ────────────────────────────────────────────────

# A token is "wordlike" if it looks like a real word from any language
# that uses Latin script. Heuristic only — false positives on rare
# words are fine because the downstream Gemini call will still catch
# vague-but-real prompts via clarity_level=low. We only need to catch
# the OBVIOUS cases here.
_VOWEL_RE = re.compile(r"[aeiouAEIOU]")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z\-']*")
# 2-4 char substring repeating 3+ times: "asdasdasdas", "loloolol", "qweqweqwe".
# Anchored to start to catch leading repeats even when the tail diverges
# ("asdasdasdas" = "asd"×3 + "as" trailing).
_REPEAT_RE = re.compile(r"^(.{2,4})\1{2,}")


def _is_wordlike(token: str) -> bool:
    """Return True if a token looks like it could be a real word.

    A token is *not* wordlike when it:
      • has no vowels (e.g. "qwrtypzx")
      • is a short repeat pattern ("asdasdasdas" — "asd" × 4)
      • has too few vowels relative to its length
    """
    if not token:
        return False
    if len(token) <= 2:
        # 1-2 char tokens are too short to judge — assume wordlike
        # ("a", "an", "of", "to", "we", "I"). Avoids false positives
        # on connectors.
        return True
    if not _VOWEL_RE.search(token):
        return False
    if _REPEAT_RE.search(token):
        return False
    # Vowel density check — real words sit around 30-50% vowels;
    # qwerty mash sits well below. Threshold tuned for "qwertyzxcv"
    # (e is the only true vowel) and similar keyboard-row inputs.
    vowels = sum(1 for c in token if c in "aeiouAEIOU")
    if vowels / len(token) < 0.22:
        return False
    return True


def is_gibberish(text: str) -> bool:
    """Return True when *text* looks like keyboard mash / random chars.

    Conservative on purpose — only flags clear cases. If a prompt is
    vague-but-real ("a website"), this returns False and the downstream
    intent classifier handles disambiguation via clarification_questions.
    """
    if text is None:
        return False
    stripped = text.strip()
    if not stripped:
        # Empty input is handled elsewhere (the UI shouldn't allow it,
        # but if we get here, treat as gibberish so we ask for input).
        return True

    # Long inputs are unlikely to be pure gibberish — even rambling
    # prompts contain enough real words to be worth research.
    if len(stripped) > 120:
        return False

    tokens = _TOKEN_RE.findall(stripped)
    if not tokens:
        # No alphabetic characters at all (pure numbers / symbols /
        # CJK without latin). Treat as gibberish for the landing
        # generator — it cannot derive intent.
        return True

    wordlike = sum(1 for t in tokens if _is_wordlike(t))
    # Need at least one wordlike token, AND wordlike tokens must
    # outweigh non-wordlike when the prompt is short.
    if wordlike == 0:
        return True
    if len(tokens) <= 4 and wordlike < len(tokens) / 2:
        return True
    return False


# ── Out-of-scope feature detector ─────────────────────────────────────

# Each entry: (warning_key, regex pattern). Regex is matched case-insens.
# Anchored to whole-word boundaries so "login" matches but "blogin" does not.
_SCOPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "auth_requested",
        re.compile(
            r"\b(login|log[-\s]?in|sign[-\s]?in|sign[-\s]?up|signup|"
            r"forgot[-\s]?password|password\s+reset|reset\s+password|"
            r"oauth|sso|magic[-\s]?link|two[-\s]?factor|2fa|mfa)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "dashboard_requested",
        re.compile(
            r"\b(dashboard|admin\s+panel|control\s+panel|"
            r"user\s+portal|customer\s+portal|management\s+console)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "billing_requested",
        re.compile(
            r"\b(stripe|paddle|chargebee|subscription\s+billing|"
            r"billing\s+page|payment\s+gateway|recurring\s+payments)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "crud_requested",
        re.compile(
            r"\b(crud|create[-\s]?read[-\s]?update[-\s]?delete|"
            r"data\s+table|record\s+management|entity\s+management)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "multi_page_requested",
        re.compile(
            r"\b(multi[-\s]?page|multiple\s+pages|several\s+pages|"
            r"5\s+pages|10\s+pages|sitemap|whole\s+website|full\s+website|"
            r"entire\s+site)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "multi_tenant_requested",
        re.compile(
            r"\b(multi[-\s]?tenant|tenancy|workspaces?|"
            r"organizations?\s+layer|team\s+accounts)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "backend_requested",
        re.compile(
            r"\b(custom\s+api|rest\s+api|graphql|database\s+schema|"
            r"server[-\s]?side\s+(logic|code)|microservices?|"
            r"webhooks?\s+endpoint)\b",
            re.IGNORECASE,
        ),
    ),
]


def detect_scope_warnings(text: str) -> list[str]:
    """Return a list of scope-warning keys triggered by *text*.

    Example:
        >>> detect_scope_warnings("landing page + login + Stripe billing")
        ['auth_requested', 'billing_requested']

    Empty list means the prompt is within the landing-page generator's
    current scope.
    """
    if not text:
        return []
    triggered: list[str] = []
    for key, pat in _SCOPE_PATTERNS:
        if pat.search(text):
            triggered.append(key)
    return triggered


# ── Self-test ─────────────────────────────────────────────────────────
# Run `python3 prompt_guards.py` to exercise the heuristics.

_GIBBERISH_CASES: list[tuple[str, bool]] = [
    ("asdasdasdas",                  True),   # repeat pattern
    ("dasdasdasdas",                 True),   # repeat pattern
    ("qwertyzxcv",                   True),   # no real vowels in cluster — actually has e... edge
    ("ksjdhfksjdhf",                 True),   # repeat
    ("lol",                          False),  # short, has vowel — give benefit of doubt
    ("a website",                    False),  # vague but real
    ("italian restaurant",           False),  # real
    ("Florence trattoria landing page", False),
    ("",                             True),   # empty
    ("    ",                         True),   # whitespace only
    ("123456",                       True),   # no letters
    ("a",                            False),  # 1 char — too short to judge
]

_SCOPE_CASES: list[tuple[str, list[str]]] = [
    ("Italian restaurant in Florence", []),
    ("SaaS dashboard with login and stripe billing", ["auth_requested", "dashboard_requested", "billing_requested"]),
    ("Build admin panel for orders", ["dashboard_requested"]),
    ("Multi-tenant SaaS with workspaces", ["multi_tenant_requested"]),
    ("Landing + signup form", ["auth_requested"]),
    ("Whole website with 10 pages", ["multi_page_requested"]),
]


if __name__ == "__main__":
    print("=== is_gibberish ===")
    for text, expected in _GIBBERISH_CASES:
        got = is_gibberish(text)
        mark = "OK  " if got == expected else "FAIL"
        print(f"{mark} {got!s:<5}  expected={expected!s:<5}  {text!r}")

    print("\n=== detect_scope_warnings ===")
    for text, expected in _SCOPE_CASES:
        got = detect_scope_warnings(text)
        mark = "OK  " if set(got) == set(expected) else "FAIL"
        print(f"{mark} got={got}  expected={expected}  text={text!r}")
