"""PostgreSQL transaction owned by one chat turn, outside LangGraph state."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.tools.db import get_conn


_registry: dict[str, "TurnTransaction"] = {}
_registry_lock = threading.RLock()


@dataclass
class TurnTransaction:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    connection: Any = None
    aborted: bool = False
    reason: str = ""
    source_text: str | None = None
    writes: list[dict] = field(default_factory=list)
    finished: bool = False
    lock: Any = field(default_factory=threading.RLock)

    def conn(self):
        if self.finished or self.aborted:
            raise RuntimeError("A transação deste turno não aceita novas operações.")
        if self.connection is None:
            self.connection = get_conn()
        return self.connection

    def abort(self, reason: str) -> None:
        self.aborted = True
        if not self.reason:
            self.reason = reason

    def record(self, name: str, result: dict) -> None:
        self.writes.append({"name": name, "result": result})

    def finish(self, approved: bool) -> str:
        """Return committed, rolled_back, uncertain, or no_write."""
        with self.lock:
            if self.finished:
                raise RuntimeError("A transação já foi finalizada.")
            self.finished = True
            conn = self.connection
            if conn is None:
                return "no_write"
            try:
                if not approved or self.aborted:
                    try:
                        conn.rollback()
                        return "rolled_back"
                    except Exception:
                        return "rollback_uncertain"
                try:
                    conn.commit()
                    return "committed"
                except Exception:
                    # A exceção do cliente não prova que o servidor não confirmou.
                    return "uncertain"
            finally:
                try:
                    conn.close()
                except Exception:
                    pass


def register_turn() -> TurnTransaction:
    turn = TurnTransaction()
    with _registry_lock:
        _registry[turn.id] = turn
    return turn


def unregister_turn(turn: TurnTransaction) -> None:
    with _registry_lock:
        _registry.pop(turn.id, None)
    if not turn.finished:
        turn.finish(False)


def transaction_for(config: RunnableConfig | None) -> TurnTransaction | None:
    configurable = (config or {}).get("configurable", {})
    turn_id = configurable.get("chat_transaction_id")
    if not turn_id:
        return None
    with _registry_lock:
        turn = _registry.get(turn_id)
    if turn is None:
        raise RuntimeError("A transação do chat não está disponível.")
    return turn


def connection_for_read(config: RunnableConfig | None):
    """Reuse an active turn connection; otherwise own a short read connection."""
    turn = transaction_for(config)
    if turn is not None and turn.connection is not None:
        return turn.connection, turn
    return get_conn(), None
