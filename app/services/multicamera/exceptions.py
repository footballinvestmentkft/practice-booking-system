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


class CycleNotFoundError(Exception):
    pass


class CycleConflictError(Exception):
    """Raised when cycle creation is blocked.

    Two cases:
    - A non-terminal cycle already exists for this session (different idempotency key).
    - Idempotency key collision that resolves to a missing row (safety net).
    """
    pass


class DeviceNotReadyError(Exception):
    """One or more required devices are not in 'ready' status at schedule time."""
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class NoCycleDevicesError(Exception):
    """Session has no non-removed devices to snapshot into the cycle."""
    pass


class InstructorRequiredError(Exception):
    """Operation requires the caller to be the session instructor."""
    pass
