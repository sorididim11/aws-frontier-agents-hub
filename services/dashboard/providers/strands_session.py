"""SessionAgentStore — maps session_id to Strands Agent instances."""
import threading
from typing import Callable

from strands import Agent


class SessionAgentStore:
    """Thread-safe session_id → Agent mapping.

    Each session_id maps to a single Agent instance whose conversation
    history persists across calls (Strands Agent.messages accumulates).
    """

    def __init__(self, max_sessions: int = 100):
        self._agents: dict[str, Agent] = {}
        self._lock = threading.Lock()
        self._max = max_sessions

    def get_or_create(self, session_id: str, factory: Callable[[], Agent]) -> Agent:
        """Get existing agent for session or create new one via factory."""
        with self._lock:
            if session_id not in self._agents:
                if len(self._agents) >= self._max:
                    oldest = next(iter(self._agents))
                    del self._agents[oldest]
                self._agents[session_id] = factory()
            return self._agents[session_id]

    def remove(self, session_id: str):
        """Remove a session (e.g., on explicit close)."""
        with self._lock:
            self._agents.pop(session_id, None)

    def has(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._agents

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._agents)
