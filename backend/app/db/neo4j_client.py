from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from neo4j import Driver, GraphDatabase, Session

from app.config import get_settings

_driver: Driver | None = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        settings = get_settings()
        _driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
    return _driver


def close_driver() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


@contextmanager
def neo4j_session() -> Iterator[Session]:
    with get_driver().session() as s:
        yield s


def run_cypher(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with neo4j_session() as session:
        result = session.run(query, params or {})
        return [r.data() for r in result]
