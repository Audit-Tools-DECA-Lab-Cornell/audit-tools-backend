"""Validation models for the YEE instrument contract used by the website survey."""

from __future__ import annotations

from pydantic import BaseModel, Field


class YeeInstrumentChoice(BaseModel):
	Display: str | None = None


class YeeInstrumentItem(BaseModel):
	item_id: str
	base_question_id: str
	block: str
	block_title: str | None = None
	question_text: str
	item_kind: str | None = None
	choices: dict[str, YeeInstrumentChoice] = Field(default_factory=dict)
	answers: dict[str, YeeInstrumentChoice] = Field(default_factory=dict)
	score_entries: list[dict[str, object]] = Field(default_factory=list)


class YeeInstrumentSectionMeta(BaseModel):
	block: str
	title: str
	intro_text: str = ""
	comment_prompt: str = ""


class YeeInstrumentResponse(BaseModel):
	survey_id: str | None = None
	survey_name: str
	version: str
	scoring_categories: dict[str, str] = Field(default_factory=dict)
	sections: list[YeeInstrumentSectionMeta] = Field(default_factory=list)
	scoring_items: list[YeeInstrumentItem]
