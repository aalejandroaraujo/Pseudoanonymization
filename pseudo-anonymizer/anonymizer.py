"""
Anonymizer Module

Core anonymization logic using Microsoft Presidio.
Detects and anonymizes PII entities with configurable operators.
"""

import re
import logging
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from presidio_analyzer import AnalyzerEngine, RecognizerResult, Pattern, PatternRecognizer
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

logger = logging.getLogger(__name__)


class AnonymizerError(Exception):
    """Custom exception for anonymization errors."""
    pass


class DenyListRecognizer(PatternRecognizer):
    """
    Custom recognizer for user-defined deny list terms.
    Matches exact terms (case-insensitive) from a provided list.
    """

    def __init__(self, deny_list: List[str], supported_language: str = "en"):
        patterns = []
        for term in deny_list:
            escaped_term = re.escape(term)
            # Word boundary pattern for case-insensitive matching
            pattern_str = r"(?i)\b" + escaped_term + r"\b"
            patterns.append(
                Pattern(
                    name=f"deny_list_{term[:20]}",
                    regex=pattern_str,
                    score=0.9
                )
            )

        super().__init__(
            supported_entity="CUSTOM",
            patterns=patterns,
            supported_language=supported_language,
            name="DenyListRecognizer",
        )
        self.deny_list = deny_list
        logger.info(f"DenyListRecognizer initialized with {len(deny_list)} terms")


class PseudonymGenerator:
    """Generates consistent pseudonyms for detected entities."""

    def __init__(self):
        self._counters: Dict[str, int] = defaultdict(int)
        self._mapping: Dict[str, Dict[str, str]] = defaultdict(dict)

    def get_pseudonym(self, entity_type: str, original_value: str) -> str:
        normalized = original_value.strip().lower()
        if normalized in self._mapping[entity_type]:
            return self._mapping[entity_type][normalized]
        self._counters[entity_type] += 1
        pseudonym = f"<{entity_type}_{self._counters[entity_type]}>"
        self._mapping[entity_type][normalized] = pseudonym
        return pseudonym

    def get_full_mapping(self) -> Dict[str, str]:
        result = {}
        for entity_type, mappings in self._mapping.items():
            for original, pseudonym in mappings.items():
                result[original] = pseudonym
        return result


class DocumentAnonymizer:
    """Handles document anonymization using Presidio."""

    DEFAULT_ENTITIES = [
        "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER",
        "CREDIT_CARD", "IP_ADDRESS", "URL",
    ]

    SUPPORTED_OPERATORS = {"mask", "redact", "replace", "hash"}

    def __init__(
        self,
        entities: Optional[List[str]] = None,
        language: str = "en",
        deny_list: Optional[List[str]] = None
    ):
        self.entities = entities or self.DEFAULT_ENTITIES
        self.language = language
        self.deny_list = deny_list or []
        self._pseudonym_generator = PseudonymGenerator()

        try:
            self._analyzer = AnalyzerEngine()
            self._anonymizer = AnonymizerEngine()

            if self.deny_list:
                deny_list_recognizer = DenyListRecognizer(
                    deny_list=self.deny_list,
                    supported_language=self.language
                )
                self._analyzer.registry.add_recognizer(deny_list_recognizer)
                if "CUSTOM" not in self.entities:
                    self.entities = list(self.entities) + ["CUSTOM"]
                logger.info(f"Added deny list with {len(self.deny_list)} terms")

            logger.info("Presidio engines initialized successfully")
        except Exception as e:
            raise AnonymizerError(f"Failed to initialize Presidio: {e}")

    def analyze(self, text: str) -> List[RecognizerResult]:
        try:
            results = self._analyzer.analyze(
                text=text, entities=self.entities, language=self.language
            )
            logger.info(f"Detected {len(results)} entities")
            return results
        except Exception as e:
            raise AnonymizerError(f"Analysis failed: {e}")

    def _create_operator_config(self, operator: str, text: str, results: List[RecognizerResult]) -> Dict[str, OperatorConfig]:
        if operator not in self.SUPPORTED_OPERATORS:
            raise AnonymizerError(f"Unsupported operator: {operator}")

        operators = {}
        all_entity_types = set(self.entities)
        for result in results:
            all_entity_types.add(result.entity_type)

        if operator == "mask":
            for et in all_entity_types:
                operators[et] = OperatorConfig("mask", {"masking_char": "*", "chars_to_mask": 100, "from_end": False})
        elif operator == "redact":
            for et in all_entity_types:
                operators[et] = OperatorConfig("replace", {"new_value": "[REDACTED]"})
        elif operator == "hash":
            for et in all_entity_types:
                operators[et] = OperatorConfig("hash", {"hash_type": "sha256"})
        return operators

    def anonymize_text(self, text: str, operator: str = "replace") -> Tuple[str, Dict[str, str]]:
        results = self.analyze(text)
        if not results:
            logger.info("No PII detected in text")
            return text, {}

        entity_counts = defaultdict(int)
        for r in results:
            entity_counts[r.entity_type] += 1
        logger.info(f"Entity detection summary: {dict(entity_counts)}")

        if operator == "replace":
            return self._replace_with_pseudonyms(text, results)
        else:
            operators = self._create_operator_config(operator, text, results)
            try:
                anonymized = self._anonymizer.anonymize(text=text, analyzer_results=results, operators=operators)
                mapping = self._build_mapping(text, results, anonymized.text)
                return anonymized.text, mapping
            except Exception as e:
                raise AnonymizerError(f"Anonymization failed: {e}")

    def _replace_with_pseudonyms(self, text: str, results: List[RecognizerResult]) -> Tuple[str, Dict[str, str]]:
        mapping = {}
        sorted_results = sorted(results, key=lambda x: x.start, reverse=True)
        anonymized_text = text
        for result in sorted_results:
            original_value = text[result.start:result.end]
            pseudonym = self._pseudonym_generator.get_pseudonym(result.entity_type, original_value)
            mapping[original_value] = pseudonym
            anonymized_text = anonymized_text[:result.start] + pseudonym + anonymized_text[result.end:]
        return anonymized_text, mapping

    def _build_mapping(self, original_text: str, results: List[RecognizerResult], anonymized_text: str) -> Dict[str, str]:
        mapping = {}
        for result in results:
            original_value = original_text[result.start:result.end]
            mapping[original_value] = f"[{result.entity_type}]"
        return mapping

    def get_entity_summary(self, text: str) -> Dict[str, int]:
        results = self.analyze(text)
        summary = defaultdict(int)
        for r in results:
            summary[r.entity_type] += 1
        return dict(summary)


def anonymize_text(
    text: str,
    operator: str = "replace",
    entities: Optional[List[str]] = None,
    deny_list: Optional[List[str]] = None
) -> Tuple[str, Dict[str, str]]:
    """Convenience function to anonymize text."""
    anonymizer = DocumentAnonymizer(entities=entities, deny_list=deny_list)
    return anonymizer.anonymize_text(text, operator)
