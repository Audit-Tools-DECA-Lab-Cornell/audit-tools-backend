"""Rename the PVUA 'diversity' scale to 'variety' in persisted Playspace data.

The second PVUA scale is renamed from Diversity to Variety. This migration moves
every previously submitted (and in-progress) audit onto the new identity so
scoring and report rendering keep working:

* ``playspace_scale_answers`` - the normalized ``scale_key`` value and the
  scale's ``option_key`` values (``no_/some_/a_lot_of_diversity``).
* ``playspace_submissions`` - the immutable ``responses_json`` answer snapshot
  (the scale object key and its option slugs) and the ``scores_json`` totals
  cache (``diversity_total`` / ``diversity_total_max``).
* ``instruments`` - the stored instrument definitions in ``content`` (scale key,
  title, option keys, and prose).

The JSON token rewrites are deliberately narrow: the scale key is matched with
its trailing colon so only object keys change, and option/score identifiers are
unambiguous slugs - auditor free-text in notes is never touched.

Revision ID: ps_0004
Revises: ps_0003
Create Date: 2026-06-11

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ps_0004"
down_revision = "ps_0003"
branch_labels = None
depends_on = None

# Scale + option key renames, paired as (legacy, canonical).
_SCALE_KEY_RENAME = ("diversity", "variety")
_OPTION_KEY_RENAMES = (
	("no_diversity", "no_variety"),
	("some_diversity", "some_variety"),
	("a_lot_of_diversity", "a_lot_of_variety"),
)


def _has_table(table_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	return table_name in inspector.get_table_names()


def _rename_scale_answer_rows(scale_from: str, scale_to: str, option_renames: tuple[tuple[str, str], ...]) -> None:
	"""Rewrite normalized scale-answer rows from one scale identity to the other."""

	if not _has_table("playspace_scale_answers"):
		return
	op.execute(
		sa.text("UPDATE playspace_scale_answers SET scale_key = :to WHERE scale_key = :from_").bindparams(
			to=scale_to, from_=scale_from
		)
	)
	for option_from, option_to in option_renames:
		op.execute(
			sa.text("UPDATE playspace_scale_answers SET option_key = :to WHERE option_key = :from_").bindparams(
				to=option_to, from_=option_from
			)
		)


def _rewrite_submission_json(
	scale_token_from: str,
	scale_token_to: str,
	option_renames: tuple[tuple[str, str], ...],
	score_from: str,
	score_to: str,
) -> None:
	"""Rewrite the JSONB answer snapshot and score cache on submissions."""

	if not _has_table("playspace_submissions"):
		return
	# responses_json: scale object key (matched with its colon) + option slugs.
	op.execute(
		sa.text(
			f"""
			UPDATE playspace_submissions
			SET responses_json = replace(replace(replace(replace(
					responses_json::text,
					'"{scale_token_from}":', '"{scale_token_to}":'),
					'"{option_renames[0][0]}"', '"{option_renames[0][1]}"'),
					'"{option_renames[1][0]}"', '"{option_renames[1][1]}"'),
					'"{option_renames[2][0]}"', '"{option_renames[2][1]}"')::jsonb
			WHERE responses_json::text LIKE '%{scale_token_from}%'
			"""
		)
	)
	# scores_json: column totals cache (covers both _total and _total_max).
	op.execute(
		sa.text(
			f"""
			UPDATE playspace_submissions
			SET scores_json = replace(scores_json::text, '{score_from}', '{score_to}')::jsonb
			WHERE scores_json::text LIKE '%{score_from}%'
			"""
		)
	)


def _rewrite_instrument_content(text_from_cap: str, text_to_cap: str, text_from_low: str, text_to_low: str) -> None:
	"""Rewrite stored instrument definitions (no auditor free-text lives here)."""

	if not _has_table("instruments"):
		return
	op.execute(
		sa.text(
			f"""
			UPDATE instruments
			SET content = replace(replace(content::text, '{text_from_cap}', '{text_to_cap}'), '{text_from_low}', '{text_to_low}')::jsonb
			WHERE content::text LIKE '%{text_from_low}%'
			"""
		)
	)


def upgrade() -> None:
	_rename_scale_answer_rows(_SCALE_KEY_RENAME[0], _SCALE_KEY_RENAME[1], _OPTION_KEY_RENAMES)
	_rewrite_submission_json(
		_SCALE_KEY_RENAME[0],
		_SCALE_KEY_RENAME[1],
		_OPTION_KEY_RENAMES,
		"diversity_total",
		"variety_total",
	)
	_rewrite_instrument_content("Diversity", "Variety", "diversity", "variety")


def downgrade() -> None:
	reversed_options = tuple((new, old) for old, new in _OPTION_KEY_RENAMES)
	_rename_scale_answer_rows(_SCALE_KEY_RENAME[1], _SCALE_KEY_RENAME[0], reversed_options)
	_rewrite_submission_json(
		_SCALE_KEY_RENAME[1],
		_SCALE_KEY_RENAME[0],
		reversed_options,
		"variety_total",
		"diversity_total",
	)
	_rewrite_instrument_content("Variety", "Diversity", "variety", "diversity")
