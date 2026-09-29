# SPDX-License-Identifier: AGPL-3.0-or-later
"""Access to cpak's permission commands.

Inside cpak, the ``cpak-host`` shim forwards a fixed set of commands to the
host through the system broker, which requires the ``permissions`` capability
of the ``cpak`` host action provider. Outside cpak, for development, the same
commands run on the ``cpak`` binary directly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from .policy import Application

TIMEOUT = 60


class CpakError(Exception):
    pass


def default_command() -> list[str]:
    configured = os.environ.get("PAKSEAL_CPAK")
    if configured:
        return configured.split()
    for name in ("cpak-host", "cpak"):
        path = shutil.which(name)
        if path:
            return [path]
    raise CpakError("Neither cpak-host nor cpak was found. Is pakseal running inside cpak with the permissions capability?")


class CpakHost:
    def __init__(self, command: list[str] | None = None):
        self._command = command

    @property
    def command(self) -> list[str]:
        if self._command is None:
            self._command = default_command()
        return self._command

    def list(self) -> list[Application]:
        data = self._run(["permissions", "list", "--json"])
        if not isinstance(data, list):
            raise CpakError("cpak returned an unexpected application list")
        return [Application.from_json(entry) for entry in data]

    def set(self, origin: str, version: str, policy: dict) -> Application:
        data = self._run(
            ["permissions", "set", "--origin", origin, "--package-version", version, "--policy", "-", "--json"],
            json.dumps(policy),
        )
        return Application.from_json(data)

    def reset(self, origin: str, version: str) -> Application:
        data = self._run(["permissions", "reset", "--origin", origin, "--package-version", version, "--json"])
        return Application.from_json(data)

    def _run(self, arguments: list[str], stdin: str | None = None):
        try:
            completed = subprocess.run(
                self.command + arguments,
                input=stdin if stdin is not None else "",
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CpakError(str(error)) from error
        if completed.returncode != 0:
            raise CpakError(_error_message(completed.stderr) or f"cpak exited with status {completed.returncode}")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise CpakError("cpak returned output that is not JSON") from error


def _error_message(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return ""
    message = lines[-1]
    for prefix in ("host action failed: ", "Error: ", "error: "):
        if message.startswith(prefix):
            message = message[len(prefix):]
    return message
