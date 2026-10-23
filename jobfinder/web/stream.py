"""SSE scan-progress and log stream (T20).

``GET /api/stream/scan`` — a single, tagged-JSON-event-per-message stream
replacing the desktop app's ``queue.Queue`` + 300ms ``QTimer`` polling
(``orchestrator.py``, ``widget.py``). That protocol pushed a bare token
(``"SCAN_COMPLETE"``) then, as a *separate* queue item, its payload — not
atomic, and two producers desync it. ``Orchestrator.run_scan`` now pushes
one ``{"type": ..., ...}`` dict per message instead
(``scan_start`` / ``scan_complete`` / ``matches`` / ``show_history``); this
module is now the only consumer of that tagged protocol: the other one,
``jobfinder/ui/widget.py``'s ``_drain_queue``, was deleted in T33.

Threading model — this is the part that is easy to get subtly wrong:

  * The scan itself runs on an APScheduler worker thread
    (``jobfinder/scheduler.py``), not the Uvicorn event loop.
  * ``asyncio.Queue`` is **not** thread-safe, and a plain ``queue.Queue``
    can't be ``await``ed. So the scan thread never touches an
    ``asyncio.Queue`` directly — it calls ``EventBroadcaster.put()``
    (the ``queue.Queue``-shaped adapter below), which schedules the real
    broadcast via ``loop.call_soon_threadsafe`` onto the loop captured at
    lifespan startup (``jobfinder/web/app.py``'s ``app.state.loop``, T14).
  * Because ``EventBroadcaster.publish`` (the thing ``call_soon_threadsafe``
    actually runs) only ever executes on the loop thread, and
    subscribe/unsubscribe also only ever run on the loop thread (inside the
    SSE route's async generator), ``_subscribers``/``_ring`` need no lock.
  * ``call_soon_threadsafe`` never raises back into the scan thread even if
    the loop has already shut down (e.g. the app is tearing down while a
    scan is still running) — it's swallowed, not propagated, which is the
    whole point: a dropped/absent client, or a dead loop, must never crash
    ``Orchestrator.run_scan``.

Both buffers T20's DoD requires bounded are the *same* bounded ring buffer
(``EventBroadcaster._ring``, a ``collections.deque(maxlen=...)``):

  * It is the replay buffer a client that connects mid-scan, or reconnects
    after a drop, catches up from (``GET /api/state``, T19, remains the
    full-snapshot complement to this — "running: bool" plus the counts,
    not the event-by-event detail this stream carries).
  * It is also where log records land once ``BroadcastLogHandler`` is
    attached (only for the duration of a scan cycle, see
    ``jobfinder/web/scan.py``'s ``make_scan_fn``), so there is no separate
    unbounded ``queue.Queue`` for logs in the web path at all — unlike
    ``jobfinder.ui.log_console.QueueLogHandler``, which fed the desktop
    app's always-visible, always-draining console window and went with it
    in T33.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

logger = logging.getLogger("jobfinder.web.stream")

router = APIRouter(prefix="/api")

# Bounds both the replay-on-reconnect window and the "log queue" the T20
# DoD requires stays capped with no client attached — one deque, one cap,
# for both jobs.
RING_BUFFER_SIZE = 200

# Each subscriber also gets its own bounded asyncio.Queue so one slow
# client can't grow memory unboundedly either; a full queue just drops the
# newest event for that subscriber (logged, not raised).
SUBSCRIBER_QUEUE_SIZE = 500


class EventBroadcaster:
    """Fan-out hub for tagged scan/log events.

    ``_subscribers`` and ``_ring`` are mutated only on the asyncio event
    loop thread: ``publish`` only ever runs there (scheduled via
    ``call_soon_threadsafe`` from the scan thread by ``publish_threadsafe``
    below), and ``subscribe``/``unsubscribe`` run inside the SSE route's
    async generator, which Starlette always drives on the loop. No lock is
    needed as a result — see module docstring.
    """

    def __init__(self, replay_size: int = RING_BUFFER_SIZE) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._ring: deque[dict[str, Any]] = deque(maxlen=replay_size)
        self._next_seq = 0

    def subscribe(self) -> tuple["asyncio.Queue[dict[str, Any]]", list[dict[str, Any]]]:
        """Register a new subscriber queue and return it with a replay
        snapshot of the current ring buffer, so a client connecting
        mid-scan (or reconnecting after a drop) catches up immediately.
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.add(q)
        return q, list(self._ring)

    def unsubscribe(self, q: "asyncio.Queue[dict[str, Any]]") -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        """Runs on the loop thread only — append to the bounded ring
        buffer, then fan out to every currently-subscribed queue.
        """
        self._next_seq += 1
        stamped = {**event, "seq": self._next_seq}
        self._ring.append(stamped)
        for q in list(self._subscribers):
            try:
                q.put_nowait(stamped)
            except asyncio.QueueFull:
                logger.warning("Dropping SSE event for a slow subscriber (queue full).")

    def publish_threadsafe(self, loop: asyncio.AbstractEventLoop, event: dict[str, Any]) -> None:
        """Called from the APScheduler worker thread. Never raises back
        into the scan — a dropped client or a loop that has already shut
        down must not crash ``Orchestrator.run_scan``.
        """
        try:
            loop.call_soon_threadsafe(self.publish, event)
        except RuntimeError:
            logger.debug("Event loop unavailable; dropping event %r", event.get("type"))


