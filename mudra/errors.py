"""Error types surfaced to API clients."""


class MudraError(Exception):
    status = 400

    def __init__(self, message, status=None):
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status


class ValidationError(MudraError):
    status = 400


class NotFound(MudraError):
    status = 404


class Conflict(MudraError):
    status = 409
