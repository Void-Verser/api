"""Owners, owner sessions, jobs, worker heartbeats; api_keys.owner_id.

Revision ID: 0002_owners_jobs
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_owners_jobs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owners",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "owner_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("csrf_token", sa.String(128), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("owners.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_owner_sessions_token_hash", "owner_sessions", ["token_hash"])
    op.create_index("ix_owner_sessions_owner_id", "owner_sessions", ["owner_id"])

    with op.batch_alter_table("api_keys") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_api_keys_owner_id", "owners", ["owner_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_api_keys_owner_id", ["owner_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("api_key_id", sa.Integer(), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("media_id", sa.String(256), nullable=False),
        sa.Column("source_url", sa.String(1024), nullable=False),
        sa.Column("output_format", sa.String(8), nullable=False),
        sa.Column("quality", sa.String(16), nullable=True),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_by", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_key", sa.String(512), nullable=True),
        sa.Column("result_filename", sa.String(255), nullable=True),
        sa.Column("result_content_type", sa.String(64), nullable=True),
        sa.Column("result_size", sa.Integer(), nullable=True),
        sa.Column("result_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_api_key_id", "jobs", ["api_key_id"])

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(64), primary_key=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("capabilities", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    op.drop_index("ix_jobs_api_key_id", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_table("jobs")
    with op.batch_alter_table("api_keys") as batch:
        batch.drop_index("ix_api_keys_owner_id")
        batch.drop_constraint("fk_api_keys_owner_id", type_="foreignkey")
        batch.drop_column("owner_id")
    op.drop_index("ix_owner_sessions_owner_id", table_name="owner_sessions")
    op.drop_index("ix_owner_sessions_token_hash", table_name="owner_sessions")
    op.drop_table("owner_sessions")
    op.drop_table("owners")
