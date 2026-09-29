# SPDX-License-Identifier: AGPL-3.0-or-later
"""Permission catalog and policy editing for cpak overrides.

A policy is the JSON object cpak stores as an override (``types.Override`` in
cpak). An override replaces the manifest policy as a whole, so pakseal always
edits a complete copy and writes all of it back. Fields that pakseal does not
show, such as the session bus rules, are kept as they are.
"""

from __future__ import annotations

import copy
import posixpath
import re
from dataclasses import dataclass

XDG_DIRECTORIES = (
    "xdg-desktop",
    "xdg-documents",
    "xdg-download",
    "xdg-music",
    "xdg-pictures",
    "xdg-public-share",
    "xdg-templates",
    "xdg-videos",
)

ACCESS_READ_ONLY = "read-only"
ACCESS_READ_WRITE = "read-write"

FILE_PICKER_MODES = ("filePicker.openFile", "filePicker.openFolder", "filePicker.saveFile")


@dataclass(frozen=True)
class Toggle:
    """One on/off permission, addressed by a dotted key into the policy."""

    key: str
    title: str
    subtitle: str = ""
    # The toggle can only be on while this key is on.
    requires: str | None = None
    # Shown with a warning style because it weakens the sandbox considerably.
    risky: bool = False


@dataclass(frozen=True)
class Number:
    key: str
    title: str
    subtitle: str
    maximum: int
    unit: str = ""


@dataclass(frozen=True)
class Group:
    title: str
    description: str = ""
    toggles: tuple[Toggle, ...] = ()
    numbers: tuple[Number, ...] = ()
    # Only shown when the manifest or the current policy enables one of them.
    legacy: bool = False


@dataclass(frozen=True)
class HostAction:
    provider: str
    capability: str
    title: str
    subtitle: str
    risky: bool = False


GROUPS: tuple[Group, ...] = (
    Group(
        "Display",
        toggles=(
            Toggle("socketWayland", "Wayland", "Show windows on the Wayland display. The compositor also shares the clipboard."),
            Toggle("displayX11", "X11 compatibility", "Run an isolated, nested X11 display"),
            Toggle("clipboard.hostToApp", "Read the host clipboard", "Through the X11 compatibility display", requires="displayX11"),
            Toggle("clipboard.appToHost", "Write the host clipboard", "Through the X11 compatibility display", requires="displayX11"),
        ),
    ),
    Group(
        "Sound and Media",
        toggles=(
            Toggle("socketPulseAudio", "PulseAudio", "Play and record sound"),
            Toggle("deviceAlsa", "ALSA devices", "Direct access to sound cards"),
            Toggle("deviceVideo", "Cameras", "Video capture devices"),
        ),
    ),
    Group(
        "Network",
        toggles=(
            Toggle("network", "Network access", "Internet and local network through a private network namespace"),
            Toggle(
                "hostNetwork",
                "Share the host network",
                "Includes localhost services and host ports",
                requires="network",
                risky=True,
            ),
        ),
    ),
    Group(
        "Desktop Integration",
        toggles=(
            Toggle("notification", "Notifications", "Send desktop notifications"),
            Toggle("openURI", "Open links and files", "Hand URIs to host applications"),
            Toggle("hostApplications", "Launch host applications", "List and start host desktop applications"),
            Toggle("bluetooth", "Bluetooth", "The BlueZ service through a private proxy"),
            Toggle("socketCups", "Printing", "The CUPS socket"),
            Toggle("socketSshAgent", "SSH agent", "Authenticate with your SSH keys", risky=True),
            Toggle("socketGpgAgent", "GPG agent", "Sign and decrypt with your GPG keys", risky=True),
        ),
    ),
    Group(
        "File Chooser",
        "Let the application ask you for files instead of granting folders in advance.",
        toggles=(
            Toggle("filePicker.openFile", "Open files"),
            Toggle("filePicker.openFolder", "Open folders"),
            Toggle("filePicker.saveFile", "Save files"),
            Toggle("filePicker.persistent", "Remember choices", "Offer grants that last across launches", requires="filePicker.*"),
            Toggle(
                "filePicker.containingFolder",
                "Offer the containing folder",
                "Let you grant the folder of an opened file",
                requires="filePicker.openFile",
            ),
        ),
    ),
    Group(
        "Devices",
        toggles=(
            Toggle("deviceDri", "GPU acceleration", "/dev/dri and NVIDIA devices"),
            Toggle("deviceShm", "Shared memory", "/dev/shm"),
            Toggle("deviceKvm", "Virtualization", "/dev/kvm"),
            Toggle("deviceUsb", "USB devices", "Raw USB access"),
            Toggle("deviceSerial", "Serial ports", "USB and CDC serial devices"),
            Toggle("deviceInput", "Input devices", "Keyboards, mice and game controllers", risky=True),
            Toggle("deviceFuse", "FUSE", "Mount user-space file systems"),
            Toggle("deviceTun", "TUN/TAP", "Virtual network interfaces"),
            Toggle("deviceTTY", "Terminal", "The controlling terminal"),
            Toggle("deviceAll", "All devices", "The whole of /dev", risky=True),
        ),
    ),
    Group(
        "Sandbox",
        toggles=(
            Toggle("process", "Share host processes", "See and signal processes outside the sandbox", risky=True),
            Toggle("userNamespaces", "Nested sandboxes", "Allow user namespaces. Disables Landlock for this application.", risky=True),
            Toggle("asRoot", "Run as root", "Root inside the sandbox, not on the host"),
        ),
    ),
    Group(
        "Resources",
        "0 means no limit.",
        numbers=(
            Number("memoryMaxMB", "Memory", "Maximum memory", 1 << 20, "MiB"),
            Number("cpuQuota", "CPU", "Percentage of one core", 1000, "%"),
            Number("pidsMax", "Processes", "Maximum number of processes", 1 << 22),
        ),
    ),
    Group(
        "Legacy Permissions",
        "These permissions come from older manifests. cpak may ignore or refuse them.",
        legacy=True,
        toggles=(
            Toggle("socketX11", "Host X11 socket", "No input or screen isolation", risky=True),
            Toggle("socketSessionBus", "Raw session bus", risky=True),
            Toggle("socketSystemBus", "Raw system bus", risky=True),
            Toggle("socketAtSpiBus", "Accessibility bus"),
            Toggle("socketBluetooth", "Bluetooth socket"),
            Toggle("fsHost", "Host file system", "Read-only"),
            Toggle("fsHostEtc", "Host /etc"),
            Toggle("fsHostHome", "Home folder", "Read and write", risky=True),
        ),
    ),
)

