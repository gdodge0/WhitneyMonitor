class WhitneyMonitorException(Exception):
    """Base class for exceptions in MyProject."""
    pass


class SigninError(WhitneyMonitorException):
    """Custom exception class for specific errors."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details

    def __str__(self):
        return f"{self.__class__.__name__}: {self.args[0]} - Details: {self.details}"
