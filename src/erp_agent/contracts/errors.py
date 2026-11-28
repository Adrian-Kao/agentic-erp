from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[dict[str, str]] = Field(default_factory=list)
    retryable: bool = False


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable

    def response(self, request_id: str) -> ErrorResponse:
        return ErrorResponse(
            code=self.code, message=self.message, request_id=request_id, retryable=self.retryable
        )
