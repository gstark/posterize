"""Discovery and execution helpers for OpenSCAD and potrace."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from posterize.errors import ExternalToolError
from posterize.paths import Pathish

OPENSCAD = "openscad"
POTRACE = "potrace"
OPENSCAD_ENV_VAR = "POSTERIZE_OPENSCAD"
POTRACE_ENV_VAR = "POSTERIZE_POTRACE"


@dataclass(frozen=True)
class ToolOverrides:
    """Optional paths supplied by CLI/config before environment discovery."""

    openscad: str | None = None
    potrace: str | None = None


@dataclass(frozen=True)
class ExternalTools:
    """Resolved external tool executables used by rendering/vectorization."""

    openscad: str
    potrace: str


def _candidate_source(
    tool_name: str,
    cli_option: str,
    env_var: str,
    override: str | None,
    environ: Mapping[str, str],
) -> tuple[str, str]:
    if override is not None:
        return override, f"{cli_option}/config"
    if env_var in environ:
        return environ[env_var], env_var
    return tool_name, "PATH"


def _validate_tool_request(tool_name: str, requested: object, source: str) -> str:
    if not isinstance(requested, str):
        raise ExternalToolError(f"{tool_name} from {source} must be a path string")
    if not requested.strip():
        raise ExternalToolError(f"{tool_name} from {source} must not be empty")
    if "\0" in requested:
        raise ExternalToolError(f"{tool_name} from {source} must not contain NUL bytes")
    return requested


def _resolve_tool(
    tool_name: str,
    cli_option: str,
    env_var: str,
    override: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    environ_was_supplied = environ is not None
    environ = os.environ if environ is None else environ
    requested, source = _candidate_source(
        tool_name, cli_option, env_var, override, environ
    )
    requested = _validate_tool_request(tool_name, requested, source)
    search_path = environ.get("PATH", "") if environ_was_supplied else None
    resolved = shutil.which(requested, path=search_path)
    requested_path = Path(requested)
    if resolved is None and (
        requested_path.is_absolute() or requested_path.parent != Path("")
    ):
        if requested_path.is_file() and os.access(requested_path, os.X_OK):
            resolved = os.fspath(requested_path)
    if resolved is None:
        raise ExternalToolError(
            f"Install {tool_name} or provide {cli_option} / set {env_var}; "
            f"could not resolve {requested!r} from {source}."
        )
    return resolved


def discover_external_tools(
    overrides: ToolOverrides | None = None,
    environ: Mapping[str, str] | None = None,
) -> ExternalTools:
    """Resolve OpenSCAD and potrace executables from config, env, then PATH."""
    overrides = overrides or ToolOverrides()
    return ExternalTools(
        openscad=_resolve_tool(
            OPENSCAD,
            "--openscad",
            OPENSCAD_ENV_VAR,
            override=overrides.openscad,
            environ=environ,
        ),
        potrace=_resolve_tool(
            POTRACE,
            "--potrace",
            POTRACE_ENV_VAR,
            override=overrides.potrace,
            environ=environ,
        ),
    )


def _command_text(cmd: Sequence[object]) -> str:
    return " ".join(shlex.quote(str(part)) for part in cmd)


def _process_output_block(label: str, value: object | None) -> str:
    text = "" if value is None else str(value).strip()
    return f"{label}:\n{text or '(empty)'}"


def _external_tool_message(
    summary: str,
    cmd: Sequence[object],
    result: object | None = None,
) -> str:
    lines = [summary, f"command: {_command_text(cmd)}"]
    if result is not None:
        lines.extend(
            [
                _process_output_block("stdout", getattr(result, "stdout", "")),
                _process_output_block("stderr", getattr(result, "stderr", "")),
            ]
        )
    return "\n".join(lines)


def _remove_stale_output(output_path: Pathish | None, tool_name: str) -> None:
    if output_path is None:
        return
    path = Path(output_path)
    if path.exists() or path.is_symlink():
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ExternalToolError(
                f"cannot remove stale {tool_name} output {path}: {exc}"
            ) from None


def _run_external_command(
    tool_name: str,
    cmd: Sequence[str],
    output_path: Pathish | None = None,
    empty_output_ok: bool = False,
) -> subprocess.CompletedProcess[str]:
    _remove_stale_output(output_path, tool_name)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise ExternalToolError(
            _external_tool_message(f"failed to start {tool_name}: {exc}", cmd)
        ) from None

    returncode = getattr(result, "returncode", None)
    if type(returncode) is not int:
        raise ExternalToolError(
            _external_tool_message(
                f"{tool_name} returned invalid exit status {returncode!r}",
                cmd,
                result,
            )
        )
    if returncode != 0:
        stderr = getattr(result, "stderr", "") or ""
        if empty_output_ok and "empty" in stderr.lower():
            return result
        raise ExternalToolError(
            _external_tool_message(
                f"{tool_name} failed with exit code {returncode}",
                cmd,
                result,
            )
        )
    return result
