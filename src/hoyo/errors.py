"""User-facing errors. CLI maps these to exit code 1."""


class HoyoError(Exception):
    """Errors that should be printed as a message, not a traceback."""


class UnknownGameError(HoyoError):
    pass


class VersionNotFoundError(HoyoError):
    pass


class VersionNotInstalledError(HoyoError):
    pass


class SourceNotConfiguredError(HoyoError):
    pass


class HashMismatchError(HoyoError):
    pass


class MissingBlobError(HoyoError):
    pass


class LaunchError(HoyoError):
    pass


class InsufficientSpaceError(HoyoError):
    pass
