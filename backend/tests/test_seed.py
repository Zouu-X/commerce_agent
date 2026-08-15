from app.commerce.seed import build_knowledge_objects, build_seed_objects, stable_id


def test_seed_dataset_is_deterministic_and_has_expected_scale() -> None:
    first_objects, first_counts = build_seed_objects()
    second_objects, second_counts = build_seed_objects()

    assert first_counts == second_counts
    assert first_counts == {
        "tenants": 2,
        "stores": 2,
        "customers": 12,
        "products": 24,
        "variants": 48,
        "orders": 60,
        "shipments": 28,
        "shipment_events": 56,
        "after_sales": 6,
    }
    assert len(first_objects) == len(second_objects)
    assert stable_id("tenant:aurora") == stable_id("tenant:aurora")
    assert stable_id("tenant:aurora") != stable_id("tenant:harbor")


def test_knowledge_seed_is_deterministic_and_includes_expired_boundaries() -> None:
    first_objects, first_counts = build_knowledge_objects()
    second_objects, second_counts = build_knowledge_objects()

    assert first_counts == second_counts == {
        "knowledge_documents": 28,
        "knowledge_chunks": 28,
    }
    assert len(first_objects) == len(second_objects) == 56
