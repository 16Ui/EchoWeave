from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Self


class LifecycleState(str, Enum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class LifecycleComponent(Protocol):
    """A resource managed by :class:`RuntimeHost`."""

    @property
    def name(self) -> str: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class LifecycleFailure:
    component: str
    error: BaseException


class RuntimeLifecycleError(RuntimeError):
    pass


class RuntimeStartupError(RuntimeLifecycleError):
    def __init__(
        self,
        component: str,
        cause: BaseException,
        rollback_failures: tuple[LifecycleFailure, ...] = (),
    ) -> None:
        self.component = component
        self.cause = cause
        self.rollback_failures = rollback_failures
        suffix = ""
        if rollback_failures:
            names = ", ".join(failure.component for failure in rollback_failures)
            suffix = f"; rollback also failed for: {names}"
        super().__init__(f"failed to start runtime component {component!r}: {cause}{suffix}")


class RuntimeShutdownError(RuntimeLifecycleError):
    def __init__(self, failures: tuple[LifecycleFailure, ...]) -> None:
        self.failures = failures
        names = ", ".join(failure.component for failure in failures)
        super().__init__(f"failed to stop runtime component(s): {names}")


class RuntimeHost:
    """Owns runtime resources and enforces deterministic startup and shutdown.

    Components start in registration order and stop in reverse order. If startup
    fails, resources that already started are rolled back before the error escapes.
    """

    def __init__(self) -> None:
        self._components: list[LifecycleComponent] = []
        self._started: list[LifecycleComponent] = []
        self._state = LifecycleState.CREATED

    @property
    def state(self) -> LifecycleState:
        return self._state

    @property
    def component_names(self) -> tuple[str, ...]:
        return tuple(component.name for component in self._components)

    @property
    def started_component_names(self) -> tuple[str, ...]:
        return tuple(component.name for component in self._started)

    def register(self, component: LifecycleComponent) -> Self:
        if self._state is not LifecycleState.CREATED:
            raise RuntimeLifecycleError("components can only be registered before the runtime starts")
        name = component.name.strip()
        if not name:
            raise ValueError("runtime component name must not be empty")
        if name in self.component_names:
            raise ValueError(f"runtime component {name!r} is already registered")
        self._components.append(component)
        return self

    def start(self) -> None:
        if self._state is LifecycleState.RUNNING:
            return
        if self._state is not LifecycleState.CREATED:
            raise RuntimeLifecycleError(f"cannot start runtime from state {self._state.value!r}")

        self._state = LifecycleState.STARTING
        for component in self._components:
            try:
                component.start()
            except BaseException as exc:
                rollback_failures = self._stop_started()
                self._state = LifecycleState.FAILED
                raise RuntimeStartupError(component.name, exc, rollback_failures) from exc
            self._started.append(component)
        self._state = LifecycleState.RUNNING

    def stop(self) -> None:
        if self._state in {LifecycleState.STOPPED, LifecycleState.FAILED}:
            return
        if self._state is LifecycleState.CREATED:
            self._state = LifecycleState.STOPPED
            return
        if self._state is not LifecycleState.RUNNING:
            raise RuntimeLifecycleError(f"cannot stop runtime from state {self._state.value!r}")

        self._state = LifecycleState.STOPPING
        failures = self._stop_started()
        self._state = LifecycleState.FAILED if failures else LifecycleState.STOPPED
        if failures:
            raise RuntimeShutdownError(failures)

    def _stop_started(self) -> tuple[LifecycleFailure, ...]:
        failures: list[LifecycleFailure] = []
        while self._started:
            component = self._started.pop()
            try:
                component.stop()
            except BaseException as exc:
                failures.append(LifecycleFailure(component.name, exc))
        return tuple(failures)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.stop()
        return False


__all__ = [
    "LifecycleComponent",
    "LifecycleFailure",
    "LifecycleState",
    "RuntimeHost",
    "RuntimeLifecycleError",
    "RuntimeShutdownError",
    "RuntimeStartupError",
]
