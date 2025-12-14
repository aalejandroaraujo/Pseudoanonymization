"""
Unit tests for the anonymizer module.
"""

import pytest
from anonymizer import DocumentAnonymizer, anonymize_text, PseudonymGenerator


class TestPseudonymGenerator:
    """Tests for PseudonymGenerator class."""

    def test_consistent_pseudonyms(self):
        """Same input should always produce same pseudonym."""
        gen = PseudonymGenerator()

        first = gen.get_pseudonym("PERSON", "John Smith")
        second = gen.get_pseudonym("PERSON", "John Smith")

        assert first == second

    def test_different_names_different_pseudonyms(self):
        """Different inputs should produce different pseudonyms."""
        gen = PseudonymGenerator()

        john = gen.get_pseudonym("PERSON", "John Smith")
        jane = gen.get_pseudonym("PERSON", "Jane Doe")

        assert john != jane

    def test_case_insensitive(self):
        """Pseudonyms should be case-insensitive."""
        gen = PseudonymGenerator()

        lower = gen.get_pseudonym("PERSON", "john smith")
        upper = gen.get_pseudonym("PERSON", "JOHN SMITH")

        assert lower == upper

    def test_incremental_numbering(self):
        """Pseudonyms should have incremental numbering."""
        gen = PseudonymGenerator()

        first = gen.get_pseudonym("PERSON", "John")
        second = gen.get_pseudonym("PERSON", "Jane")
        third = gen.get_pseudonym("PERSON", "Bob")

        assert first == "<PERSON_1>"
        assert second == "<PERSON_2>"
        assert third == "<PERSON_3>"

    def test_different_entity_types(self):
        """Different entity types should have separate counters."""
        gen = PseudonymGenerator()

        person = gen.get_pseudonym("PERSON", "John")
        email = gen.get_pseudonym("EMAIL_ADDRESS", "john@example.com")

        assert person == "<PERSON_1>"
        assert email == "<EMAIL_ADDRESS_1>"

    def test_get_full_mapping(self):
        """Full mapping should contain all entries."""
        gen = PseudonymGenerator()

        gen.get_pseudonym("PERSON", "John")
        gen.get_pseudonym("EMAIL_ADDRESS", "john@example.com")

        mapping = gen.get_full_mapping()

        assert len(mapping) == 2
        assert "john" in mapping
        assert "john@example.com" in mapping


class TestDocumentAnonymizer:
    """Tests for DocumentAnonymizer class."""

    def test_initialization(self):
        """Anonymizer should initialize without errors."""
        anonymizer = DocumentAnonymizer()
        assert anonymizer is not None

    def test_custom_entities(self):
        """Custom entity list should be used."""
        anonymizer = DocumentAnonymizer(entities=["PERSON"])
        assert anonymizer.entities == ["PERSON"]

    def test_analyze_person(self):
        """Should detect person names."""
        anonymizer = DocumentAnonymizer(entities=["PERSON"])
        results = anonymizer.analyze("John Smith went to the store.")

        assert len(results) > 0
        assert results[0].entity_type == "PERSON"

    def test_analyze_email(self):
        """Should detect email addresses."""
        anonymizer = DocumentAnonymizer(entities=["EMAIL_ADDRESS"])
        results = anonymizer.analyze("Contact us at test@example.com")

        assert len(results) > 0
        assert results[0].entity_type == "EMAIL_ADDRESS"

    def test_analyze_phone(self):
        """Should detect phone numbers."""
        anonymizer = DocumentAnonymizer(entities=["PHONE_NUMBER"])
        results = anonymizer.analyze("Call me at 555-123-4567")

        assert len(results) > 0
        assert results[0].entity_type == "PHONE_NUMBER"

    def test_anonymize_replace(self):
        """Replace operator should create pseudonyms."""
        anonymizer = DocumentAnonymizer(entities=["PERSON"])
        text = "John Smith is a customer."

        result, mapping = anonymizer.anonymize_text(text, "replace")

        assert "<PERSON_" in result
        assert "John Smith" not in result
        assert len(mapping) > 0

    def test_anonymize_mask(self):
        """Mask operator should replace with asterisks."""
        anonymizer = DocumentAnonymizer(entities=["EMAIL_ADDRESS"])
        text = "Email: test@example.com"

        result, mapping = anonymizer.anonymize_text(text, "mask")

        assert "test@example.com" not in result
        assert "*" in result

    def test_anonymize_redact(self):
        """Redact operator should remove entities."""
        anonymizer = DocumentAnonymizer(entities=["EMAIL_ADDRESS"])
        text = "Email: test@example.com end"

        result, mapping = anonymizer.anonymize_text(text, "redact")

        assert "test@example.com" not in result

    def test_no_pii_detected(self):
        """Should return original text when no PII found."""
        anonymizer = DocumentAnonymizer(entities=["PERSON"])
        text = "The quick brown fox jumps over the lazy dog."

        result, mapping = anonymizer.anonymize_text(text, "replace")

        assert result == text
        assert len(mapping) == 0

    def test_consistent_replacement(self):
        """Same name should be replaced consistently."""
        anonymizer = DocumentAnonymizer(entities=["PERSON"])
        text = "John Smith met John Smith at the park."

        result, mapping = anonymizer.anonymize_text(text, "replace")

        # Count occurrences of the pseudonym
        pseudonym = list(mapping.values())[0]
        assert result.count(pseudonym) == 2

    def test_get_entity_summary(self):
        """Should return accurate entity counts."""
        anonymizer = DocumentAnonymizer(entities=["PERSON", "EMAIL_ADDRESS"])
        text = "John Smith (john@example.com) and Jane Doe (jane@example.com)"

        summary = anonymizer.get_entity_summary(text)

        assert "PERSON" in summary
        assert "EMAIL_ADDRESS" in summary


class TestAnonymizeTextFunction:
    """Tests for the convenience function."""

    def test_basic_usage(self):
        """Convenience function should work."""
        text = "John Smith's email is john@example.com"
        result, mapping = anonymize_text(text)

        assert "John Smith" not in result
        assert "john@example.com" not in result

    def test_custom_entities(self):
        """Should accept custom entity list."""
        text = "John Smith's email is john@example.com"
        result, mapping = anonymize_text(text, entities=["EMAIL_ADDRESS"])

        # Person should not be anonymized, email should be
        assert "john@example.com" not in result

    def test_custom_operator(self):
        """Should accept custom operator."""
        text = "Email: test@example.com"
        result, mapping = anonymize_text(text, operator="mask", entities=["EMAIL_ADDRESS"])

        assert "*" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
