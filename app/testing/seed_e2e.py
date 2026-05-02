"""CLI entry point for seeding deterministic Playspace E2E data."""

from __future__ import annotations

import asyncio

from app.testing.database import seed_current_playspace_test_database


def main() -> None:
	"""Seed deterministic Playspace E2E data into the test database."""

	asyncio.run(seed_current_playspace_test_database())
	print("Playspace E2E seed data loaded.")


if __name__ == "__main__":
	main()