HOST_ACTIONS: tuple[HostAction, ...] = (
    HostAction("containers", "read", "Inspect host containers", "Podman and Docker"),
    HostAction("containers", "manage-owned", "Manage its own containers", "Create, stop and remove containers it owns"),
    HostAction("containers", "exec-owned", "Run commands in its own containers", ""),
    HostAction("cpak", "read", "Read cpak packages", "Packages, environments and their processes"),
    HostAction("cpak", "manage", "Manage cpak environments", "Install distribution packages and change environments"),
    HostAction("cpak", "exec", "Run commands in cpak environments", ""),
    HostAction(
        "cpak",
        "permissions",
        "Change permissions of other applications",
        "Includes granting them host access",
        risky=True,
    ),
)

_DEFAULTS = {
    "memoryMaxMB": 0,
    "cpuQuota": 0,
    "pidsMax": 0,
}


def get(policy: dict, key: str):
    """Return the value at a dotted key, or its default when absent."""
    value = policy
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return _DEFAULTS.get(key, False)
        value = value[part]
    if value is None:
        return _DEFAULTS.get(key, False)
    return value


def with_value(policy: dict, key: str, value) -> dict:
    """Return a normalized copy of policy with key set to value."""
    updated = copy.deepcopy(policy)
    parts = key.split(".")
    target = updated
    for part in parts[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            child = {}
            target[part] = child
        target = child
    target[parts[-1]] = value
    return normalize(updated)


def available(policy: dict, toggle: Toggle) -> bool:
    """Whether the toggle can be switched on in this policy."""
    if toggle.requires is None:
        return True
    if toggle.requires == "filePicker.*":
        return any(get(policy, mode) for mode in FILE_PICKER_MODES)
    return bool(get(policy, toggle.requires))


def normalize(policy: dict) -> dict:
    """Switch off everything whose requirement is off.

    cpak refuses these combinations, so pakseal resolves them the way the
    user most likely meant: turning a permission off also turns off what
    depended on it.
    """
    for group in GROUPS:
        for toggle in group.toggles:
            if get(policy, toggle.key) and not available(policy, toggle):
                parent, _, name = toggle.key.rpartition(".")
                target = policy[parent] if parent else policy
                target[name] = False
    return policy


def host_action_enabled(policy: dict, provider: str, capability: str) -> bool:
    for grant in policy.get("hostActions") or []:
        if grant.get("provider") == provider:
            return capability in (grant.get("capabilities") or [])
    return False


def with_host_action(policy: dict, provider: str, capability: str, enabled: bool) -> dict:
    updated = copy.deepcopy(policy)
    grants = [dict(grant) for grant in updated.get("hostActions") or []]
    for grant in grants:
        if grant.get("provider") == provider:
            capabilities = set(grant.get("capabilities") or [])
            break
    else:
        grant = {"provider": provider, "capabilities": []}
        grants.append(grant)
        capabilities = set()
    if enabled:
        capabilities.add(capability)
    else:
        capabilities.discard(capability)
    grant["capabilities"] = sorted(capabilities)
    grants = [grant for grant in grants if grant["capabilities"]]
    if grants:
        updated["hostActions"] = grants
    else:
        updated.pop("hostActions", None)
    return updated


def legacy_in_use(*policies: dict) -> bool:
    for group in GROUPS:
        if group.legacy:
            return any(get(policy, toggle.key) for policy in policies for toggle in group.toggles)
    return False


# Filesystem permissions


def valid_filesystem_path(path: str) -> bool:
    """Mirror cpak's validFilesystemPath."""
    if path in ("host", "home") or path in XDG_DIRECTORIES:
        return True
    if path.startswith("home/"):
        relative = path[len("home/"):]
        return (
            relative != ""
            and not relative.startswith("/")
            and _clean(relative) == relative
            and relative not in (".", "..")
            and not relative.startswith("../")
        )
    return path != "/" and path.startswith("/") and _clean(path) == path


def _clean(path: str) -> str:
    cleaned = posixpath.normpath(path)
    # normpath keeps a leading "//", Go's filepath.Clean does not.
    if cleaned.startswith("//"):
        cleaned = "/" + cleaned.lstrip("/")
    return cleaned


def filesystem_error(entries: list[dict], path: str, access: str) -> str | None:
    """Explain why a new filesystem entry would be refused, or return None."""
    if not valid_filesystem_path(path):
        return "Use home, home/…, host, an xdg- folder or a clean absolute path"
    if path == "host" and access != ACCESS_READ_ONLY:
        return "The host file system can only be shared read-only"
    if any(entry.get("path") == path for entry in entries):
        return "This location is already listed"
    return None


def filesystem(policy: dict) -> list[dict]:
    return [dict(entry) for entry in policy.get("filesystem") or []]


def with_filesystem(policy: dict, entries: list[dict]) -> dict:
    updated = copy.deepcopy(policy)
    if entries:
        updated["filesystem"] = [{"path": entry["path"], "access": entry["access"]} for entry in entries]
    else:
        updated.pop("filesystem", None)
    return updated


def filesystem_risky(entry: dict) -> bool:
    """Whether a grant lets the application reach code that runs on the host.

    Writing to the home folder, including its hidden configuration folders,
    lets an application change startup files that later run outside cpak.
    """
    path, access = entry.get("path", ""), entry.get("access")
    if path == "host":
        return True
    return access == ACCESS_READ_WRITE and (path == "home" or path.startswith("home/.") or path in ("/etc", "/usr"))


def describe_location(path: str) -> str:
    if path == "home":
        return "Home folder"
    if path == "host":
        return "Host file system"
    if path.startswith("home/"):
        return "~/" + path[len("home/"):]
    if path in XDG_DIRECTORIES:
        return path[len("xdg-"):].replace("-", " ").capitalize() + " folder"
    return path


# Environment variables

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def environment_error(entries: list[str], entry: str) -> str | None:
    name, separator, _ = entry.partition("=")
    if not separator or not _ENVIRONMENT_NAME.match(name):
        return "Use NAME=value"
    if any(ch in entry for ch in "\x00\r\n"):
        return "Values must be on one line"
    if any(existing.partition("=")[0] == name for existing in entries):
        return f"{name} is already set"
    return None


def environment(policy: dict) -> list[str]:
    return list(policy.get("env") or [])


def with_environment(policy: dict, entries: list[str]) -> dict:
    updated = copy.deepcopy(policy)
    if entries:
        updated["env"] = list(entries)
    else:
        updated.pop("env", None)
    return updated


# Session bus rules are kept but not edited here.


def session_bus_rules(policy: dict) -> int:
    bus = policy.get("sessionBus") or {}
    return sum(len(value) for value in bus.values() if isinstance(value, list))


@dataclass
class Application:
    """One installed version as reported by ``cpak permissions list``."""

    origin: str
    name: str
    version: str
    manifest: dict
    override: dict | None
    override_error: str = ""
    pulled_in: bool = False

    @classmethod
    def from_json(cls, data: dict) -> "Application":
        return cls(
            origin=data["origin"],
            name=data.get("name") or data["origin"],
            version=data.get("version", ""),
            manifest=data.get("manifest") or {},
            override=data.get("override"),
            override_error=data.get("override_error", ""),
            pulled_in=bool(data.get("pulled_in")),
        )

    @property
    def key(self) -> tuple[str, str]:
        return (self.origin, self.version)

    @property
    def policy(self) -> dict:
        """The policy the application runs under, before any host ceiling."""
        return self.override if self.override is not None else self.manifest

    @property
    def customized(self) -> bool:
        return self.override is not None
