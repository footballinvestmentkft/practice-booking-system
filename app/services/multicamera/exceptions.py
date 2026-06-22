class SessionNotFoundError(Exception):
    pass


class DeviceNotFoundError(Exception):
    pass


class ParticipantNotFoundError(Exception):
    pass


class InvalidTransitionError(Exception):
    def __init__(self, entity: str, current: str, target: str):
        self.entity = entity
        self.current = current
        self.target = target
        super().__init__(f"{entity}: cannot transition {current} → {target}")


class RevisionConflictError(Exception):
    def __init__(self, entity: str, expected: int, actual: int):
        self.entity = entity
        self.expected = expected
        self.actual = actual
        super().__init__(f"{entity}: revision conflict (expected {expected}, actual {actual})")


class SessionFullError(Exception):
    def __init__(self, limit_type: str, current: int, maximum: int):
        self.limit_type = limit_type
        self.current = current
        self.maximum = maximum
        super().__init__(f"{limit_type}: {current}/{maximum}")


class CrossSessionReferenceError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class DeviceRoleViolationError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)
