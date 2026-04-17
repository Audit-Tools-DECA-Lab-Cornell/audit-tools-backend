"""Backfill Playspace audit answers to provision keys.

Revision ID: 20260416_0016
Revises: 20260416_0015
Create Date: 2026-04-16 02:10:00
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "20260416_0016"
down_revision: str | None = "20260416_0015"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

PLAYSPACE_INSTRUMENT_KEY = "pvua_v5_2"


def _is_target_product(product_key: str) -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("product", "yee").strip().lower() == product_key


def _rename_payload(
    value: object, key_map: dict[str, str], *, rename_string_values: bool
) -> object:
    if isinstance(value, dict):
        next_value: dict[str, object] = {}
        for key, item in value.items():
            next_value[key_map.get(key, key)] = _rename_payload(
                item,
                key_map,
                rename_string_values=rename_string_values,
            )
        return next_value

    if isinstance(value, list):
        return [
            _rename_payload(item, key_map, rename_string_values=rename_string_values)
            for item in value
        ]

    if rename_string_values and isinstance(value, str):
        return key_map.get(value, value)

    return value


def _write_jsonb(
    bind: sa.Connection, *, table: str, column: str, row_id: object, payload: object
) -> None:
    bind.execute(
        sa.text(
            f"""
            UPDATE {table}
            SET {column} = CAST(:payload AS jsonb),
                updated_at = NOW()
            WHERE id = :row_id
            """
        ),
        {"payload": json.dumps(payload), "row_id": row_id},
    )


def _backfill_audit_documents() -> None:
    bind = op.get_bind()
    rows = (
        bind.execute(
            sa.text(
                """
            SELECT id, responses_json, scores_json
            FROM audits
            WHERE instrument_key = :instrument_key
            """
            ),
            {"instrument_key": PLAYSPACE_INSTRUMENT_KEY},
        )
        .mappings()
        .all()
    )

    response_key_map = {"quantity": "provision"}
    score_key_map = {
        "quantity_total": "provision_total",
        "quantity_total_max": "provision_total_max",
    }

    for row in rows:
        responses_json = row["responses_json"]
        scores_json = row["scores_json"]
        if not isinstance(responses_json, dict) and not isinstance(scores_json, dict):
            continue

        next_responses = (
            _rename_payload(responses_json, response_key_map, rename_string_values=True)
            if isinstance(responses_json, dict)
            else responses_json
        )
        next_scores = (
            _rename_payload(scores_json, score_key_map, rename_string_values=False)
            if isinstance(scores_json, dict)
            else scores_json
        )

        if next_responses != responses_json:
            _write_jsonb(
                bind,
                table="audits",
                column="responses_json",
                row_id=row["id"],
                payload=next_responses,
            )
        if next_scores != scores_json:
            _write_jsonb(
                bind,
                table="audits",
                column="scores_json",
                row_id=row["id"],
                payload=next_scores,
            )


def _backfill_scale_answer_rows() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE playspace_scale_answers
            SET scale_key = 'provision',
                updated_at = NOW()
            WHERE scale_key = 'quantity'
            """
        )
    )


def upgrade() -> None:
    if context.is_offline_mode():
        return

    if not _is_target_product("playspace"):
        return

    _backfill_audit_documents()
    _backfill_scale_answer_rows()


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
            SELECT id, responses_json, scores_json
            FROM audits
            WHERE instrument_key = :instrument_key
            """
            ),
            {"instrument_key": PLAYSPACE_INSTRUMENT_KEY},
        )
        .mappings()
        .all()
    )

    response_key_map = {"provision": "quantity"}
    score_key_map = {
        "provision_total": "quantity_total",
        "provision_total_max": "quantity_total_max",
    }

    for row in rows:
        responses_json = row["responses_json"]
        scores_json = row["scores_json"]
        if not isinstance(responses_json, dict) and not isinstance(scores_json, dict):
            continue

        next_responses = (
            _rename_payload(responses_json, response_key_map, rename_string_values=True)
            if isinstance(responses_json, dict)
            else responses_json
        )
        next_scores = (
            _rename_payload(scores_json, score_key_map, rename_string_values=False)
            if isinstance(scores_json, dict)
            else scores_json
        )

        if next_responses != responses_json:
            _write_jsonb(
                bind,
                table="audits",
                column="responses_json",
                row_id=row["id"],
                payload=next_responses,
            )
        if next_scores != scores_json:
            _write_jsonb(
                bind,
                table="audits",
                column="scores_json",
                row_id=row["id"],
                payload=next_scores,
            )

    bind.execute(
        sa.text(
            """
            UPDATE playspace_scale_answers
            SET scale_key = 'quantity',
                updated_at = NOW()
            WHERE scale_key = 'provision'
            """
        )
    )
