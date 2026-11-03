"""add export tracking (suivi des téléchargements par organisme producteur)

Revision ID: a7c3e2f1b9d4
Revises: f6a1feb3f297
Create Date: 2026-11-03 15:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a7c3e2f1b9d4"
down_revision = "f6a1feb3f297"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "t_export_tracking",
        sa.Column("id_tracking", sa.Integer, primary_key=True),
        sa.Column("tracking_key", sa.String(255), nullable=False, unique=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("id_export", sa.Integer),
        sa.Column("export_label", sa.String(255)),
        sa.Column("export_format", sa.String(20)),
        sa.Column("requester_type", sa.String(20), nullable=False),
        sa.Column(
            "requester_id_organisme",
            sa.Integer,
            sa.ForeignKey("utilisateurs.bib_organismes.id_organisme", ondelete="SET NULL"),
        ),
        sa.Column("nb_requests", sa.Integer, nullable=False, server_default="0"),
        schema="gn_commons",
    )
    op.create_index("i_t_export_tracking_day", "t_export_tracking", ["day"], schema="gn_commons")
    op.create_table(
        "cor_export_tracking_dataset",
        sa.Column(
            "id_tracking",
            sa.Integer,
            sa.ForeignKey("gn_commons.t_export_tracking.id_tracking", ondelete="CASCADE"),
            primary_key=True,
        ),
        # 0 = jeu de données inconnu ; pas de clé étrangère pour garder l'historique
        sa.Column("id_dataset", sa.Integer, primary_key=True),
        sa.Column("nb_observations", sa.BigInteger, nullable=False, server_default="0"),
        schema="gn_commons",
    )
    op.create_index(
        "i_cor_export_tracking_dataset_id_dataset",
        "cor_export_tracking_dataset",
        ["id_dataset"],
        schema="gn_commons",
    )
    op.execute(
        """
        COMMENT ON TABLE gn_commons.t_export_tracking IS
        'Suivi des téléchargements de données naturalistes, agrégés par jour, export, format et
        organisme demandeur. Aucune donnée personnelle (ni id_role, ni adresse IP).'
        """
    )


def downgrade():
    op.drop_table("cor_export_tracking_dataset", schema="gn_commons")
    op.drop_table("t_export_tracking", schema="gn_commons")
