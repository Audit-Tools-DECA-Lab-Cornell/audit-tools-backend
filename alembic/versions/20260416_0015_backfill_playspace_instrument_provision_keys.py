"""Backfill stored Playspace instrument content to provision keys.

Revision ID: 20260416_0015
Revises: 20260416_0014
Create Date: 2026-04-16 02:00:00
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "20260416_0015"
down_revision: str | None = "20260416_0014"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _is_target_product(product_key: str) -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("product", "yee").strip().lower() == product_key


def _rename_json_value(value: object) -> object:
    if isinstance(value, dict):
        next_value: dict[str, object] = {}
        for key, item in value.items():
            next_key = "provision" if key == "quantity" else key
            next_value[next_key] = _rename_json_value(item)
        return next_value

    if isinstance(value, list):
        return [_rename_json_value(item) for item in value]

    if value == "quantity":
        return "provision"

    return value


def _restore_json_value(value: object) -> object:
    if isinstance(value, dict):
        next_value: dict[str, object] = {}
        for key, item in value.items():
            next_key = "quantity" if key == "provision" else key
            next_value[next_key] = _restore_json_value(item)
        return next_value

    if isinstance(value, list):
        return [_restore_json_value(item) for item in value]

    if value == "provision":
        return "quantity"

    return value


def _write_instrument_content(
    bind: sa.Connection, *, instrument_id: object, content: dict[str, object]
) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE instruments
            SET content = CAST(:content AS jsonb),
                updated_at = NOW()
            WHERE id = :instrument_id
            """
        ),
        {"content": json.dumps(content), "instrument_id": instrument_id},
    )


def _backfill_instrument_content() -> None:
    bind = op.get_bind()
    rows = (
        bind.execute(
            sa.text(
                """
            SELECT id, content
            FROM instruments
            WHERE instrument_key = :instrument_key
            """
            ),
            {"instrument_key": "pvua_v5_2"},
        )
        .mappings()
        .all()
    )

    for row in rows:
        content = row["content"]
        if not isinstance(content, dict):
            continue

        next_content = _rename_json_value(content)
        if next_content != content:
            _write_instrument_content(bind, instrument_id=row["id"], content=next_content)


def upgrade() -> None:
    if context.is_offline_mode():
        return

    if not _is_target_product("playspace"):
        return

    _backfill_instrument_content()


def downgrade() -> None:
    if context.is_offline_mode():
        return

    if not _is_target_product("playspace"):
        return

    bind = op.get_bind()
    rows = (
        bind.execute(
            sa.text(
                """
            SELECT id, content
            FROM instruments
            WHERE instrument_key = :instrument_key
            """
            ),
            {"instrument_key": "pvua_v5_2"},
        )
        .mappings()
        .all()
    )

    for row in rows:
        content = row["content"]
        if not isinstance(content, dict):
            continue

        next_content = _restore_json_value(content)
        if next_content != content:
            _write_instrument_content(bind, instrument_id=row["id"], content=next_content)
