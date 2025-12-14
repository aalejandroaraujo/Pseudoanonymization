# Pseudo-Anonymizer

A Python CLI tool for anonymizing sensitive documents before sharing them with LLMs (like ChatGPT, Claude, etc.). Detect and replace personally identifiable information (PII) including names, emails, phone numbers, and more.

## Features

- **Multi-format support**: Process PDF, DOCX, and TXT files
- **PII Detection**: Uses Microsoft Presidio for accurate entity recognition
- **Custom Deny Lists**: Anonymize project names, company names, or any NDA-protected terms
- **Multiple anonymization methods**: mask, redact, replace, or hash sensitive data
- **Reversible anonymization**: Save mappings to restore original content later
- **Consistent pseudonyms**: Same name always maps to the same placeholder
- **Simple CLI**: Easy to use from the command line or VS Code terminal

## Installation

### 1. Clone or download the project

```bash
cd pseudo-anonymizer
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Download the spaCy language model

```bash
python -m spacy download en_core_web_sm
```

### 5. (Optional) Set up environment variables

```bash
cp .env.example .env
# Edit .env with your API keys
```

## Quick Start

### Basic usage - anonymize a document

```bash
python main.py anonymize -i document.pdf
```

### Anonymize with mapping for later reversal

```bash
python main.py anonymize -i report.docx --save-mapping mapping.json
```

### Restore anonymized document

```bash
python main.py de-anonymize -i report_anon.txt -m mapping.json
```

## Usage

### Anonymize Command

```bash
python main.py anonymize [OPTIONS]
```

**Options:**

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--input` | `-i` | Input file path (PDF, DOCX, TXT) | Required |
| `--output` | `-o` | Output file path | `{input}_anon.txt` |
| `--operator` | | Anonymization method | `replace` |
| `--entities` | | Comma-separated entity types | `PERSON,EMAIL_ADDRESS,PHONE_NUMBER` |
| `--save-mapping` | | Save mapping to JSON file | None |
| `--deny-list` | `-d` | Custom terms to anonymize (comma-separated or file path) | None |
| `--api-key` | | API key for future LLM features | None |

**Examples:**

```bash
# Basic anonymization
python main.py anonymize -i patient_report.pdf

# Mask names with asterisks
python main.py anonymize -i report.docx --operator mask

# Replace with pseudonyms and save mapping
python main.py anonymize -i data.txt --operator replace --save-mapping mapping.json

# Custom output path
python main.py anonymize -i document.pdf -o anonymized_document.txt

# Only anonymize emails and phone numbers
python main.py anonymize -i contacts.txt --entities EMAIL_ADDRESS,PHONE_NUMBER

# Detect all common PII types
python main.py anonymize -i file.pdf --entities PERSON,EMAIL_ADDRESS,PHONE_NUMBER,CREDIT_CARD,IP_ADDRESS,URL

# Anonymize custom terms (e.g., project names under NDA)
python main.py anonymize -i confidential.pdf --deny-list "ProjectX,SecretCorp" --save-mapping mapping.json

# Use a deny list file
python main.py anonymize -i document.pdf --deny-list deny_terms.txt
```

### De-anonymize Command

Restore an anonymized document using a saved mapping file:

```bash
python main.py de-anonymize -i document_anon.txt -m mapping.json
```

