"""종료 코드를 담는 예외."""

from packet_ask import codes


class PacketAskError(Exception):
    """사용자에게 보여줄 메시지와 종료 코드."""

    def __init__(self, message: str, code: int = codes.INTERNAL) -> None:
        super().__init__(message)
        self.code = code


class PolicyError(PacketAskError):
    def __init__(self, message: str) -> None:
        super().__init__(message, codes.POLICY)


class ScopeError(PacketAskError):
    def __init__(self, message: str) -> None:
        super().__init__(message, codes.SCOPE)


class BudgetError(PacketAskError):
    def __init__(self, message: str) -> None:
        super().__init__(message, codes.BUDGET)


class RedactionFailed(PacketAskError):
    def __init__(self, message: str) -> None:
        super().__init__(message, codes.REDACTION)
