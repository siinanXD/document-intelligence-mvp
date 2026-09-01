"""Tighten evidence locators and keep relation endpoints inside one package.

Canonical facts must point at a complete locator, and a relation may only
connect entities that share its package. Cross-package edges inside one tenant
were previously accepted because entity FKs were tenant-scoped only.

Revision ID: 0010_evidence_pkg
Revises: 0009_engineering
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_evidence_pkg"
down_revision: str | None = "0009_engineering"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCATOR_SQL = """
(
    (locator_kind = 'chunk' AND document_id IS NOT NULL AND source_id IS NOT NULL)
    OR (locator_kind = 'page' AND document_id IS NOT NULL AND page_number IS NOT NULL)
    OR (
        locator_kind = 'sheet_cell'
        AND document_id IS NOT NULL
        AND sheet_name IS NOT NULL
        AND cell_range IS NOT NULL
    )
    OR (
        locator_kind = 'image_region'
        AND document_id IS NOT NULL
        AND region <> '{}'::jsonb
    )
    OR (locator_kind = 'xml_path' AND document_id IS NOT NULL AND xml_path IS NOT NULL)
    OR (locator_kind = 'line_range' AND document_id IS NOT NULL AND line_start IS NOT NULL)
    OR (
        locator_kind = 'native_id'
        AND document_id IS NOT NULL
        AND native_object_id IS NOT NULL
    )
)
"""


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_engineering_entities_tenant_package_id",
        "engineering_entities",
        ["tenant_id", "package_id", "id"],
    )
    op.drop_constraint(
        "fk_engineering_relations_tenant_source", "engineering_relations", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_engineering_relations_tenant_target", "engineering_relations", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_engineering_relations_tenant_source",
        "engineering_relations",
        "engineering_entities",
        ["tenant_id", "package_id", "source_entity_id"],
        ["tenant_id", "package_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_engineering_relations_tenant_target",
        "engineering_relations",
        "engineering_entities",
        ["tenant_id", "package_id", "target_entity_id"],
        ["tenant_id", "package_id", "id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "fk_relation_candidates_tenant_source",
        "engineering_relation_candidates",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_relation_candidates_tenant_target",
        "engineering_relation_candidates",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_relation_candidates_tenant_source",
        "engineering_relation_candidates",
        "engineering_entities",
        ["tenant_id", "package_id", "source_entity_id"],
        ["tenant_id", "package_id", "id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_relation_candidates_tenant_target",
        "engineering_relation_candidates",
        "engineering_entities",
        ["tenant_id", "package_id", "target_entity_id"],
        ["tenant_id", "package_id", "id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_evidence_references_locator",
        "evidence_references",
        _LOCATOR_SQL,
    )


def downgrade() -> None:
    op.drop_constraint("ck_evidence_references_locator", "evidence_references", type_="check")
    op.drop_constraint(
        "fk_relation_candidates_tenant_source",
        "engineering_relation_candidates",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_relation_candidates_tenant_target",
        "engineering_relation_candidates",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_relation_candidates_tenant_source",
        "engineering_relation_candidates",
        "engineering_entities",
        ["tenant_id", "source_entity_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_relation_candidates_tenant_target",
        "engineering_relation_candidates",
        "engineering_entities",
        ["tenant_id", "target_entity_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "fk_engineering_relations_tenant_source", "engineering_relations", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_engineering_relations_tenant_target", "engineering_relations", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_engineering_relations_tenant_source",
        "engineering_relations",
        "engineering_entities",
        ["tenant_id", "source_entity_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_engineering_relations_tenant_target",
        "engineering_relations",
        "engineering_entities",
        ["tenant_id", "target_entity_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_engineering_entities_tenant_package_id",
        "engineering_entities",
        type_="unique",
    )
