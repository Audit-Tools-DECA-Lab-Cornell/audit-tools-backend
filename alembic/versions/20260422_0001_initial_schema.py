"""Initial schema baseline.

Revision ID: 20260422_0001
Revises: 
Create Date: 2026-04-22

"""

from __future__ import annotations

from alembic import op
from sqlalchemy.dialects.postgresql import ENUM

from app.models import AccountType, AuditStatus, Base, NotificationType


# revision identifiers, used by Alembic.
revision = "20260422_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
	bind = op.get_bind()

	ENUM(AccountType, name="shared_account_type").create(bind, checkfirst=True)
	ENUM(AuditStatus, name="shared_audit_status").create(bind, checkfirst=True)
	ENUM(NotificationType, name="notification_type_enum").create(bind, checkfirst=True)

	Base.metadata.create_all(bind=bind)


def downgrade() -> None:
	bind = op.get_bind()
	Base.metadata.drop_all(bind=bind)

	ENUM(NotificationType, name="notification_type_enum").drop(bind, checkfirst=True)
	ENUM(AuditStatus, name="shared_audit_status").drop(bind, checkfirst=True)
	ENUM(AccountType, name="shared_account_type").drop(bind, checkfirst=True)
