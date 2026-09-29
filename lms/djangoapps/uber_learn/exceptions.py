"""
Custom exceptions for the uber_learn app.
"""


class CooldownError(Exception):
    """
    Raised when an assessment attempt is blocked by a cooldown period.

    Attributes:
        wait_seconds: Number of seconds the user must wait before retrying.
    """

    def __init__(self, wait_seconds: int) -> None:
        self.wait_seconds = wait_seconds
        super().__init__(f"Assessment cooldown: {wait_seconds}s remaining")
