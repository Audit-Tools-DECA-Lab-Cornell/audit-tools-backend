from __future__ import annotations

from dataclasses import dataclass
from typing import Final, TypeAlias

SCORING_VERSION: Final = "yee_v2"

DomainKey: TypeAlias = str

DOMAIN_ORDER: Final[tuple[DomainKey, ...]] = (
	"access",
	"activitySpaces",
	"amenities",
	"experienceOfSpace",
	"aestheticsAndCare",
	"useAndUsability",
)

SECTION_BY_DOMAIN: Final[dict[DomainKey, str]] = {
	"access": "Access: Presence, Condition, Provision",
	"activitySpaces": "Activity Spaces: Presence, Condition, Provision",
	"amenities": "Amenities: Presence, Condition Provision",
	"experienceOfSpace": "Experience of Space:",
	"aestheticsAndCare": "Aesthetics & Care: Presence, condition, provision",
	"useAndUsability": "Use & Usability: Presence, condition, provision",
}

CATEGORY_BY_DOMAIN: Final[dict[DomainKey, str]] = {
	"access": "Access",
	"activitySpaces": "Activity",
	"amenities": "Amenities",
	"experienceOfSpace": "Experience",
	"aestheticsAndCare": "Aesthetics & Care",
	"useAndUsability": "Use & Usability",
}


@dataclass(frozen=True, slots=True)
class AnswerScore:
	answer_id: str
	score: int


@dataclass(frozen=True, slots=True)
class PairedItemSpec:
	key: str
	domain: DomainKey
	presence_item_id: str
	condition_item_id: str
	choice_id: str
	max_score: int = 3


@dataclass(frozen=True, slots=True)
class PresenceItemSpec:
	key: str
	domain: DomainKey
	item_id: str
	choice_id: str
	answer_scores: tuple[AnswerScore, ...]

	@property
	def max_score(self) -> int:
		return max(answer.score for answer in self.answer_scores)


ScoreItemSpec: TypeAlias = PairedItemSpec | PresenceItemSpec

CONDITION_SCORES: Final = (
	AnswerScore("1", 1),
	AnswerScore("2", 2),
	AnswerScore("3", 3),
)
PRESENCE_YES_NO_SCORES: Final = (AnswerScore("1", 1), AnswerScore("2", 0))
PRESENCE_THREE_LEVEL_SCORES: Final = (
	AnswerScore("1", 2),
	AnswerScore("2", 1),
	AnswerScore("3", 0),
)
REVERSE_THREE_LEVEL_SCORES: Final = (
	AnswerScore("1", 0),
	AnswerScore("2", 1),
	AnswerScore("3", 2),
)
PRESENCE_TWO_LEVEL_SCORES: Final = (AnswerScore("1", 1), AnswerScore("2", 0))
REVERSE_TWO_LEVEL_SCORES: Final = (AnswerScore("1", 0), AnswerScore("2", 1))


def paired(key: str, domain: DomainKey, base_qid: str, choice_id: str) -> PairedItemSpec:
	return PairedItemSpec(
		key=key,
		domain=domain,
		presence_item_id=f"{base_qid}#1",
		condition_item_id=f"{base_qid}#2",
		choice_id=choice_id,
	)


def presence(
	key: str,
	domain: DomainKey,
	item_id: str,
	choice_id: str,
	answer_scores: tuple[AnswerScore, ...],
) -> PresenceItemSpec:
	return PresenceItemSpec(key=key, domain=domain, item_id=item_id, choice_id=choice_id, answer_scores=answer_scores)