class ThreadsafeEventSink:
    """``queue.Queue``-shaped adapter: ``Orchestrator`` only ever calls
    ``.put(event)`` on whatever it's constructed with (see
    ``jobfinder/orchestrator.py``), so this is a drop-in replacement for
    the desktop app's plain ``queue.Queue()`` that instead bridges each
    tagged event onto the asyncio loop via the broadcaster.
    """

    def __init__(self, broadcaster: EventBroadcaster, loop: asyncio.AbstractEventLoop) -> None:
        self._broadcaster = broadcaster
        self._loop = loop

    def put(self, event: dict[str, Any]) -> None:
        self._broadcaster.publish_threadsafe(self._loop, event)

    # Orchestrator only calls .put(), but queue.Queue also exposes
    # put_nowait — kept as an alias so anything written against the real
    # queue.Queue interface keeps working against this adapter too.
    def put_nowait(self, event: dict[str, Any]) -> None:
        self.put(event)


class BroadcastLogHandler(logging.Handler):
    """Forwards log records as ``{"type": "log", ...}`` tagged events
    while installed. Installed only for the duration of a single scan
    cycle (``jobfinder/web/scan.py``'s ``make_scan_fn``) — it is not a
    permanent root-logger handler, so there is nothing accumulating with
    no scan running and no client attached beyond the ring buffer's own
    cap.
    """

    def __init__(self, broadcaster: EventBroadcaster, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._broadcaster = broadcaster
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        try:
            event = {
                "type": "log",
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "time": record.created,
            }
        except Exception:
            self.handleError(record)
            return
        self._broadcaster.publish_threadsafe(self._loop, event)


async def _scan_event_stream(broadcaster: EventBroadcaster) -> AsyncIterator[ServerSentEvent]:
    q, replay = broadcaster.subscribe()
    try:
        for event in replay:
            yield ServerSentEvent(data=event, id=str(event["seq"]))
        while True:
            event = await q.get()
            yield ServerSentEvent(data=event, id=str(event["seq"]))
    finally:
        # Runs on disconnect/cancellation too (client closed the tab,
        # dropped connection, or reconnected elsewhere) — the scan itself
        # never knows or cares; it just kept calling publish_threadsafe.
        broadcaster.unsubscribe(q)


@router.get("/stream/scan", response_class=EventSourceResponse)
async def stream_scan(request: Request) -> AsyncIterator[ServerSentEvent]:
    broadcaster: EventBroadcaster = request.app.state.broadcaster
    async for event in _scan_event_stream(broadcaster):
        yield event
