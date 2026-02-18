"""Application-specific exceptions.

Raise these from service code; the router layer catches them
and maps to the appropriate HTTP status.
"""


class SessionNotFoundError(Exception):
    """Raised when a session ID does not exist in the store."""


class ProviderError(ValueError):
    """Raised for invalid / unsupported model provider."""


class APIKeyMissingError(ValueError):
    """Raised when no API key can be resolved for a provider."""


class APIKeyInvalidError(Exception):
    """Raised when the upstream LLM rejects the API key."""
