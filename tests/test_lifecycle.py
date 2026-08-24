from __future__ import annotations

from dataclasses import dataclass

import pytest

from echoweave_runtime.lifecycle import (
    LifecycleState,
    RuntimeHost,
    RuntimeLifecycleError,
    RuntimeShutdownError,
    RuntimeStartupError,
)


@dataclass
class RecordingComponent:
    name: str
    events: list[str]
    start_error: Exception | None = None
    stop_error: Exception | None = None

    def start(self) -> None:
        self.events.append(f"start:{self.name}")
        if self.start_error is not None:
            raise self.start_error

    def stop(self) -> None:
        self.events.append(f"stop:{self.name}")
        if self.stop_error is not None:
            raise self.stop_error


def test_runtime_host_starts_in_order_and_stops_in_reverse_order() -> None:
    events: list[str] = []
    host = RuntimeHost()
    host.register(RecordingComponent("provider", events))
    host.register(RecordingComponent("channel", events))

    with host:
        assert host.state is LifecycleState.RUNNING
        assert host.started_component_names == ("provider", "channel")

    assert host.state is LifecycleState.STOPPED
    assert events == ["start:provider", "start:channel", "stop:channel", "stop:provider"]


def test_runtime_host_rolls_back_started_components_when_start_fails() -> None:
    events: list[str] = []
    host = RuntimeHost()
    host.register(RecordingComponent("provider", events))
    host.register(RecordingComponent("channel", events, start_error=OSError("port in use")))

    with pytest.raises(RuntimeStartupError) as raised:
        host.start()

    assert raised.value.component == "channel"
    assert isinstance(raised.value.__cause__, OSError)
    assert host.state is LifecycleState.FAILED
    assert host.started_component_names == ()
    assert events == ["start:provider", "start:channel", "stop:provider"]


def test_runtime_host_preserves_rollback_failures() -> None:
    events: list[str] = []
    host = RuntimeHost()
    host.register(RecordingComponent("provider", events, stop_error=RuntimeError("disconnect failed")))
    host.register(RecordingComponent("channel", events, start_error=OSError("bind failed")))

    with pytest.raises(RuntimeStartupError) as raised:
        host.start()

    assert [failure.component for failure in raised.value.rollback_failures] == ["provider"]
    assert "rollback also failed" in str(raised.value)


def test_runtime_host_attempts_every_stop_and_reports_failures() -> None:
    events: list[str] = []
    host = RuntimeHost()
    host.register(RecordingComponent("provider", events, stop_error=RuntimeError("provider stop failed")))
    host.register(RecordingComponent("channel", events, stop_error=RuntimeError("channel stop failed")))
    host.start()

    with pytest.raises(RuntimeShutdownError) as raised:
        host.stop()

    assert host.state is LifecycleState.FAILED
    assert [failure.component for failure in raised.value.failures] == ["channel", "provider"]
    assert events[-2:] == ["stop:channel", "stop:provider"]


def test_runtime_host_rejects_duplicate_or_late_registration() -> None:
    events: list[str] = []
    host = RuntimeHost().register(RecordingComponent("provider", events))

    with pytest.raises(ValueError, match="already registered"):
        host.register(RecordingComponent("provider", events))

    host.start()
    with pytest.raises(RuntimeLifecycleError, match="before the runtime starts"):
        host.register(RecordingComponent("channel", events))
    host.stop()


def test_runtime_host_start_and_stop_are_idempotent() -> None:
    events: list[str] = []
    host = RuntimeHost().register(RecordingComponent("provider", events))

    host.start()
    host.start()
    host.stop()
    host.stop()

    assert events == ["start:provider", "stop:provider"]
