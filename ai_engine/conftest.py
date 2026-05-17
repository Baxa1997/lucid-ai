"""Project-wide pytest configuration.

Registers the `live` marker so tests that hit real external services
(Gemini, Supabase) can be opted into or out of via `-m live` or
`-m "not live"`. Without registering, pytest 8+ warns about unknown
markers.
"""
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: test exercises a real external service (Gemini, Supabase). "
        "Costs money + requires credentials. Deselect with -m 'not live'.",
    )