**Options:**

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--input` | `-i` | Anonymized file to restore | Required |
| `--mapping` | `-m` | Path to mapping JSON | Required |
| `--output` | `-o` | Output file path | `{input}_restored.txt` |

### Helper Commands

List available entity types:
```bash
python main.py entities
```

List available operators:
```bash
python main.py operators
```

View help:
```bash
python main.py --help
python main.py anonymize --help
```

## Anonymization Operators

| Operator | Description | Example | Reversible |
|----------|-------------|---------|------------|
| `replace` | Replace with pseudonyms | `John Smith` → `<PERSON_1>` | Yes |
| `mask` | Replace with asterisks | `John Smith` → `**********` | No |
| `redact` | Remove completely | `John Smith` → `` | No |
| `hash` | SHA256 hash | `John Smith` → `a1b2c3...` | No |

## Supported Entity Types

| Entity | Description |
|--------|-------------|
| `PERSON` | Names of people |
| `EMAIL_ADDRESS` | Email addresses |
| `PHONE_NUMBER` | Phone numbers |
| `CREDIT_CARD` | Credit card numbers |
| `IP_ADDRESS` | IP addresses |
| `URL` | URLs and web addresses |
| `LOCATION` | Physical locations |
| `DATE_TIME` | Dates and times |
| `US_SSN` | US Social Security Numbers |
| `IBAN_CODE` | Bank account numbers |
| `CUSTOM` | Custom deny list terms (see below) |

Run `python main.py entities` for the complete list.

## Custom Deny Lists

Beyond standard PII detection, you can specify custom terms to anonymize. This is useful for:

- **Project names** under NDA
- **Company names** that should remain confidential
- **Product codenames** or internal terminology
- **Any sensitive terms** specific to your documents

### Inline Deny List

Provide terms directly as a comma-separated string:

```bash
python main.py anonymize -i document.pdf --deny-list "ProjectX,SecretCorp,InternalCodename"
```

### Deny List File

Create a text file with one term per line:

```
# deny_terms.txt
ProjectX
SecretCorp
InternalCodename
```

Then reference it:

```bash
python main.py anonymize -i document.pdf --deny-list deny_terms.txt
```

### How It Works

Custom terms are matched case-insensitively with word boundaries. For example, if you add "ProjectX" to the deny list:

- "ProjectX" -> `<CUSTOM_1>`
- "PROJECTX" -> `<CUSTOM_1>`
- "projectx" -> `<CUSTOM_1>`
- "MyProjectX" -> Not matched (word boundary)

Custom terms appear as `<CUSTOM_N>` in the anonymized output and are included in the mapping file for reversal.

## Project Structure

```
pseudo-anonymizer/
├── main.py              # CLI entry point (Click)
├── config.py            # Configuration settings
├── anonymizer.py        # PII detection/anonymization (Presidio)
├── file_handler.py      # Document extraction (PDF/DOCX/TXT)
├── mapping_manager.py   # De-anonymization mapping storage
├── .env.example         # Environment variable template
├── requirements.txt     # Python dependencies
└── README.md            # This file
```

## Mapping File Format

The mapping JSON file stores the relationship between original and anonymized values:

```json
{
  "mapping": {
    "John Smith": "<PERSON_1>",
    "john.smith@email.com": "<EMAIL_ADDRESS_1>"
  },
  "reverse_mapping": {
    "<PERSON_1>": "John Smith",
    "<EMAIL_ADDRESS_1>": "john.smith@email.com"
  },
  "metadata": {
    "created_at": "2024-01-15T10:30:00",
    "entry_count": 2,
    "version": "1.0"
  }
}
```

## Workflow Example

### 1. Prepare a document for ChatGPT

```bash
# Anonymize the document
python main.py anonymize -i confidential_report.pdf --save-mapping report_mapping.json -o safe_report.txt

# Output: safe_report.txt (anonymized) + report_mapping.json (for reversal)
```

### 2. Use with ChatGPT

Copy the contents of `safe_report.txt` and paste into ChatGPT. All sensitive information has been replaced with placeholders like `<PERSON_1>`, `<EMAIL_ADDRESS_1>`, etc.

### 3. Get response from ChatGPT

Copy ChatGPT's response to a file (e.g., `chatgpt_response.txt`).

### 4. Restore original names

```bash
python main.py de-anonymize -i chatgpt_response.txt -m report_mapping.json -o final_response.txt
```

The `final_response.txt` will have all pseudonyms replaced with the original values.

## Tips

- **Always save the mapping** if you need to de-anonymize later
- **Use `replace` operator** for reversible anonymization
- **Test on a sample first** before processing large documents
- **Keep mapping files secure** - they contain the original PII values

## Troubleshooting

### "No module named 'spacy'" or model not found

```bash
pip install spacy
python -m spacy download en_core_web_sm
```

### PDF extraction returns empty text

The PDF might be image-based (scanned). pdfplumber only extracts text from text-based PDFs. Consider using OCR tools for scanned documents.

### "Permission denied" error

Ensure you have write permissions to the output directory.

## License

MIT License

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.
