"""Custom exception types for event processing."""


class RetriableError(Exception):
    """Raise from event handlers to signal a transient failure that should be retried.

    Examples: downstream API timeout, temporary 503, connection reset.
    Any other exception is treated as a permanent failure.
    """
