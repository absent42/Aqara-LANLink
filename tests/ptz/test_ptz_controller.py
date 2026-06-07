import asyncio
import pytest
from homeassistant.exceptions import HomeAssistantError
from custom_components.aqara_lanlink.ptz.controller import PtzController
from custom_components.aqara_lanlink.ptz import commands as c
from custom_components.aqara_lanlink.ptz.session import PptzTimeout

class FakeSession:
    def __init__(self): self.sent = []; self.ready = True; self.closed = False
    async def connect(self, timeout=5.0): self.ready = True
    async def send_ioctl(self, iotype, body): self.sent.append((iotype, body)); return 1
    async def recv_ioctl(self, timeout): return (c.AUTH_REPLY_IOTYPE, 1, {"code": 0, "message": "success"})
    async def close(self): self.closed = True

class FakeCreds:
    cipher_key = "aqarager19kn"
    did_blob = b"\x00" * 20
    app_pub_hex = "ab" * 32
    sign = "cd" * 32
    time = "1700000000"

_CONTROLLERS = []

@pytest.fixture(autouse=True)
async def _shutdown_controllers():
    """Cancel any lingering idle timer / session after each test.

    The HA test plugin fails on lingering asyncio timers; the warm-session
    idle timer outlives a test unless we tear the controller down.
    """
    _CONTROLLERS.clear()
    yield
    for ctrl in _CONTROLLERS:
        await ctrl.async_shutdown()
    _CONTROLLERS.clear()

def _make(**kw):
    sessions = []
    def factory(key, did_blob, ip):
        s = FakeSession(); s.ip = ip; sessions.append(s); return s
    async def boot(*a, **k): return FakeCreds()
    async def ip_provider(): return "10.1.20.152"
    ctrl = PtzController(
        cloud_client=object(), token="T", user_id="U", did="lumi3.x",
        camera_ip_provider=ip_provider, features=frozenset({"pan_tilt","zoom","presets"}),
        bootstrap_fn=boot, session_factory=factory, idle_timeout=kw.get("idle_timeout", 180.0),
    )
    _CONTROLLERS.append(ctrl)
    return ctrl, sessions

@pytest.mark.asyncio
async def test_first_command_lazy_connects_and_auths():
    ctrl, sessions = _make()
    await ctrl.send_command(c.PTZ_IOTYPE, {"action": "left"})
    assert len(sessions) == 1 and sessions[0].ready
    # AUTH(4096) then the command(4138) were sent on the one session
    iotypes = [io for io, _ in sessions[0].sent]
    assert iotypes == [c.AUTH_IOTYPE, c.PTZ_IOTYPE]

@pytest.mark.asyncio
async def test_second_command_reuses_warm_session():
    ctrl, sessions = _make()
    await ctrl.send_command(c.PTZ_IOTYPE, {"action": "left"})
    await ctrl.send_command(c.PTZ_IOTYPE, {"action": "right"})
    assert len(sessions) == 1  # no reconnect
    iotypes = [io for io, _ in sessions[0].sent]
    assert iotypes == [c.AUTH_IOTYPE, c.PTZ_IOTYPE, c.PTZ_IOTYPE]  # one AUTH, two moves

@pytest.mark.asyncio
async def test_ptz_move_nudge():
    ctrl, sessions = _make()
    await ctrl.ptz_move("left")
    assert (c.PTZ_IOTYPE, {"action": "left"}) in sessions[0].sent

@pytest.mark.asyncio
async def test_ptz_move_continuous_with_duration_sends_stop(monkeypatch):
    ctrl, sessions = _make()
    async def _no_sleep(_): pass
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    await ctrl.ptz_move("left", continuous=True, duration=2.0)
    bodies = [b for _, b in sessions[0].sent]
    assert {"action": "left_always"} in bodies and {"action": "stop"} in bodies

@pytest.mark.asyncio
async def test_zoom_tracks_and_clamps():
    ctrl, sessions = _make()
    await ctrl.ptz_zoom(2.0)
    assert ctrl.current_zoom == 2.0
    assert (c.ZOOM_IOTYPE, {"zoom": "2.0"}) in sessions[0].sent
    await ctrl.zoom_step(+1.0)
    assert ctrl.current_zoom == 3.0
    await ctrl.ptz_zoom(99.0)  # clamps to 9.0
    assert ctrl.current_zoom == 9.0

