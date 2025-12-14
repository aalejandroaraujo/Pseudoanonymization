"""
Unit tests for the file_handler module.
"""

import pytest
import tempfile
import os
from pathlib import Path

from file_handler import (
    validate_file,
    extract_text_from_txt,
    extract_text_from_file,
    write_output_file,
    get_default_output_path,
    FileHandlerError,
    SUPPORTED_FORMATS,
)


class TestValidateFile:
    """Tests for file validation."""

    def test_valid_txt_file(self, tmp_path):
        """Should validate existing TXT file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        result = validate_file(str(test_file))
        assert result == test_file

    def test_file_not_found(self):
        """Should raise error for non-existent file."""
        with pytest.raises(FileHandlerError, match="File not found"):
            validate_file("/nonexistent/file.txt")

    def test_unsupported_format(self, tmp_path):
        """Should raise error for unsupported format."""
        test_file = tmp_path / "test.xyz"
        test_file.write_text("test")

        with pytest.raises(FileHandlerError, match="Unsupported file format"):
            validate_file(str(test_file))

    def test_directory_not_file(self, tmp_path):
        """Should raise error when path is directory."""
        with pytest.raises(FileHandlerError, match="not a file"):
            validate_file(str(tmp_path))


class TestExtractTextFromTxt:
    """Tests for TXT extraction."""

    def test_basic_extraction(self, tmp_path):
        """Should extract text from TXT file."""
        test_file = tmp_path / "test.txt"
        content = "Hello, World!"
        test_file.write_text(content)

        result = extract_text_from_txt(test_file)
        assert result == content

    def test_multiline_content(self, tmp_path):
        """Should preserve multiline content."""
        test_file = tmp_path / "test.txt"
        content = "Line 1\nLine 2\nLine 3"
        test_file.write_text(content)

        result = extract_text_from_txt(test_file)
        assert result == content

    def test_unicode_content(self, tmp_path):
        """Should handle unicode characters."""
        test_file = tmp_path / "test.txt"
        content = "Héllo, Wörld! 日本語"
        test_file.write_text(content, encoding='utf-8')

        result = extract_text_from_txt(test_file)
        assert result == content


class TestExtractTextFromFile:
    """Tests for the main extraction function."""

    def test_extract_txt(self, tmp_path):
        """Should extract from TXT files."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Test content")

        result = extract_text_from_file(str(test_file))
        assert result == "Test content"

    def test_nonexistent_file(self):
        """Should raise error for non-existent file."""
        with pytest.raises(FileHandlerError):
            extract_text_from_file("/nonexistent/file.txt")


class TestWriteOutputFile:
    """Tests for output file writing."""

    def test_write_basic(self, tmp_path):
        """Should write content to file."""
        output_path = tmp_path / "output.txt"
        content = "Anonymized content"

        write_output_file(content, str(output_path))

        assert output_path.exists()
        assert output_path.read_text() == content

    def test_create_parent_directories(self, tmp_path):
        """Should create parent directories if needed."""
        output_path = tmp_path / "nested" / "dir" / "output.txt"
        content = "Test"

        write_output_file(content, str(output_path))

        assert output_path.exists()

    def test_overwrite_existing(self, tmp_path):
        """Should overwrite existing file."""
        output_path = tmp_path / "output.txt"
        output_path.write_text("Original")

        write_output_file("New content", str(output_path))

        assert output_path.read_text() == "New content"


class TestGetDefaultOutputPath:
    """Tests for default output path generation."""

    def test_basic_path(self):
        """Should append _anon suffix."""
        result = get_default_output_path("/path/to/document.pdf")
        assert result.endswith("document_anon.txt")

    def test_custom_suffix(self):
        """Should use custom suffix."""
        result = get_default_output_path("/path/to/document.pdf", suffix="_clean")
        assert result.endswith("document_clean.txt")

    def test_preserves_directory(self):
        """Should preserve original directory."""
        result = get_default_output_path("/some/path/doc.pdf")
        assert "/some/path/" in result or "\\some\\path\\" in result


class TestSupportedFormats:
    """Tests for supported format constants."""

    def test_pdf_supported(self):
        """PDF should be supported."""
        assert '.pdf' in SUPPORTED_FORMATS

    def test_docx_supported(self):
        """DOCX should be supported."""
        assert '.docx' in SUPPORTED_FORMATS

    def test_txt_supported(self):
        """TXT should be supported."""
        assert '.txt' in SUPPORTED_FORMATS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
