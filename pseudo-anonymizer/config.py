"""
Configuration Module

Centralized configuration for the pseudo-anonymizer application.
Handles default settings, environment variables, and custom configurations.
"""

import os
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """
    Application configuration container.
    """

    # Supported file formats
    SUPPORTED_FORMATS: Set[str] = field(default_factory=lambda: {'.pdf', '.docx', '.txt'})

    # Default entity types to detect
    DEFAULT_ENTITIES: List[str] = field(default_factory=lambda: [
        "PERSON",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "CREDIT_CARD",
        "IP_ADDRESS",
        "URL",
    ])

    # All available entity types from Presidio
    ALL_ENTITIES: List[str] = field(default_factory=lambda: [
        "PERSON",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "CREDIT_CARD",
        "IP_ADDRESS",
        "URL",
        "LOCATION",
        "DATE_TIME",
        "NRP",  # Nationality, Religious, Political group
        "MEDICAL_LICENSE",
        "US_BANK_NUMBER",
        "US_DRIVER_LICENSE",
        "US_ITIN",
        "US_PASSPORT",
        "US_SSN",
        "UK_NHS",
        "IBAN_CODE",
        "CRYPTO",
        "CUSTOM",  # Custom deny list terms
    ])

    # Supported anonymization operators
    SUPPORTED_OPERATORS: Set[str] = field(default_factory=lambda: {
        'mask',
        'redact',
        'replace',
        'hash',
    })

    # Default operator
    DEFAULT_OPERATOR: str = 'replace'

    # Default language for analysis
    DEFAULT_LANGUAGE: str = 'en'

    # Output format options
    OUTPUT_FORMATS: Set[str] = field(default_factory=lambda: {'.txt', '.md'})

    # Default output format
    DEFAULT_OUTPUT_FORMAT: str = '.txt'

    # Logging configuration
    LOG_LEVEL: str = 'INFO'
    LOG_FORMAT: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # API Keys (loaded from environment)
    api_key_claude: Optional[str] = None
    api_key_openai: Optional[str] = None

    def __post_init__(self):
        """Load API keys from environment after initialization."""
        self.api_key_claude = os.getenv('API_KEY_CLAUDE')
        self.api_key_openai = os.getenv('API_KEY_OPENAI')

    def set_api_key(self, key_type: str, value: str) -> None:
        if key_type.lower() == 'claude':
            self.api_key_claude = value
        elif key_type.lower() == 'openai':
            self.api_key_openai = value
        else:
            logger.warning(f"Unknown API key type: {key_type}")

    def get_api_key(self, key_type: str) -> Optional[str]:
        if key_type.lower() == 'claude':
            return self.api_key_claude
        elif key_type.lower() == 'openai':
            return self.api_key_openai
        return None

    def validate_entities(self, entities: List[str]) -> List[str]:
        validated = []
        all_entities_set = set(self.ALL_ENTITIES)
        for entity in entities:
            normalized = entity.upper().strip()
            if normalized in all_entities_set:
                validated.append(normalized)
            else:
                logger.warning(f"Unknown entity type: {entity}. Skipping.")
        if not validated:
            raise ValueError(f"No valid entity types provided. Available: {', '.join(self.ALL_ENTITIES)}")
        return validated

    def validate_operator(self, operator: str) -> str:
        normalized = operator.lower().strip()
        if normalized not in self.SUPPORTED_OPERATORS:
            raise ValueError(f"Invalid operator: {operator}. Supported: {', '.join(self.SUPPORTED_OPERATORS)}")
        return normalized

    def validate_file_format(self, file_path: str) -> bool:
        suffix = Path(file_path).suffix.lower()
        return suffix in self.SUPPORTED_FORMATS


# Operator configurations for different use cases
OPERATOR_CONFIGS: Dict[str, Dict] = {
    'mask': {
        'description': 'Replace characters with asterisks',
        'example': 'John Smith -> **** *****',
        'reversible': False,
    },
    'redact': {
        'description': 'Remove the entity completely',
        'example': 'John Smith -> [REDACTED]',
        'reversible': False,
    },
    'replace': {
        'description': 'Replace with consistent pseudonyms',
        'example': 'John Smith -> <PERSON_1>',
        'reversible': True,
    },
    'hash': {
        'description': 'One-way hash the entity',
        'example': 'John Smith -> a1b2c3d4...',
        'reversible': False,
    },
}


# Entity type descriptions
ENTITY_DESCRIPTIONS: Dict[str, str] = {
    'PERSON': 'Names of people',
    'EMAIL_ADDRESS': 'Email addresses',
    'PHONE_NUMBER': 'Phone numbers',
    'CREDIT_CARD': 'Credit card numbers',
    'IP_ADDRESS': 'IP addresses',
    'URL': 'URLs and web addresses',
    'LOCATION': 'Physical locations and addresses',
    'DATE_TIME': 'Dates and times',
    'US_SSN': 'US Social Security Numbers',
    'IBAN_CODE': 'International Bank Account Numbers',
    'CRYPTO': 'Cryptocurrency addresses',
    'CUSTOM': 'Custom deny list terms (project names, company names, etc.)',
}


def load_deny_list(file_path: str) -> List[str]:
    """Load a deny list from a file (one term per line)."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Deny list file not found: {file_path}")
    terms = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            term = line.strip()
            if term and not term.startswith('#'):
                terms.append(term)
    logger.info(f"Loaded {len(terms)} terms from deny list: {file_path}")
    return terms


def parse_deny_list(deny_list_input: str) -> List[str]:
    """Parse deny list from either a file path or comma-separated string."""
    if Path(deny_list_input).exists():
        return load_deny_list(deny_list_input)
    terms = [term.strip() for term in deny_list_input.split(',') if term.strip()]
    logger.info(f"Parsed {len(terms)} terms from inline deny list")
    return terms


def setup_logging(level: str = 'INFO', log_file: Optional[str] = None) -> None:
    """Configure application logging."""
    cfg = Config()
    handlers: List[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=cfg.LOG_FORMAT,
        handlers=handlers,
    )
    logger.debug(f"Logging configured at level: {level}")


# Global configuration instance
config = Config()
