"""User-facing exception types for the posterize CLI and pipeline."""

from __future__ import annotations


class UserFacingError(RuntimeError):
    """Expected error that should be shown by the CLI without a traceback."""


class ExternalToolError(UserFacingError):
    """External tool discovery or execution failed."""


class InputValidationError(UserFacingError, ValueError):
    """User-supplied file or dimension validation failed."""


class OutputAssemblyError(UserFacingError):
    """Rendered assets could not be assembled into a 3MF project."""
