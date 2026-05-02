"""CLI entry point for resetting the Playspace E2E database."""

from __future__ import annotations

from app.testing.database import reset_and_migrate_playspace_test_database


def main() -> None:
	"""Reset the guarded Playspace test database and run migrations."""

	reset_and_migrate_playspace_test_database()
	print("Playspace E2E test database reset and migrated.")


if __name__ == "__main__":
	main()