@pytest.mark.asyncio
async def test_goto_preset_sends_4140():
    ctrl, sessions = _make()
    await ctrl.goto_preset("POS-STR")
    assert (c.POSITION_IOTYPE, {"position": "POS-STR"}) in sessions[0].sent

@pytest.mark.asyncio
async def test_goto_preset_recycles_session_so_next_command_reconnects():
    """A local 4140 preset recall poisons the camera's P2P session: it keeps
    ACKing but stops executing commands on it. So after a preset the controller
    must recycle the session -- the next command reconnects fresh, instead of
    silently failing (acked-but-ignored) until the 180s idle teardown.
    """
    ctrl, sessions = _make()
    await ctrl.ptz_move("left")              # builds session[0]
    assert len(sessions) == 1
    await ctrl.goto_preset('{"x":1,"y":2}')  # sent on session[0], which is then recycled
    # the preset command itself WAS sent before recycling
    assert (c.POSITION_IOTYPE, {"position": '{"x":1,"y":2}'}) in sessions[0].sent
    assert sessions[0].closed is True        # session recycled after the preset
    await ctrl.ptz_move("right")             # must build a brand-new session
    assert len(sessions) == 2
    assert sessions[1].closed is False
    assert (c.PTZ_IOTYPE, {"action": "right"}) in sessions[1].sent

@pytest.mark.asyncio
async def test_goto_preset_eager_reconnects_in_background():
    """After a preset recycles the poisoned session, the controller pre-warms a
    fresh one in the BACKGROUND (during the user's viewing pause) rather than
    lazily on the next command -- so the next move is instant."""
    ctrl, sessions = _make()
    await ctrl.ptz_move("left")              # session[0]
    await ctrl.goto_preset('{"x":1,"y":2}')  # recycles [0], schedules eager reconnect
    assert sessions[0].closed is True
    assert ctrl._reconnect_task is not None
    await ctrl._reconnect_task               # let the background pre-warm complete
    # a fresh session was established WITHOUT any user command...
    assert len(sessions) == 2 and sessions[1].ready is True
    assert [io for io, _ in sessions[1].sent] == [c.AUTH_IOTYPE]  # only AUTH so far
    # ...and the next move reuses it (no further reconnect)
    await ctrl.ptz_move("right")
    assert len(sessions) == 2
    assert (c.PTZ_IOTYPE, {"action": "right"}) in sessions[1].sent

@pytest.mark.asyncio
async def test_schedule_eager_reconnect_skips_when_one_is_pending():
    """Two presets in quick succession must not orphan a pre-warm task: while a
    pre-warm is in flight, a second schedule is skipped (single-slot guard)."""
    ctrl, sessions = _make()
    await ctrl._lock.acquire()          # block the pre-warm so task1 stays pending
    try:
        ctrl._schedule_eager_reconnect()
        task1 = ctrl._reconnect_task
        assert task1 is not None and not task1.done()
        ctrl._schedule_eager_reconnect()           # must SKIP (task1 still pending)
        assert ctrl._reconnect_task is task1        # slot not overwritten
    finally:
        ctrl._lock.release()
    await task1                          # let the single pre-warm complete cleanly

@pytest.mark.asyncio
async def test_prewarmed_session_idle_timer_armed_and_teardownable():
    """A pre-warmed (eager) session arms the idle timer and is torn down by it,
    so a recall-then-never-touch-again does not leak an open session."""
    ctrl, sessions = _make()
    await ctrl.ptz_move("left")
    await ctrl.goto_preset('{"x":1}')
    await ctrl._reconnect_task
    assert len(sessions) == 2 and sessions[1].closed is False
    assert ctrl._session is sessions[1]
    assert ctrl._idle_handle is not None        # idle timer armed for the pre-warm
    await ctrl._idle_teardown()                  # simulate the idle timer firing
    assert sessions[1].closed is True            # pre-warmed-but-unused session closed
    assert ctrl._session is None

