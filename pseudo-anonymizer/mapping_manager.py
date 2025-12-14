"""
Mapping Manager Module

Handles saving and loading anonymization mappings for de-anonymization.
Supports JSON file storage with merge capabilities.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class MappingManagerError(Exception):
    """Custom exception for mapping manager errors."""
    pass


class MappingManager:
    """
    Manages anonymization mappings for de-anonymization support.
    """

    def __init__(self, mapping_file: Optional[str] = None):
        """
        Initialize the mapping manager.

        Args:
            mapping_file: Optional path to a mapping file to load.
        """
        self._mapping: Dict[str, str] = {}
        self._reverse_mapping: Dict[str, str] = {}
        self._metadata: Dict[str, str] = {}

        if mapping_file:
            self.load(mapping_file)

    @property
    def mapping(self) -> Dict[str, str]:
        """Get the current mapping (original -> anonymized)."""
        return self._mapping.copy()

    @property
    def reverse_mapping(self) -> Dict[str, str]:
        """Get the reverse mapping (anonymized -> original)."""
        return self._reverse_mapping.copy()

    def add_mapping(self, original: str, anonymized: str) -> None:
        """
        Add a single mapping entry.

        Args:
            original: Original value.
            anonymized: Anonymized value.
        """
        self._mapping[original] = anonymized
        self._reverse_mapping[anonymized] = original

    def add_mappings(self, mappings: Dict[str, str]) -> None:
        """
        Add multiple mapping entries.

        Args:
            mappings: Dictionary of original -> anonymized mappings.
        """
        for original, anonymized in mappings.items():
            self.add_mapping(original, anonymized)

    def get_anonymized(self, original: str) -> Optional[str]:
        """
        Get the anonymized value for an original value.

        Args:
            original: Original value to look up.

        Returns:
            Anonymized value or None if not found.
        """
        return self._mapping.get(original)

    def get_original(self, anonymized: str) -> Optional[str]:
        """
        Get the original value for an anonymized value.

        Args:
            anonymized: Anonymized value to look up.

        Returns:
            Original value or None if not found.
        """
        return self._reverse_mapping.get(anonymized)

    def save(self, output_file: str, include_metadata: bool = True) -> None:
        """
        Save the mapping to a JSON file.

        Args:
            output_file: Path to the output JSON file.
            include_metadata: Whether to include metadata in the output.

        Raises:
            MappingManagerError: If saving fails.
        """
        try:
            path = Path(output_file)
            path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "mapping": self._mapping,
                "reverse_mapping": self._reverse_mapping,
            }

            if include_metadata:
                data["metadata"] = {
                    "created_at": datetime.now().isoformat(),
                    "entry_count": len(self._mapping),
                    "version": "1.0",
                }

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.info(f"Mapping saved to: {output_file} ({len(self._mapping)} entries)")

        except Exception as e:
            raise MappingManagerError(f"Failed to save mapping: {e}")

    def load(self, mapping_file: str) -> None:
        """
        Load a mapping from a JSON file.

        Args:
            mapping_file: Path to the mapping JSON file.

        Raises:
            MappingManagerError: If loading fails.
        """
        try:
            path = Path(mapping_file)

            if not path.exists():
                raise MappingManagerError(f"Mapping file not found: {mapping_file}")

            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Handle different file formats
            if "mapping" in data:
                # New format with metadata
                self._mapping = data.get("mapping", {})
                self._reverse_mapping = data.get("reverse_mapping", {})
                self._metadata = data.get("metadata", {})
            else:
                # Simple format (just key-value pairs)
                self._mapping = data
                self._reverse_mapping = {v: k for k, v in data.items()}

            logger.info(f"Mapping loaded from: {mapping_file} ({len(self._mapping)} entries)")

        except json.JSONDecodeError as e:
            raise MappingManagerError(f"Invalid JSON in mapping file: {e}")
        except MappingManagerError:
            raise
        except Exception as e:
            raise MappingManagerError(f"Failed to load mapping: {e}")

    def merge(self, other_mapping: Dict[str, str]) -> None:
        """
        Merge another mapping into this one.
        Existing entries are not overwritten.

        Args:
            other_mapping: Mapping to merge.
        """
        for original, anonymized in other_mapping.items():
            if original not in self._mapping:
                self.add_mapping(original, anonymized)

    def clear(self) -> None:
        """Clear all mappings."""
        self._mapping.clear()
        self._reverse_mapping.clear()
        self._metadata.clear()

    def de_anonymize_text(self, text: str) -> str:
        """
        De-anonymize text using the reverse mapping.

        Args:
            text: Anonymized text to restore.

        Returns:
            De-anonymized text with original values restored.
        """
        result = text
        for anonymized, original in self._reverse_mapping.items():
            result = result.replace(anonymized, original)
        return result

    def __len__(self) -> int:
        """Return the number of mapping entries."""
        return len(self._mapping)

    def __contains__(self, item: str) -> bool:
        """Check if an original value is in the mapping."""
        return item in self._mapping


def save_mapping(mapping: Dict[str, str], output_file: str) -> None:
    """
    Convenience function to save a mapping to a JSON file.

    Args:
        mapping: Dictionary mapping original values to anonymized values.
        output_file: Path to the output JSON file.
    """
    manager = MappingManager()
    manager.add_mappings(mapping)
    manager.save(output_file)


def load_mapping(mapping_file: str) -> Dict[str, str]:
    """
    Convenience function to load a mapping from a JSON file.

    Args:
        mapping_file: Path to the mapping JSON file.

    Returns:
        Dictionary mapping original values to anonymized values.
    """
    manager = MappingManager(mapping_file)
    return manager.mapping
