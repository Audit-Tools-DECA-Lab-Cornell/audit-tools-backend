"""Normalize Playspace audit storage into relational child tables.

Revision ID: 20260320_0004
Revises: 20260319_0003
Create Date: 2026-03-20 20:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "20260320_0004"
down_revision: str | None = "20260319_0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

PLAYSPACE_INSTRUMENT_KEY = "pvua_v5_2"
NOW_SQL = sa.text("now()")
MULTI_SELECT_PRE_AUDIT_FIELDS = {
    "weather_conditions",
    "users_present",
    "age_groups",
}
PRE_AUDIT_FIELD_ORDER = (
    "season",
    "weather_conditions",
    "users_present",
    "user_count",
    "age_groups",
    "place_size",
)


def _is_target_product(product_key: str) -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("product", "yee").strip().lower() == product_key


def upgrade() -> None:
    """Create normalized Playspace audit tables and backfill existing rows."""

    if not _is_target_product("playspace"):
        return
    op.create_table(
        "playspace_audit_contexts",
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_mode", sa.String(length=20), nullable=True),
        sa.Column("draft_progress_percent", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=NOW_SQL,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=NOW_SQL,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["audit_id"],
            ["audits.id"],
            name="fk_ps_context_audit",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("audit_id", name="pk_playspace_audit_contexts"),
    )

    op.create_table(
        "playspace_pre_audit_answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_key", sa.String(length=80), nullable=False),
        sa.Column("selected_value", sa.String(length=80), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=NOW_SQL,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["audit_id"],
            ["audits.id"],
            name="fk_ps_pre_audit_answer_audit",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_playspace_pre_audit_answers"),
        sa.UniqueConstraint(
            "audit_id",
            "field_key",
            "selected_value",
            name="uq_playspace_pre_audit_answers_audit_field_value",
        ),
    )
    op.create_index(
        "ix_playspace_pre_audit_answers_audit_id",
        "playspace_pre_audit_answers",
        ["audit_id"],
        unique=False,
    )

    op.create_table(
        "playspace_audit_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_key", sa.String(length=120), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=NOW_SQL,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=NOW_SQL,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["audit_id"],
            ["audits.id"],
            name="fk_ps_audit_section_audit",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_playspace_audit_sections"),
        sa.UniqueConstraint(
            "audit_id",
            "section_key",
            name="uq_playspace_audit_sections_audit_section",
        ),
    )
    op.create_index(
        "ix_playspace_audit_sections_audit_id",
        "playspace_audit_sections",
        ["audit_id"],
        unique=False,
    )

    op.create_table(
        "playspace_question_responses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_key", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=NOW_SQL,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=NOW_SQL,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["playspace_audit_sections.id"],
            name="fk_ps_question_response_section",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_playspace_question_responses"),
        sa.UniqueConstraint(
            "section_id",
            "question_key",
            name="uq_playspace_question_responses_section_question",
        ),
    )
    op.create_index(
        "ix_playspace_question_responses_section_id",
        "playspace_question_responses",
        ["section_id"],
        unique=False,
    )

    op.create_table(
        "playspace_scale_answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_response_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scale_key", sa.String(length=40), nullable=False),
        sa.Column("option_key", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=NOW_SQL,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=NOW_SQL,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["question_response_id"],
            ["playspace_question_responses.id"],
            name="fk_ps_scale_answer_question_response",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_playspace_scale_answers"),
        sa.UniqueConstraint(
            "question_response_id",
            "scale_key",
            name="uq_playspace_scale_answers_question_scale",
        ),
    )
    op.create_index(
        "ix_playspace_scale_answers_question_response_id",
        "playspace_scale_answers",
        ["question_response_id"],
        unique=False,
    )

    _backfill_existing_playspace_audits()


def downgrade() -> None:
    """Drop normalized Playspace audit tables."""

    if not _is_target_product("playspace"):
        return
    op.drop_index(
        "ix_playspace_scale_answers_question_response_id",
        table_name="playspace_scale_answers",
    )
    op.drop_table("playspace_scale_answers")
    op.drop_index(
        "ix_playspace_question_responses_section_id",
        table_name="playspace_question_responses",
    )
    op.drop_table("playspace_question_responses")
    op.drop_index("ix_playspace_audit_sections_audit_id", table_name="playspace_audit_sections")
    op.drop_table("playspace_audit_sections")
    op.drop_index(
        "ix_playspace_pre_audit_answers_audit_id",
        table_name="playspace_pre_audit_answers",
    )
    op.drop_table("playspace_pre_audit_answers")
    op.drop_table("playspace_audit_contexts")


def _backfill_existing_playspace_audits() -> None:
    """Copy cached Playspace JSON data into the new normalized tables."""

    bind = op.get_bind()
    audits_table = sa.table(
        "audits",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("instrument_key", sa.String()),
        sa.column("responses_json", postgresql.JSONB(astext_type=sa.Text())),
        sa.column("scores_json", postgresql.JSONB(astext_type=sa.Text())),
    )
    context_table = sa.table(
        "playspace_audit_contexts",
        sa.column("audit_id", postgresql.UUID(as_uuid=True)),
        sa.column("execution_mode", sa.String()),
        sa.column("draft_progress_percent", sa.Float()),
    )
    pre_audit_table = sa.table(
        "playspace_pre_audit_answers",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("audit_id", postgresql.UUID(as_uuid=True)),
        sa.column("field_key", sa.String()),
        sa.column("selected_value", sa.String()),
        sa.column("sort_order", sa.Integer()),
    )
    section_table = sa.table(
        "playspace_audit_sections",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("audit_id", postgresql.UUID(as_uuid=True)),
        sa.column("section_key", sa.String()),
        sa.column("note", sa.Text()),
    )
    question_table = sa.table(
        "playspace_question_responses",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("section_id", postgresql.UUID(as_uuid=True)),
        sa.column("question_key", sa.String()),
    )
    scale_table = sa.table(
        "playspace_scale_answers",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("question_response_id", postgresql.UUID(as_uuid=True)),
        sa.column("scale_key", sa.String()),
        sa.column("option_key", sa.String()),
    )

    context_rows: list[dict[str, object]] = []
    pre_audit_rows: list[dict[str, object]] = []
    section_rows: list[dict[str, object]] = []
    question_rows: list[dict[str, object]] = []
    scale_rows: list[dict[str, object]] = []

    audit_rows = bind.execute(
        sa.select(
            audits_table.c.id,
            audits_table.c.instrument_key,
            audits_table.c.responses_json,
            audits_table.c.scores_json,
        )
    ).mappings()

    for audit_row in audit_rows:
        instrument_key = audit_row.get("instrument_key")
        if instrument_key != PLAYSPACE_INSTRUMENT_KEY:
            continue

        audit_id = audit_row["id"]
        responses_json = _read_json_dict(audit_row.get("responses_json"))
        scores_json = _read_json_dict(audit_row.get("scores_json"))
        meta_payload = _read_json_dict(responses_json.get("meta"))

        execution_mode = meta_payload.get("execution_mode")
        raw_draft_progress_percent = scores_json.get("draft_progress_percent")
        if isinstance(execution_mode, str) or isinstance(raw_draft_progress_percent, int | float):
            context_rows.append(
                {
                    "audit_id": audit_id,
                    "execution_mode": execution_mode if isinstance(execution_mode, str) else None,
                    "draft_progress_percent": (
                        float(raw_draft_progress_percent)
                        if isinstance(raw_draft_progress_percent, int | float)
                        else None
                    ),
                }
            )

        pre_audit_payload = _read_json_dict(responses_json.get("pre_audit"))
        for field_key in PRE_AUDIT_FIELD_ORDER:
            raw_value = pre_audit_payload.get(field_key)
            if field_key in MULTI_SELECT_PRE_AUDIT_FIELDS and isinstance(raw_value, list):
                for sort_order, selected_value in enumerate(_string_values(raw_value)):
                    pre_audit_rows.append(
                        {
                            "id": uuid.uuid4(),
                            "audit_id": audit_id,
                            "field_key": field_key,
                            "selected_value": selected_value,
                            "sort_order": sort_order,
                        }
                    )
                continue

            if isinstance(raw_value, str) and raw_value.strip():
                pre_audit_rows.append(
                    {
                        "id": uuid.uuid4(),
                        "audit_id": audit_id,
                        "field_key": field_key,
                        "selected_value": raw_value,
                        "sort_order": 0,
                    }
                )

        sections_payload = _read_json_dict(responses_json.get("sections"))
        for section_key, raw_section_payload in sections_payload.items():
            section_payload = _read_json_dict(raw_section_payload)
            section_id = uuid.uuid4()
            raw_note = section_payload.get("note")
            section_rows.append(
                {
                    "id": section_id,
                    "audit_id": audit_id,
                    "section_key": section_key,
                    "note": raw_note if isinstance(raw_note, str) else None,
                }
            )

            responses_payload = _read_json_dict(section_payload.get("responses"))
            for question_key, raw_question_payload in responses_payload.items():
                question_response_id = uuid.uuid4()
                question_rows.append(
                    {
                        "id": question_response_id,
                        "section_id": section_id,
                        "question_key": question_key,
                    }
                )

                for scale_key, option_key in _read_string_dict(raw_question_payload).items():
                    scale_rows.append(
                        {
                            "id": uuid.uuid4(),
                            "question_response_id": question_response_id,
                            "scale_key": scale_key,
                            "option_key": option_key,
                        }
                    )

    if context_rows:
        op.bulk_insert(context_table, context_rows)
    if pre_audit_rows:
        op.bulk_insert(pre_audit_table, pre_audit_rows)
    if section_rows:
        op.bulk_insert(section_table, section_rows)
    if question_rows:
        op.bulk_insert(question_table, question_rows)
    if scale_rows:
        op.bulk_insert(scale_table, scale_rows)


def _read_json_dict(value: object) -> dict[str, object]:
    """Safely coerce JSON-like values into dictionaries."""

    return dict(value) if isinstance(value, dict) else {}


def _read_string_dict(value: object) -> dict[str, str]:
    """Safely coerce JSON-like values into string-only dictionaries."""

    if not isinstance(value, dict):
        return {}

    next_payload: dict[str, str] = {}
    for entry_key, entry_value in value.items():
        if isinstance(entry_value, str) and entry_key.strip():
            next_payload[entry_key] = entry_value
    return next_payload


def _string_values(values: list[object]) -> list[str]:
    """Filter a JSON-like list down to its string values."""

    return [value for value in values if isinstance(value, str) and value.strip()]