ITEM_SPECS: Final[tuple[ScoreItemSpec, ...]] = (
	paired("access.q1", "access", "QID1", "1"),
	paired("access.q2", "access", "QID1", "2"),
	presence("access.q3", "access", "QID11#1", "1", PRESENCE_THREE_LEVEL_SCORES),
	presence("access.q4", "access", "QID11#1", "2", PRESENCE_THREE_LEVEL_SCORES),
	presence("access.q5", "access", "QID11#1", "3", PRESENCE_THREE_LEVEL_SCORES),
	presence("access.q6", "access", "QID11#1", "4", PRESENCE_THREE_LEVEL_SCORES),
	paired("activitySpaces.q1", "activitySpaces", "QID4", "1"),
	paired("activitySpaces.q2", "activitySpaces", "QID4", "2"),
	paired("activitySpaces.q3", "activitySpaces", "QID4", "3"),
	paired("activitySpaces.q4", "activitySpaces", "QID4", "4"),
	paired("activitySpaces.q5", "activitySpaces", "QID4", "5"),
	paired("activitySpaces.q6", "activitySpaces", "QID4", "6"),
	presence("activitySpaces.q7", "activitySpaces", "QID7#1", "1", PRESENCE_THREE_LEVEL_SCORES),
	presence("activitySpaces.q8", "activitySpaces", "QID7#1", "2", PRESENCE_THREE_LEVEL_SCORES),
	presence("activitySpaces.q9", "activitySpaces", "QID7#1", "3", PRESENCE_THREE_LEVEL_SCORES),
	presence("activitySpaces.q10", "activitySpaces", "QID7#1", "4", PRESENCE_THREE_LEVEL_SCORES),
	paired("amenities.q1", "amenities", "QID12", "1"),
	paired("amenities.q2", "amenities", "QID12", "2"),
	paired("amenities.q3", "amenities", "QID12", "3"),
	presence("amenities.q4", "amenities", "QID13#1", "1", PRESENCE_THREE_LEVEL_SCORES),
	presence("amenities.q5", "amenities", "QID13#1", "2", PRESENCE_THREE_LEVEL_SCORES),
	presence("amenities.q6", "amenities", "QID13#1", "3", PRESENCE_THREE_LEVEL_SCORES),
	presence("amenities.q7", "amenities", "QID13#1", "4", PRESENCE_THREE_LEVEL_SCORES),
	presence("amenities.q8", "amenities", "QID13#1", "5", PRESENCE_THREE_LEVEL_SCORES),
	presence("amenities.q9", "amenities", "QID13#1", "6", PRESENCE_THREE_LEVEL_SCORES),
	presence("amenities.q10", "amenities", "QID13#1", "7", PRESENCE_THREE_LEVEL_SCORES),
	presence("experienceOfSpace.q1", "experienceOfSpace", "QID15#1", "1", REVERSE_THREE_LEVEL_SCORES),
	presence("experienceOfSpace.q2", "experienceOfSpace", "QID15#1", "2", PRESENCE_THREE_LEVEL_SCORES),
	presence("experienceOfSpace.q3", "experienceOfSpace", "QID15#1", "3", PRESENCE_THREE_LEVEL_SCORES),
	presence("experienceOfSpace.q4", "experienceOfSpace", "QID15#1", "4", PRESENCE_THREE_LEVEL_SCORES),
	presence("experienceOfSpace.q5", "experienceOfSpace", "QID15#1", "5", PRESENCE_THREE_LEVEL_SCORES),
	presence("experienceOfSpace.q6", "experienceOfSpace", "QID15#1", "6", PRESENCE_THREE_LEVEL_SCORES),
	presence("experienceOfSpace.q7", "experienceOfSpace", "QID15#1", "7", PRESENCE_THREE_LEVEL_SCORES),
	presence("experienceOfSpace.q8", "experienceOfSpace", "QID15#1", "8", REVERSE_THREE_LEVEL_SCORES),
	presence("experienceOfSpace.q9", "experienceOfSpace", "QID15#1", "9", REVERSE_THREE_LEVEL_SCORES),
	presence("experienceOfSpace.q10", "experienceOfSpace", "QID15#1", "10", REVERSE_THREE_LEVEL_SCORES),
	PairedItemSpec("aestheticsAndCare.q1", "aestheticsAndCare", "QID16#2", "QID16#1", "1"),
	PairedItemSpec("aestheticsAndCare.q2", "aestheticsAndCare", "QID16#2", "QID16#1", "2"),
	PairedItemSpec("aestheticsAndCare.q3", "aestheticsAndCare", "QID16#2", "QID16#1", "3"),
	PairedItemSpec("aestheticsAndCare.q4", "aestheticsAndCare", "QID16#2", "QID16#1", "4"),
	presence("aestheticsAndCare.q5", "aestheticsAndCare", "QID17#1", "1", REVERSE_THREE_LEVEL_SCORES),
	presence("aestheticsAndCare.q6", "aestheticsAndCare", "QID17#1", "2", REVERSE_THREE_LEVEL_SCORES),
	presence("aestheticsAndCare.q7", "aestheticsAndCare", "QID17#1", "3", PRESENCE_THREE_LEVEL_SCORES),
	presence("aestheticsAndCare.q8", "aestheticsAndCare", "QID17#1", "4", REVERSE_THREE_LEVEL_SCORES),
	presence("aestheticsAndCare.q9", "aestheticsAndCare", "QID17#1", "5", REVERSE_THREE_LEVEL_SCORES),
	presence("aestheticsAndCare.q10", "aestheticsAndCare", "QID17#1", "6", PRESENCE_THREE_LEVEL_SCORES),
	paired("useAndUsability.q1", "useAndUsability", "QID19", "1"),
	paired("useAndUsability.q2", "useAndUsability", "QID19", "2"),
	presence("useAndUsability.q3", "useAndUsability", "QID20#1", "1", PRESENCE_THREE_LEVEL_SCORES),
	presence("useAndUsability.q4", "useAndUsability", "QID20#1", "2", PRESENCE_THREE_LEVEL_SCORES),
	presence("useAndUsability.q5", "useAndUsability", "QID20#1", "3", PRESENCE_THREE_LEVEL_SCORES),
	presence("useAndUsability.q6", "useAndUsability", "QID21#1", "1", REVERSE_TWO_LEVEL_SCORES),
	presence("useAndUsability.q7", "useAndUsability", "QID21#1", "2", PRESENCE_TWO_LEVEL_SCORES),
	presence("useAndUsability.q8", "useAndUsability", "QID21#1", "3", REVERSE_TWO_LEVEL_SCORES),
)
