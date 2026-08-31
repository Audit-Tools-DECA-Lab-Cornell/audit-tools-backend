"""Validation models for the YEE instrument contract used by the website survey."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.products.yee.schemas.instrument_authoring import AuthoringInstrumentV2


class YeeInstrumentNestedModel(BaseModel):
	model_config = ConfigDict(extra="allow")


class YeeInstrumentChoice(YeeInstrumentNestedModel):
	Display: str | None = None


class YeeInstrumentItem(YeeInstrumentNestedModel):
	item_id: str
	base_question_id: str
	block: str
	block_title: str | None = None
	question_text: str
	item_kind: str | None = None
	choices: dict[str, YeeInstrumentChoice] = Field(default_factory=dict)
	answers: dict[str, YeeInstrumentChoice] = Field(default_factory=dict)
	score_entries: list[dict[str, object]] = Field(default_factory=list)


class YeeInstrumentSectionMeta(YeeInstrumentNestedModel):
	block: str
	title: str
	intro_text: str = ""
	comment_prompt: str = ""


class YeeInstrumentOption(YeeInstrumentNestedModel):
	value: str
	label: str
	notes: str | None = None


class YeeInstrumentPreAuditQuestion(YeeInstrumentNestedModel):
	id: str
	title: str
	prompt: str
	description: str = ""
	options: list[YeeInstrumentOption] = Field(default_factory=list)
	multi_select: bool = False
	required: bool = True
	auto_generated: bool = False


class YeeInstrumentScaleRule(YeeInstrumentNestedModel):
	value: str
	label: str
	add: int | None = None
	boost: int | None = None
	follow_up_behavior: str | None = None
	tag: str | None = None


class YeeInstrumentScaleGuidance(YeeInstrumentNestedModel):
	id: str
	title: str
	prompt: str
	description: str = ""
	rules: list[YeeInstrumentScaleRule] = Field(default_factory=list)


class YeeInstrumentLegalDocument(YeeInstrumentNestedModel):
	id: str
	title: str
	last_updated: str | None = None
	content: str
	document_type: str | None = None


class YeeInstrumentWeightingDomain(YeeInstrumentNestedModel):
	# ``key`` matches the mobile/web domain keys exactly (access, activitySpaces,
	# amenities, experienceOfSpace, aestheticsAndCare, useAndUsability) so clients
	# can bind a per-domain importance prompt without any text parsing.
	key: str
	label: str
	prompt: str


class YeeInstrumentWeighting(YeeInstrumentNestedModel):
	title: str = ""
	description: str = ""
	options: list[YeeInstrumentOption] = Field(default_factory=list)
	domains: list[YeeInstrumentWeightingDomain] = Field(default_factory=list)


class YeeInstrumentResponse(BaseModel):
	model_config = ConfigDict(extra="allow")

	survey_id: str | None = None
	survey_name: str
	version: str
	scoring_categories: dict[str, str] = Field(default_factory=dict)
	sections: list[YeeInstrumentSectionMeta] = Field(default_factory=list)
	scoring_items: list[YeeInstrumentItem]
	preamble: list[str] = Field(default_factory=list)
	pre_audit_questions: list[YeeInstrumentPreAuditQuestion] = Field(default_factory=list)
	scale_guidance: list[YeeInstrumentScaleGuidance] = Field(default_factory=list)
	legal_documents: list[YeeInstrumentLegalDocument] = Field(default_factory=list)
	# Per-domain youth-weighting prompts + scale, shown on the weighting step.
	weighting: YeeInstrumentWeighting | None = None
	# Shared "If yes, please rate the condition…" follow-up prompt (domain steps).
	condition_prompt: str = ""
	# Prompt for the overall/final comments field before review & submit.
	final_comments_prompt: str = ""
	authoring: AuthoringInstrumentV2 | None = None
