"""Error classes the worker uses to decide what happens to a job."""


class TransientError(Exception):
    """A dependency (e.g. Ollama) is unavailable: retry later without counting an attempt."""


class PermanentError(Exception):
    """Retrying cannot help (blocked address, 404, not an article): fail the job now."""
