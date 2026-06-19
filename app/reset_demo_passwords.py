"""Restore seeded YEE demo account passwords without destructive reseeding."""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.auth_security import hash_password
from app.database import ASYNC_SESSION_FACTORY_BY_PRODUCT, ProductKey
from app.demo_accounts import DEFAULT_YEE_DEMO_EMAILS
from app.models import User

DEFAULT_PASSWORD = "DemoPass123!"


def _parse_args() -> argparse.Namespace:
	"""Parse CLI options for demo password restoration."""

	parser = argparse.ArgumentParser(description="Restore seeded YEE demo account passwords.")
	parser.add_argument(
		"--email",
		dest="emails",
		action="append",
		default=[],
		help="Specific email to restore. Repeat to restore multiple users. Defaults to all seeded YEE demo users.",
	)
	parser.add_argument(
		"--password",
		default=DEFAULT_PASSWORD,
		help="New password to apply to the selected demo accounts. Defaults to DemoPass123!.",
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		default=False,
		help="Show which users would be updated without writing changes.",
	)
	return parser.parse_args()


async def _reset_demo_passwords(*, emails: list[str], password: str, dry_run: bool) -> None:
	"""Reset one or more YEE demo-user passwords."""

	target_emails = (
		tuple(dict.fromkeys(email.strip().lower() for email in emails if email.strip())) or DEFAULT_YEE_DEMO_EMAILS
	)

	async with ASYNC_SESSION_FACTORY_BY_PRODUCT[ProductKey.YEE]() as session:
		result = await session.execute(select(User).where(User.email.in_(target_emails)).order_by(User.email.asc()))
		users = list(result.scalars())
		found_emails = {user.email.lower() for user in users}
		missing_emails = [email for email in target_emails if email.lower() not in found_emails]

		if not users:
			print("No matching YEE users were found.")
			if missing_emails:
				print(f"Missing emails: {', '.join(missing_emails)}")
			return

		for user in users:
			print(f"{'Would reset' if dry_run else 'Resetting'} password for {user.email} ({user.account_type.value})")
			if dry_run:
				continue
			user.password_hash = hash_password(password)
			user.failed_login_attempts = 0

		if dry_run:
			print(f"Dry run complete. {len(users)} user(s) matched.")
		else:
			await session.commit()
			print(f"Updated {len(users)} user(s).")

		if missing_emails:
			print(f"Missing emails: {', '.join(missing_emails)}")


def main() -> None:
	"""CLI entrypoint for demo password restoration."""

	args = _parse_args()
	asyncio.run(_reset_demo_passwords(emails=args.emails, password=args.password, dry_run=args.dry_run))


if __name__ == "__main__":
	main()