@pytest.mark.asyncio
async def test_zoom_rejected_when_unsupported():
    ctrl, _ = _make()
    ctrl.features = frozenset({"pan_tilt", "presets"})  # no zoom
    with pytest.raises(HomeAssistantError):
        await ctrl.ptz_zoom(2.0)


# --- Task 4.3: lifecycle edges -------------------------------------------

@pytest.mark.asyncio
async def test_idle_timeout_tears_down_session():
    ctrl, sessions = _make(idle_timeout=0.01)
    await ctrl.send_command(c.PTZ_IOTYPE, {"action": "left"})
    assert len(sessions) == 1 and not sessions[0].closed
    await asyncio.sleep(0.05)  # let the idle timer fire + teardown run
    assert sessions[0].closed  # warm session closed on idle
    # a subsequent command must build a fresh (second) session
    await ctrl.send_command(c.PTZ_IOTYPE, {"action": "right"})
    assert len(sessions) == 2 and not sessions[1].closed

@pytest.mark.asyncio
async def test_auth_failure_triggers_one_rebootstrap_retry():
    sessions = []

    class FlakyAuthSession:
        """First instance fails AUTH (code 1); second succeeds (code 0)."""
        def __init__(self, code):
            self.code = code; self.sent = []; self.ready = True; self.closed = False
        async def connect(self, timeout=5.0): self.ready = True
        async def send_ioctl(self, iotype, body): self.sent.append((iotype, body)); return 1
        async def recv_ioctl(self, timeout):
            return (c.AUTH_REPLY_IOTYPE, 1, {"code": self.code, "message": "x"})
        async def close(self): self.closed = True

    def factory(key, did_blob, ip):
        s = FlakyAuthSession(code=1 if not sessions else 0)
        sessions.append(s); return s

    boot_calls = 0
    async def boot(*a, **k):
        nonlocal boot_calls
        boot_calls += 1
        return FakeCreds()
    async def ip_provider(): return "10.1.20.152"

    ctrl = PtzController(
        cloud_client=object(), token="T", user_id="U", did="lumi3.x",
        camera_ip_provider=ip_provider, features=frozenset({"pan_tilt", "zoom", "presets"}),
        bootstrap_fn=boot, session_factory=factory, idle_timeout=180.0,
    )
    _CONTROLLERS.append(ctrl)

    await ctrl.send_command(c.PTZ_IOTYPE, {"action": "left"})
    assert len(sessions) == 2  # first auth failed -> rebootstrap + retry
    assert sessions[0].closed  # failed session was torn down
    assert boot_calls == 2  # creds re-fetched on retry
    # the move only landed on the second (authed) session
    assert (c.PTZ_IOTYPE, {"action": "left"}) in sessions[1].sent
    assert (c.PTZ_IOTYPE, {"action": "left"}) not in sessions[0].sent

@pytest.mark.asyncio
async def test_terminal_auth_failure_closes_both_sessions():
    """Every session fails AUTH -> HomeAssistantError, both sessions torn down."""
    sessions = []

    class AlwaysFailSession:
        def __init__(self):
            self.sent = []; self.ready = True; self.closed = False
        async def connect(self, timeout=5.0): self.ready = True
        async def send_ioctl(self, iotype, body): self.sent.append((iotype, body)); return 1
        async def recv_ioctl(self, timeout):
            return (c.AUTH_REPLY_IOTYPE, 1, {"code": 1, "message": "fail"})
        async def close(self): self.closed = True

    def factory(key, did_blob, ip):
        s = AlwaysFailSession(); sessions.append(s); return s
    async def boot(*a, **k): return FakeCreds()
    async def ip_provider(): return "10.1.20.152"

    ctrl = PtzController(
        cloud_client=object(), token="T", user_id="U", did="lumi3.x",
        camera_ip_provider=ip_provider, features=frozenset({"pan_tilt", "zoom", "presets"}),
        bootstrap_fn=boot, session_factory=factory, idle_timeout=180.0,
    )
    _CONTROLLERS.append(ctrl)

    with pytest.raises(HomeAssistantError):
        await ctrl.ptz_move("left")
    assert len(sessions) == 2  # initial attempt + one retry
    assert all(s.closed for s in sessions)  # both failed sessions torn down


