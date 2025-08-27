class BaseSettings:
    pass

def Field(default=None, default_factory=None):
    if default_factory is not None:
        return default_factory()
    return default
