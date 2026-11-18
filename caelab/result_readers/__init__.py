"""Headless numeric readers for registered native result profiles."""


class ResultError(ValueError):
    """A result cannot be produced with the requested provenance or coverage."""

    def __init__(self, code: str, detail: str, *, availability: str | None = None):
        self.code = code
        self.detail = detail
        self.availability = availability
        super().__init__(f"{code}: {detail}")


def no_checkpoint() -> None:
    pass
