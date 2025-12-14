"""
Unit tests for the mapping_manager module.
"""

import pytest
import json
from pathlib import Path

from mapping_manager import (
    MappingManager,
    save_mapping,
    load_mapping,
    MappingManagerError,
)


class TestMappingManager:
    """Tests for MappingManager class."""

    def test_initialization(self):
        """Should initialize empty manager."""
        manager = MappingManager()
        assert len(manager) == 0

    def test_add_mapping(self):
        """Should add single mapping."""
        manager = MappingManager()
        manager.add_mapping("John", "<PERSON_1>")

        assert manager.get_anonymized("John") == "<PERSON_1>"

    def test_add_mappings(self):
        """Should add multiple mappings."""
        manager = MappingManager()
        manager.add_mappings({
            "John": "<PERSON_1>",
            "jane@email.com": "<EMAIL_ADDRESS_1>",
        })

        assert len(manager) == 2

    def test_get_anonymized(self):
        """Should return anonymized value."""
        manager = MappingManager()
        manager.add_mapping("Original", "Anonymized")

        assert manager.get_anonymized("Original") == "Anonymized"
        assert manager.get_anonymized("NotFound") is None

    def test_get_original(self):
        """Should return original value from reverse mapping."""
        manager = MappingManager()
        manager.add_mapping("Original", "Anonymized")

        assert manager.get_original("Anonymized") == "Original"
        assert manager.get_original("NotFound") is None

    def test_save_and_load(self, tmp_path):
        """Should save and load mappings correctly."""
        mapping_file = tmp_path / "mapping.json"

        # Save
        manager1 = MappingManager()
        manager1.add_mappings({
            "John Smith": "<PERSON_1>",
            "test@example.com": "<EMAIL_ADDRESS_1>",
        })
        manager1.save(str(mapping_file))

        assert mapping_file.exists()

        # Load
        manager2 = MappingManager(str(mapping_file))

        assert manager2.get_anonymized("John Smith") == "<PERSON_1>"
        assert manager2.get_original("<EMAIL_ADDRESS_1>") == "test@example.com"

    def test_save_creates_directories(self, tmp_path):
        """Should create parent directories when saving."""
        mapping_file = tmp_path / "nested" / "dir" / "mapping.json"

        manager = MappingManager()
        manager.add_mapping("test", "value")
        manager.save(str(mapping_file))

        assert mapping_file.exists()

    def test_load_nonexistent_file(self):
        """Should raise error for non-existent file."""
        with pytest.raises(MappingManagerError, match="not found"):
            MappingManager("/nonexistent/mapping.json")

    def test_load_invalid_json(self, tmp_path):
        """Should raise error for invalid JSON."""
        mapping_file = tmp_path / "invalid.json"
        mapping_file.write_text("not valid json")

        with pytest.raises(MappingManagerError, match="Invalid JSON"):
            MappingManager(str(mapping_file))

    def test_load_simple_format(self, tmp_path):
        """Should load simple key-value format."""
        mapping_file = tmp_path / "simple.json"
        mapping_file.write_text(json.dumps({
            "John": "<PERSON_1>",
            "Jane": "<PERSON_2>",
        }))

        manager = MappingManager(str(mapping_file))

        assert manager.get_anonymized("John") == "<PERSON_1>"
        assert manager.get_original("<PERSON_2>") == "Jane"

    def test_merge_mappings(self):
        """Should merge mappings without overwriting."""
        manager = MappingManager()
        manager.add_mapping("John", "<PERSON_1>")

        manager.merge({
            "John": "<PERSON_99>",  # Should not overwrite
            "Jane": "<PERSON_2>",   # Should add
        })

        assert manager.get_anonymized("John") == "<PERSON_1>"
        assert manager.get_anonymized("Jane") == "<PERSON_2>"

    def test_clear_mappings(self):
        """Should clear all mappings."""
        manager = MappingManager()
        manager.add_mappings({"a": "1", "b": "2"})

        manager.clear()

        assert len(manager) == 0

    def test_de_anonymize_text(self):
        """Should restore original values in text."""
        manager = MappingManager()
        manager.add_mappings({
            "John Smith": "<PERSON_1>",
            "jane@email.com": "<EMAIL_ADDRESS_1>",
        })

        text = "Hello <PERSON_1>, your email is <EMAIL_ADDRESS_1>."
        result = manager.de_anonymize_text(text)

        assert result == "Hello John Smith, your email is jane@email.com."

    def test_contains(self):
        """Should support 'in' operator."""
        manager = MappingManager()
        manager.add_mapping("John", "<PERSON_1>")

        assert "John" in manager
        assert "Jane" not in manager

    def test_mapping_property(self):
        """Should return copy of mapping."""
        manager = MappingManager()
        manager.add_mapping("John", "<PERSON_1>")

        mapping = manager.mapping
        mapping["New"] = "Value"  # Modify returned dict

        assert "New" not in manager.mapping  # Original unchanged

    def test_reverse_mapping_property(self):
        """Should return copy of reverse mapping."""
        manager = MappingManager()
        manager.add_mapping("John", "<PERSON_1>")

        reverse = manager.reverse_mapping

        assert reverse["<PERSON_1>"] == "John"

    def test_save_with_metadata(self, tmp_path):
        """Should include metadata in saved file."""
        mapping_file = tmp_path / "mapping.json"

        manager = MappingManager()
        manager.add_mapping("test", "value")
        manager.save(str(mapping_file), include_metadata=True)

        with open(mapping_file) as f:
            data = json.load(f)

        assert "metadata" in data
        assert "created_at" in data["metadata"]
        assert "entry_count" in data["metadata"]


class TestConvenienceFunctions:
    """Tests for save_mapping and load_mapping functions."""

    def test_save_mapping(self, tmp_path):
        """save_mapping should create valid JSON file."""
        mapping_file = tmp_path / "mapping.json"

        save_mapping(
            {"John": "<PERSON_1>", "jane@email.com": "<EMAIL_ADDRESS_1>"},
            str(mapping_file)
        )

        assert mapping_file.exists()

    def test_load_mapping(self, tmp_path):
        """load_mapping should return dictionary."""
        mapping_file = tmp_path / "mapping.json"

        save_mapping({"John": "<PERSON_1>"}, str(mapping_file))
        result = load_mapping(str(mapping_file))

        assert result["John"] == "<PERSON_1>"

    def test_roundtrip(self, tmp_path):
        """Should be able to save and load mapping."""
        mapping_file = tmp_path / "mapping.json"
        original = {
            "John Smith": "<PERSON_1>",
            "test@example.com": "<EMAIL_ADDRESS_1>",
            "555-1234": "<PHONE_NUMBER_1>",
        }

        save_mapping(original, str(mapping_file))
        loaded = load_mapping(str(mapping_file))

        assert loaded == original


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