@pytest.mark.asyncio
async def test_connect_timeout_translates_to_home_assistant_error():
    """A PptzTimeout on connect (offline camera) -> HomeAssistantError; lock released."""
    sessions = []

    class TimeoutSession:
        def __init__(self):
            self.sent = []; self.ready = False; self.closed = False
        async def connect(self, timeout=5.0):
            raise PptzTimeout("no camera reply")
        async def send_ioctl(self, iotype, body): self.sent.append((iotype, body)); return 1
        async def recv_ioctl(self, timeout): return None
        async def close(self): self.closed = True

    def factory(key, did_blob, ip):
        s = TimeoutSession(); sessions.append(s); return s
    async def boot(*a, **k): return FakeCreds()
    async def ip_provider(): return "10.1.20.152"

    ctrl = PtzController(
        cloud_client=object(), token="T", user_id="U", did="lumi3.x",
        camera_ip_provider=ip_provider, features=frozenset({"pan_tilt", "zoom", "presets"}),
        bootstrap_fn=boot, session_factory=factory, idle_timeout=180.0,
    )
    _CONTROLLERS.append(ctrl)

    with pytest.raises(HomeAssistantError):
        await ctrl.ptz_move("left")
    # lock was released -> a second command can be attempted (also fails cleanly)
    with pytest.raises(HomeAssistantError):
        await ctrl.send_command(c.PTZ_IOTYPE, {"action": "right"})


@pytest.mark.asyncio
async def test_no_camera_ip_translates_and_builds_no_session():
    """A None IP -> HomeAssistantError before any session is constructed."""
    sessions = []
    def factory(key, did_blob, ip):
        s = FakeSession(); s.ip = ip; sessions.append(s); return s
    async def boot(*a, **k): return FakeCreds()
    async def ip_provider(): return None

    ctrl = PtzController(
        cloud_client=object(), token="T", user_id="U", did="lumi3.x",
        camera_ip_provider=ip_provider, features=frozenset({"pan_tilt", "zoom", "presets"}),
        bootstrap_fn=boot, session_factory=factory, idle_timeout=180.0,
    )
    _CONTROLLERS.append(ctrl)

    with pytest.raises(HomeAssistantError):
        await ctrl.ptz_move("left")
    assert sessions == []  # no session was constructed


@pytest.mark.asyncio
async def test_command_error_releases_lock():
    """A failure inside a command must release the lock for the next command."""
    sessions = []
    calls = 0
    def factory(key, did_blob, ip):
        s = FakeSession(); s.ip = ip; sessions.append(s); return s
    async def boot(*a, **k): return FakeCreds()
    async def ip_provider():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("ip lookup boom")
        return "10.1.20.152"

    ctrl = PtzController(
        cloud_client=object(), token="T", user_id="U", did="lumi3.x",
        camera_ip_provider=ip_provider, features=frozenset({"pan_tilt", "zoom", "presets"}),
        bootstrap_fn=boot, session_factory=factory, idle_timeout=180.0,
    )
    _CONTROLLERS.append(ctrl)

    with pytest.raises(RuntimeError):
        await ctrl.send_command(c.PTZ_IOTYPE, {"action": "left"})
    # lock must have been released; a second command now succeeds
    await ctrl.send_command(c.PTZ_IOTYPE, {"action": "right"})
    assert len(sessions) == 1 and sessions[0].ready
    assert (c.PTZ_IOTYPE, {"action": "right"}) in sessions[0].sent


@pytest.mark.asyncio
async def test_async_shutdown_closes_live_session():
    ctrl, sessions = _make()
    await ctrl.send_command(c.PTZ_IOTYPE, {"action": "left"})
    assert not sessions[0].closed
    await ctrl.async_shutdown()
    assert sessions[0].closed
    assert ctrl._session is None
    assert ctrl._idle_handle is None or ctrl._idle_handle.cancelled()
