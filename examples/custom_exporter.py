"""Custom exporter example for the Acme SDK.

This example shows how to create a custom exporter that writes spans
to a SQLite database for local querying and analysis.
"""

import json
import sqlite3
from pathlib import Path
from typing import Sequence

from acme_sdk.models import Span, SpanKind, SpanStatus


class SQLiteExporter:
    """Export spans to a local SQLite database.

    This custom exporter stores spans in a SQLite database, allowing
    you to query trace data locally using SQL.
    """

    def __init__(self, db_path: str = "traces.db") -> None:
        self._db_path = Path(db_path)
        self._conn = sqlite3.connect(str(self._db_path))
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS spans (
                span_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                parent_span_id TEXT,
                name TEXT NOT NULL,
                service_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                duration_ms REAL,
                attributes TEXT,
                events TEXT
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trace_id ON spans(trace_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_service_name ON spans(service_name)
        """)
        self._conn.commit()

    def export(self, spans: Sequence[Span]) -> int:
        """Export spans to the SQLite database."""
        for span in spans:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO spans
                (span_id, trace_id, parent_span_id, name, service_name,
                 kind, status, start_time, end_time, duration_ms,
                 attributes, events)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    span.span_id,
                    span.trace_id,
                    span.parent_span_id,
                    span.name,
                    span.service_name,
                    span.kind.value,
                    span.status.value,
                    span.start_time.isoformat(),
                    span.end_time.isoformat() if span.end_time else None,
                    span.duration_ms,
                    json.dumps(span.attributes),
                    json.dumps([e.model_dump(mode="json") for e in span.events]),
                ),
            )
        self._conn.commit()
        return len(spans)

    def query(self, sql: str) -> list[tuple]:
        """Run a SQL query against the spans database."""
        cursor = self._conn.execute(sql)
        return cursor.fetchall()

    def close(self) -> None:
        self._conn.close()


def main():
    # Create some sample spans
    spans = [
        Span(
            name="GET /api/users",
            service_name="user-service",
            kind=SpanKind.SERVER,
            duration_ms=45.2,
            attributes={"http.method": "GET", "http.status_code": 200},
        ),
        Span(
            name="SELECT users",
            service_name="user-service",
            kind=SpanKind.CLIENT,
            duration_ms=12.8,
            attributes={"db.system": "postgresql"},
        ),
        Span(
            name="GET /api/products",
            service_name="product-service",
            kind=SpanKind.SERVER,
            duration_ms=89.5,
            attributes={"http.method": "GET", "http.status_code": 200},
        ),
    ]

    for span in spans:
        span.finish()

    # Export to SQLite
    exporter = SQLiteExporter("example_traces.db")
    exported = exporter.export(spans)
    print(f"Exported {exported} spans to SQLite")

    # Query the data
    print("\nAll spans by service:")
    for row in exporter.query(
        "SELECT service_name, name, duration_ms FROM spans ORDER BY service_name"
    ):
        print(f"  {row[0]}: {row[1]} ({row[2]:.1f}ms)")

    print("\nAverage duration by service:")
    for row in exporter.query(
        "SELECT service_name, AVG(duration_ms) FROM spans GROUP BY service_name"
    ):
        print(f"  {row[0]}: {row[1]:.1f}ms")

    exporter.close()


if __name__ == "__main__":
    main()
