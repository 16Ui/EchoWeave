from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DockerSandboxProfile:
    """Container-level command sandbox policy.

    The profile is intentionally deterministic and easy to audit: it only
    mounts the active workspace and runs the requested shell command inside a
    short-lived container. The Python runtime still performs path and command
    policy checks before this wrapper is used.
    """

    enabled: bool = False
    image: str = "python:3.12-slim"
    network: str = "none"
    memory: str = "512m"
    cpus: str = "1.0"
    read_only_rootfs: bool = True
    workdir: str = "/workspace"
    tmpfs: tuple[str, ...] = ("/tmp:rw,noexec,nosuid,size=128m",)
    extra_args: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_config(cls, data: dict[str, Any] | None) -> "DockerSandboxProfile":
        if not data:
            return cls()
        tmpfs_value = data.get("tmpfs", cls.tmpfs)
        if isinstance(tmpfs_value, str):
            tmpfs = (tmpfs_value,)
        elif isinstance(tmpfs_value, list):
            tmpfs = tuple(str(item) for item in tmpfs_value if str(item))
        else:
            tmpfs = cls.tmpfs
        extra_value = data.get("extra_args", ())
        extra_args = tuple(str(item) for item in extra_value) if isinstance(extra_value, list) else ()
        return cls(
            enabled=bool(data.get("enabled", False)),
            image=str(data.get("image") or cls.image),
            network=str(data.get("network") or cls.network),
            memory=str(data.get("memory") or cls.memory),
            cpus=str(data.get("cpus") or cls.cpus),
            read_only_rootfs=bool(data.get("read_only_rootfs", True)),
            workdir=str(data.get("workdir") or cls.workdir),
            tmpfs=tmpfs,
            extra_args=extra_args,
        )

    def wrap_command(self, command: str, *, workspace: Path, cwd: Path) -> list[str]:
        if not self.enabled:
            raise ValueError("docker sandbox profile is disabled")
        workspace = workspace.resolve()
        cwd = cwd.resolve()
        try:
            rel_cwd = cwd.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"container cwd outside workspace: {cwd}") from exc
        container_cwd = self.workdir if str(rel_cwd) == "." else f"{self.workdir}/{rel_cwd.as_posix()}"
        args = [
            "docker",
            "run",
            "--rm",
            "--network",
            self.network,
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "-v",
            f"{workspace}:{self.workdir}",
            "-w",
            container_cwd,
        ]
        if self.read_only_rootfs:
            args.append("--read-only")
        for mount in self.tmpfs:
            args.extend(["--tmpfs", mount])
        args.extend(self.extra_args)
        args.extend([self.image, "sh", "-lc", command])
        return args

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "image": self.image,
            "network": self.network,
            "memory": self.memory,
            "cpus": self.cpus,
            "read_only_rootfs": self.read_only_rootfs,
            "workdir": self.workdir,
            "tmpfs": list(self.tmpfs),
            "extra_args": list(self.extra_args),
        }
