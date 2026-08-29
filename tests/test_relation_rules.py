"""The relation rules, in isolation.

`classify` is pure: a candidate and the thresholds go in, a verdict comes out.
Testing it without a database or a vector store is what makes the rules
arguable - each test states a rule in its name and its assertion.
"""

import pytest

from app.core.settings import Settings
from app.models import RelationType
from app.services.relations import Candidate, classify


@pytest.fixture
def settings():
    return Settings()


def test_identical_bytes_are_an_exact_duplicate(settings):
    """Not a similarity question. The same file is the same file."""
    decision = classify(Candidate(document_id="d", same_file_hash=True, similarity=0.1), settings)

    assert decision.relation_type is RelationType.exact_duplicate
    assert decision.score == 1.0
    assert decision.reason["signals"] == ["file_hash"]


def test_identical_text_is_a_content_duplicate(settings):
    decision = classify(
        Candidate(document_id="d", same_content_hash=True, similarity=0.2), settings
    )

    assert decision.relation_type is RelationType.content_duplicate
    assert decision.reason["signals"] == ["content_hash"]


def test_an_exact_duplicate_outranks_everything_else(settings):
    """Ordering is the design: the most certain rule wins."""
    decision = classify(
        Candidate(
            document_id="d",
            same_file_hash=True,
            same_content_hash=True,
            similarity=0.99,
            shared_identifiers=["INV-1"],
        ),
        settings,
    )

    assert decision.relation_type is RelationType.exact_duplicate


def test_high_similarity_with_shared_parties_is_a_possible_version(settings):
    decision = classify(
        Candidate(
            document_id="d",
            similarity=0.95,
            shared_organizations=["Acme"],
            shared_dates=["2024-01-01"],
        ),
        settings,
    )

    assert decision.relation_type is RelationType.possible_version
    assert decision.reason["similarity"] == 0.95
    assert decision.reason["threshold"] == settings.relation_version_similarity


def test_high_similarity_alone_is_not_a_version(settings):
    """Two invoices off one template look alike without being versions."""
    decision = classify(Candidate(document_id="d", similarity=0.97), settings)

    assert decision.relation_type is not RelationType.possible_version
    assert decision.relation_type is RelationType.related


def test_a_shared_identifier_means_the_same_matter(settings):
    decision = classify(
        Candidate(document_id="d", similarity=0.3, shared_identifiers=["CASE-2024-17"]),
        settings,
    )

    assert decision.relation_type is RelationType.same_case
    assert decision.reason["shared_identifiers"] == ["CASE-2024-17"]


def test_shared_parties_without_an_identifier_are_the_same_entity(settings):
    decision = classify(
        Candidate(
            document_id="d",
            similarity=0.4,
            shared_organizations=["Acme"],
            shared_persons=["A. Muster"],
        ),
        settings,
    )

    assert decision.relation_type is RelationType.same_entity


def test_one_shared_party_is_not_enough(settings):
    """A single shared name links half a corpus; the floor is configurable."""
    decision = classify(
        Candidate(document_id="d", similarity=0.3, shared_organizations=["Acme"]), settings
    )

    assert decision is None


def test_similarity_above_the_related_threshold_is_related(settings):
    decision = classify(Candidate(document_id="d", similarity=0.8), settings)

    assert decision.relation_type is RelationType.related
    assert decision.reason["signals"] == ["document_vector"]


def test_an_unrelated_document_is_not_linked_at_all(settings):
    """The case that makes the feature useful rather than noise."""
    assert classify(Candidate(document_id="d", similarity=0.2), settings) is None
    assert classify(Candidate(document_id="d", similarity=None), settings) is None


def test_thresholds_come_from_configuration(settings):
    lenient = settings.model_copy(update={"relation_related_similarity": 0.1})
    candidate = Candidate(document_id="d", similarity=0.2)

    assert classify(candidate, settings) is None
    assert classify(candidate, lenient).relation_type is RelationType.related


def test_every_decision_records_the_signals_that_produced_it(settings):
    candidates = [
        Candidate(document_id="d", same_file_hash=True),
        Candidate(document_id="d", same_content_hash=True),
        Candidate(
            document_id="d", similarity=0.95, shared_organizations=["A"], shared_persons=["B"]
        ),
        Candidate(document_id="d", shared_identifiers=["X-1"]),
        Candidate(document_id="d", shared_organizations=["A"], shared_persons=["B"]),
        Candidate(document_id="d", similarity=0.8),
    ]

    for candidate in candidates:
        decision = classify(candidate, settings)
        assert decision is not None
        assert decision.reason.get("signals"), decision
