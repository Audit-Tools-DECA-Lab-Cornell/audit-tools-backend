"""
Privacy helpers for Playspace API serialization.
"""

from __future__ import annotations


def mask_email(email: str | None) -> str | None:
	"""Mask an email so dashboards never expose full addresses."""

	if email is None:
		return None

	normalized_email = email.strip()
	if not normalized_email:
		return None

	if "@" not in normalized_email:
		return "***"

	local_part, domain_part = normalized_email.split("@", 1)
	if not local_part or not domain_part:
		return "***"

	visible_local = local_part[0:3]
	masked_local = f"{visible_local}{'*' * max(len(local_part) - 3, 4)}"

	domain_segments = [segment for segment in domain_part.split(".") if segment]
	if not domain_segments:
		return f"{masked_local}@***"

	domain_name = domain_segments[0]
	domain_suffix = ".".join(domain_segments[1:])
	masked_domain_name = f"{domain_name[0:2]}{'*' * max(len(domain_name) - 2, 4)}"

	if domain_suffix:
		return f"{masked_local}@{masked_domain_name}.{domain_suffix}"

	return f"{masked_local}@{masked_domain_name}"
