class BaseSettings:
    pass


class BaseModel:
    """Minimal stand-in for ``pydantic.BaseModel`` used in tests.

    The implementation only supports instantiation via keyword arguments and
    the ``dict`` method for serialization which is sufficient for the unit
    tests in this kata.  Validation and other advanced features of Pydantic are
    intentionally omitted to keep the test environment lightweight.
    """

    def __init__(self, **data):
        for key, value in data.items():
            setattr(self, key, value)

    def dict(self):  # pragma: no cover - trivial
        return self.__dict__.copy()


def Field(default=None, default_factory=None):
    if default_factory is not None:
        return default_factory()
    return default

