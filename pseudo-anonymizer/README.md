# Pseudo-Anonymizer

Anonymize sensitive documents before sharing them with LLMs. Detects PII (names, emails, phones, etc.) and replaces them with consistent pseudonyms. Also blurs text in images embedded in PDFs.

## What it does

1. Takes a document (PDF, DOCX, TXT) or image
2. Detects personally identifiable information using Microsoft Presidio
3. Replaces sensitive data with placeholders like `<PERSON_1>`, `<EMAIL_ADDRESS_1>`
4. Saves a mapping file so you can restore the original later
5. **PDF-to-PDF anonymization** with image text blurring using OCR
6. **Fuzzy matching** for OCR text detection (handles OCR misreadings)

The idea: you want to use ChatGPT/Claude to help with a confidential document, but you can't share the real names. Anonymize first, get LLM help, then de-anonymize the response.

## Vibe code alert

99% of this repo was vibe coded with Claude. The code works but don't expect production-grade engineering. PRs welcome if you want to clean things up.

## Setup

```bash
# clone and enter
cd pseudo-anonymizer

# create venv (recommended)
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # macOS/Linux

# install deps
pip install -r requirements.txt

# download spacy model
python -m spacy download en_core_web_sm
```

For image blurring, you also need [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) installed.

## Usage

### CLI

```bash
# basic anonymization
python main.py anonymize -i document.pdf

# save mapping for later reversal
python main.py anonymize -i report.docx --save-mapping mapping.json

# restore original names in LLM response
python main.py de-anonymize -i chatgpt_response.txt -m mapping.json

# blur text in images
python main.py blur-image -i screenshot.png --blur-all

# anonymize custom terms (project names under NDA, etc.)
python main.py anonymize -i confidential.pdf --deny-list "ProjectX,SecretCorp"
```

### Web GUI

```bash
# start the web server
cd pseudo-anonymizer
python -m uvicorn web.app:app --host 0.0.0.0 --port 8000

# open http://localhost:8000
```

The web interface walks you through: Upload -> Configure (deny list, entities, operator) -> Process -> Download results.

## Operators

| Operator | What it does | Example | Reversible |
|----------|--------------|---------|------------|
| `replace` | Consistent pseudonyms | `John Smith` -> `<PERSON_1>` | Yes |
| `mask` | Asterisks | `John Smith` -> `**********` | No |
| `redact` | [REDACTED] marker | `John Smith` -> `[REDACTED]` | No |
| `hash` | SHA256 | `John Smith` -> `a1b2c3...` | No |

## Custom Deny Lists

Beyond standard PII, you can specify terms to anonymize - useful for project codenames, company names, anything under NDA:

```bash
# inline
python main.py anonymize -i doc.pdf --deny-list "ProjectX,SecretCorp"

# from file (one term per line)
python main.py anonymize -i doc.pdf --deny-list deny_terms.txt
```

## Directory Structure

```
pseudo-anonymizer/
├── main.py              # CLI entry point
├── anonymizer.py        # PII detection (Presidio)
├── file_handler.py      # PDF/DOCX/TXT extraction
├── image_anonymizer.py  # OCR + blur
├── pdf_anonymizer.py    # PDF-to-PDF output (PyMuPDF)
├── mapping_manager.py   # Save/load mappings
├── config.py            # Settings
├── web/
│   ├── app.py           # FastAPI backend
│   ├── templates/       # Jinja2 HTML
│   └── static/          # CSS/JS
└── requirements.txt
```

## Workflow Example

```bash
# 1. Anonymize
python main.py anonymize -i confidential.pdf --save-mapping mapping.json -o safe.txt

# 2. Paste safe.txt into ChatGPT, get response

# 3. Save ChatGPT response to file, then restore names
python main.py de-anonymize -i response.txt -m mapping.json -o final.txt
```

## PDF Image Processing

When processing PDFs with embedded images (like screenshots), the tool uses:

1. **3x image scaling** - Upscales images for better OCR accuracy on small text
2. **Image preprocessing** - Enhances contrast and sharpness before OCR
3. **Fuzzy matching** - Uses Levenshtein distance to match OCR text even with typos
   - OCR might read "Mindguord" instead of "Mindguard"
   - Fuzzy matching with 80% similarity threshold catches these variants
4. **Low confidence threshold** - Accepts OCR results down to 15% confidence

### OCR Settings

The following settings are used for image text detection:
- `MIN_CONFIDENCE = 15` - Minimum OCR confidence (0-100)
- `scale_factor = 3` - Image upscaling multiplier
- `contrast = 1.5` - Contrast enhancement
- `sharpness = 2.0` - Sharpness enhancement
- `similarity_threshold = 0.8` - Fuzzy match threshold (80%)

## Processing Log

The web GUI provides a downloadable processing log that includes:
- OCR detection results with confidence scores
- Fuzzy match details (similarity percentages)
- Coordinates of blurred regions
- Potential matches that were considered

## Troubleshooting

**spacy model not found**: `python -m spacy download en_core_web_sm`

**PDF returns empty text**: Probably a scanned PDF (image-based). Use OCR tools instead.

**Tesseract not found**: Install from [here](https://github.com/UB-Mannheim/tesseract/wiki) and add to PATH.

**OCR not detecting text in images**: Try these:
1. Ensure Tesseract is installed and in PATH
2. Check the processing log for detected words
3. Some stylized/artistic fonts may not be recognized by OCR
4. Very small text may not be detected even with scaling

## License

MIT
