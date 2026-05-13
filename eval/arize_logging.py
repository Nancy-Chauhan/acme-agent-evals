"""Small Arize AX helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import pandas as pd


def create_arize_client():
    """Create an Arize client lazily so dry runs do not require the package."""
    from arize import ArizeClient

    api_key = os.environ.get("ARIZE_API_KEY")
    if not api_key:
        raise EnvironmentError("ARIZE_API_KEY must be set in .env")
    return ArizeClient(api_key=api_key)


def create_dataset(client: Any, tasks: list[dict[str, Any]], name: str) -> str:
    """Upload a task dataset to Arize and return its id."""
    space_id = os.environ.get("ARIZE_SPACE_ID")
    if not space_id:
        raise EnvironmentError("ARIZE_SPACE_ID must be set in .env")

    df = pd.DataFrame(
        {
            "attributes.input.value": [task["description"] for task in tasks],
            "attributes.output.value": [
                json.dumps(task.get("expected_output", {})) for task in tasks
            ],
            "task_id": [task["id"] for task in tasks],
            "tier": [task["tier"] for task in tasks],
            "category": [task["category"] for task in tasks],
        }
    )
    dataset = client.datasets.create(space=space_id, name=name, examples=df)
    return dataset.id


def dataset_name(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
