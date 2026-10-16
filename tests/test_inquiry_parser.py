from inquiry_parser import EXAMPLE_INQUIRY, analyze_inquiry


def test_example_inquiry_extracts_explicit_details_without_guessing_port() -> None:
    result = analyze_inquiry(EXAMPLE_INQUIRY)

    assert result.country == "德国"
    assert result.product_name == "wooden dining tables"
    assert result.quantity.startswith("200")
    assert result.destination_port == "Hamburg（原文提及交付地点，未明确为目的港）"
    assert "未明确为目的港" in result.destination_port
    assert result.expected_delivery.startswith("45 days")
    assert result.certification_requirements.startswith("FSC")
    assert "来自德国" in result.customer_summary
    assert any("目的港" in item for item in result.missing_information)
    assert any("认证" in action for action in result.next_actions)


def test_unstated_information_is_marked_unknown_or_not_mentioned() -> None:
    result = analyze_inquiry("Hello, please send your catalog.")

    assert result.country == "未知"
    assert result.product_name == "未提及"
    assert result.quantity == "未提及"
    assert result.destination_port == "未提及"
    assert result.expected_delivery == "未提及"
    assert result.certification_requirements == "未提及"


def test_explicit_destination_port_is_distinguished_from_delivery_location() -> None:
    result = analyze_inquiry("Please ship 50 dining tables to port of Hamburg.")

    assert result.destination_port == "Hamburg（原文明示为目的港）"
    assert not any("尚未明确目的港" in item for item in result.missing_information)


def test_country_is_not_inferred_from_a_shipping_destination() -> None:
    result = analyze_inquiry("Please ship 50 dining tables to Germany.")

    assert result.country == "未知"


def test_unrelated_number_is_not_reported_as_purchase_quantity() -> None:
    result = analyze_inquiry("Our 2025 catalog includes several wooden dining tables.")

    assert result.quantity == "未提及"
