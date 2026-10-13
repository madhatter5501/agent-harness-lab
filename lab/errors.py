"""Expected control-flow errors. They are distinct from infrastructure failures."""


class InvalidAnswer(ValueError):
    """The model may correct this answer, within the recovery budget."""


class NeedsClarification(ValueError):
    """Pause and show this question to the user; do not execute the proposed tool."""
