"""
Pseudo-Anonymizer CLI

A command-line tool for anonymizing sensitive documents before sharing with LLMs.
Supports PDF, DOCX, and TXT files with configurable PII detection and anonymization.
Also supports image anonymization with text blur.
"""

import sys
import logging
from pathlib import Path
from typing import Optional

import click

from config import config, setup_logging, OPERATOR_CONFIGS, ENTITY_DESCRIPTIONS, parse_deny_list
from file_handler import (
    extract_text_from_file,
    write_output_file,
    get_default_output_path,
    FileHandlerError,
)
from anonymizer import DocumentAnonymizer, AnonymizerError
from mapping_manager import MappingManager, MappingManagerError

logger = logging.getLogger(__name__)


def print_banner():
    """Print application banner."""
    click.echo(click.style("""
+===========================================================+
|           Pseudo-Anonymizer - Document PII Remover        |
|         Safely share documents with LLMs like ChatGPT     |
+===========================================================+
""", fg='cyan'))


@click.group()
@click.version_option(version='1.0.0', prog_name='pseudo-anonymizer')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
@click.option('--quiet', '-q', is_flag=True, help='Suppress non-essential output')
@click.pass_context
def cli(ctx, verbose: bool, quiet: bool):
    """
    Pseudo-Anonymizer: Anonymize sensitive documents for LLM sharing.

    Use 'anonymize' to process documents and 'de-anonymize' to restore them.
    """
    ctx.ensure_object(dict)

    # Set up logging based on flags
    if verbose:
        setup_logging('DEBUG')
    elif quiet:
        setup_logging('WARNING')
    else:
        setup_logging('INFO')

    ctx.obj['verbose'] = verbose
    ctx.obj['quiet'] = quiet


@cli.command()
@click.option(
    '--input', '-i',
    'input_file',
    required=True,
    type=click.Path(exists=True),
    help='Input file path (PDF, DOCX, or TXT)'
)
@click.option(
    '--output', '-o',
    'output_file',
    type=click.Path(),
    help='Output file path (defaults to input_anon.txt)'
)
@click.option(
    '--operator',
    type=click.Choice(['mask', 'redact', 'replace', 'hash'], case_sensitive=False),
    default='replace',
    help='Anonymization method (default: replace)'
)
@click.option(
    '--entities',
    default='PERSON,EMAIL_ADDRESS,PHONE_NUMBER',
    help='Comma-separated list of entity types to detect'
)
@click.option(
    '--save-mapping',
    'mapping_file',
    type=click.Path(),
    help='Path to save anonymization mapping JSON'
)
@click.option(
    '--api-key',
    help='Claude or OpenAI API key (for future LLM enhancement)'
)
@click.option(
    '--deny-list', '-d',
    'deny_list',
    help='Custom terms to anonymize: comma-separated (e.g., "ProjectX,SecretCorp") or path to file'
)
@click.pass_context
def anonymize(
    ctx,
    input_file: str,
    output_file: Optional[str],
    operator: str,
    entities: str,
    mapping_file: Optional[str],
    api_key: Optional[str],
    deny_list: Optional[str],
):
    """
    Anonymize a document by detecting and replacing PII.

    Examples:

        python main.py anonymize -i document.pdf

        python main.py anonymize -i report.docx --operator mask

        python main.py anonymize -i data.txt --save-mapping mapping.json
    """
    quiet = ctx.obj.get('quiet', False)

    if not quiet:
        print_banner()

    try:
        # Store API key if provided
        if api_key:
            config.set_api_key('claude', api_key)
            logger.debug("API key stored for future use")

        # Parse and validate entities
        entity_list = [e.strip().upper() for e in entities.split(',')]
        validated_entities = config.validate_entities(entity_list)

        # Parse deny list if provided
        deny_list_terms = []
        if deny_list:
            deny_list_terms = parse_deny_list(deny_list)

        if not quiet:
            click.echo(click.style(f"[FILE] Input file: ", fg='white') + click.style(input_file, fg='yellow'))
            click.echo(click.style(f"[SEARCH] Detecting: ", fg='white') + click.style(', '.join(validated_entities), fg='yellow'))
            click.echo(click.style(f"[GEAR]  Operator: ", fg='white') + click.style(operator, fg='yellow'))
            if deny_list_terms:
                click.echo(click.style(f"[DENY]  Custom terms: ", fg='white') + click.style(', '.join(deny_list_terms), fg='yellow'))
            click.echo()

        # Extract text from file
        click.echo(click.style("Extracting text...", fg='blue'))
        text = extract_text_from_file(input_file)
        logger.info(f"Extracted {len(text)} characters from {input_file}")

        if not text.strip():
            click.echo(click.style("[WARN]  Warning: No text extracted from file", fg='yellow'))
            return

        # Initialize anonymizer with deny list
        click.echo(click.style("Analyzing for PII...", fg='blue'))
        anonymizer = DocumentAnonymizer(
            entities=validated_entities,
            deny_list=deny_list_terms if deny_list_terms else None
        )

        # Get entity summary first
        summary = anonymizer.get_entity_summary(text)
        total_entities = sum(summary.values())

        if total_entities == 0:
            click.echo(click.style("[OK] No PII detected in document", fg='green'))

            # Still write output if requested
            if output_file:
                write_output_file(text, output_file)
                click.echo(click.style(f"[FOLDER] Output saved to: {output_file}", fg='green'))
            return

        # Show detection summary
        if not quiet:
            click.echo(click.style("\n[CHART] Detection Summary:", fg='cyan', bold=True))
            for entity_type, count in sorted(summary.items()):
                desc = ENTITY_DESCRIPTIONS.get(entity_type, entity_type)
                click.echo(f"   * {entity_type}: {count} ({desc})")
            click.echo()

        # Perform anonymization
        click.echo(click.style("Anonymizing...", fg='blue'))
        anonymized_text, mapping = anonymizer.anonymize_text(text, operator)

        # Determine output path
        if not output_file:
            output_file = get_default_output_path(input_file)

        # Write output file
        write_output_file(anonymized_text, output_file)

        # Save mapping if requested
        if mapping_file and mapping:
            manager = MappingManager()
            manager.add_mappings(mapping)
            manager.save(mapping_file)
            click.echo(click.style(f"[KEY] Mapping saved to: {mapping_file}", fg='green'))

        # Print success summary
        click.echo()
        click.echo(click.style("=" * 50, fg='green'))
        click.echo(click.style(f"[OK] Anonymized {total_entities} entities", fg='green', bold=True))
        click.echo(click.style(f"[FOLDER] Output saved to: {output_file}", fg='green'))
        click.echo(click.style("=" * 50, fg='green'))

    except FileHandlerError as e:
        click.echo(click.style(f"[X] File error: {e}", fg='red'), err=True)
        sys.exit(1)
    except AnonymizerError as e:
        click.echo(click.style(f"[X] Anonymization error: {e}", fg='red'), err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(click.style(f"[X] Configuration error: {e}", fg='red'), err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error")
        click.echo(click.style(f"[X] Unexpected error: {e}", fg='red'), err=True)
        sys.exit(1)


@cli.command('de-anonymize')
@click.option(
    '--input', '-i',
    'input_file',
    required=True,
    type=click.Path(exists=True),
    help='Anonymized file to restore'
)
@click.option(
    '--mapping', '-m',
    'mapping_file',
    required=True,
    type=click.Path(exists=True),
    help='Path to the anonymization mapping JSON'
)
@click.option(
    '--output', '-o',
    'output_file',
    type=click.Path(),
    help='Output file path (defaults to input_restored.txt)'
)
@click.pass_context
def de_anonymize(
    ctx,
    input_file: str,
    mapping_file: str,
    output_file: Optional[str],
):
    """
    Restore an anonymized document using a mapping file.

    This reverses the anonymization process using the saved mapping.

    Examples:

        python main.py de-anonymize -i document_anon.txt -m mapping.json

        python main.py de-anonymize -i document_anon.txt -m mapping.json -o restored.txt
    """
    quiet = ctx.obj.get('quiet', False)

    if not quiet:
        print_banner()

    try:
        # Load the mapping
        click.echo(click.style("Loading mapping...", fg='blue'))
        manager = MappingManager(mapping_file)

        if len(manager) == 0:
            click.echo(click.style("[WARN]  Warning: Mapping file is empty", fg='yellow'))
            return

        click.echo(click.style(f"[FOLDER] Loaded {len(manager)} mapping entries", fg='green'))

        # Read the anonymized file
        click.echo(click.style("Reading anonymized file...", fg='blue'))
        text = extract_text_from_file(input_file)

        # De-anonymize
        click.echo(click.style("Restoring original values...", fg='blue'))
        restored_text = manager.de_anonymize_text(text)

        # Determine output path
        if not output_file:
            path = Path(input_file)
            output_file = str(path.parent / f"{path.stem}_restored.txt")

        # Write output
        write_output_file(restored_text, output_file)

        click.echo()
        click.echo(click.style("=" * 50, fg='green'))
        click.echo(click.style("[OK] Document restored successfully", fg='green', bold=True))
        click.echo(click.style(f"[FOLDER] Output saved to: {output_file}", fg='green'))
        click.echo(click.style("=" * 50, fg='green'))

    except FileHandlerError as e:
        click.echo(click.style(f"[X] File error: {e}", fg='red'), err=True)
        sys.exit(1)
    except MappingManagerError as e:
        click.echo(click.style(f"[X] Mapping error: {e}", fg='red'), err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error")
        click.echo(click.style(f"[X] Unexpected error: {e}", fg='red'), err=True)
        sys.exit(1)


@cli.command()
def entities():
    """
    List all available entity types for detection.
    """
    print_banner()

    click.echo(click.style("Available Entity Types:", fg='cyan', bold=True))
    click.echo()

    default_entities = set(config.DEFAULT_ENTITIES)

    for entity in config.ALL_ENTITIES:
        desc = ENTITY_DESCRIPTIONS.get(entity, '')
        is_default = '(default)' if entity in default_entities else ''

        click.echo(
            click.style(f"  * {entity}", fg='yellow') +
            click.style(f" - {desc}", fg='white') +
            click.style(f" {is_default}", fg='green')
        )

    click.echo()
    click.echo("Use --entities to specify which types to detect:")
    click.echo(click.style("  python main.py anonymize -i file.pdf --entities PERSON,EMAIL_ADDRESS,PHONE_NUMBER", fg='cyan'))


@cli.command()
def operators():
    """
    List all available anonymization operators.
    """
    print_banner()

    click.echo(click.style("Available Operators:", fg='cyan', bold=True))
    click.echo()

    for op, info in OPERATOR_CONFIGS.items():
        reversible = "[+] Reversible" if info['reversible'] else "[-] Not reversible"
        click.echo(click.style(f"  {op}", fg='yellow', bold=True))
        click.echo(click.style(f"    {info['description']}", fg='white'))
        click.echo(click.style(f"    Example: {info['example']}", fg='cyan'))
        click.echo(click.style(f"    {reversible}", fg='green' if info['reversible'] else 'red'))
        click.echo()

    click.echo("Use --operator to specify which method to use:")
    click.echo(click.style("  python main.py anonymize -i file.pdf --operator replace", fg='cyan'))


@cli.command('blur-image')
@click.option(
    '--input', '-i',
    'input_file',
    required=True,
    type=click.Path(exists=True),
    help='Input image path (PNG, JPG, BMP, TIFF, GIF)'
)
@click.option(
    '--output', '-o',
    'output_file',
    type=click.Path(),
    help='Output image path (defaults to input_anon.ext)'
)
@click.option(
    '--deny-list', '-d',
    'deny_list',
    help='Custom terms to blur: comma-separated (e.g., "ProjectX,SecretCorp") or path to file'
)
@click.option(
    '--blur-all', '-a',
    is_flag=True,
    help='Blur all detected text (ignores deny list)'
)
@click.option(
    '--blur-radius', '-r',
    default=15,
    type=int,
    help='Gaussian blur radius (default: 15, higher = more blur)'
)
@click.option(
    '--preview', '-p',
    is_flag=True,
    help='Preview detected text without anonymizing'
)
@click.pass_context
def blur_image(
    ctx,
    input_file: str,
    output_file: Optional[str],
    deny_list: Optional[str],
    blur_all: bool,
    blur_radius: int,
    preview: bool,
):
    """
    Anonymize an image by blurring detected text.

    Uses OCR to detect text regions, then applies Gaussian blur to anonymize.
    Specify terms with --deny-list or use --blur-all to blur everything.

    Examples:

        python main.py blur-image -i screenshot.png --blur-all

        python main.py blur-image -i document.jpg --deny-list "ProjectX,SecretCorp"

        python main.py blur-image -i photo.png --preview
    """
    quiet = ctx.obj.get('quiet', False)

    if not quiet:
        print_banner()

    try:
        # Import here to avoid dependency issues if not using image features
        try:
            from image_anonymizer import ImageAnonymizer, ImageAnonymizerError
        except ImportError as e:
            click.echo(click.style(
                "[X] Image anonymization requires additional dependencies.\n"
                "    Install with: pip install Pillow pytesseract\n"
                "    Also install Tesseract OCR: https://github.com/tesseract-ocr/tesseract",
                fg='red'
            ), err=True)
            sys.exit(1)

        # Parse deny list if provided
        deny_list_terms = []
        if deny_list:
            deny_list_terms = parse_deny_list(deny_list)

        if not quiet:
            click.echo(click.style(f"[IMAGE] Input: ", fg='white') + click.style(input_file, fg='yellow'))
            if deny_list_terms:
                click.echo(click.style(f"[DENY]  Terms to blur: ", fg='white') + click.style(', '.join(deny_list_terms), fg='yellow'))
            elif blur_all:
                click.echo(click.style(f"[BLUR]  Mode: ", fg='white') + click.style("Blur ALL detected text", fg='yellow'))
            click.echo(click.style(f"[GEAR]  Blur radius: ", fg='white') + click.style(str(blur_radius), fg='yellow'))
            click.echo()

        # Initialize anonymizer
        click.echo(click.style("Initializing OCR engine...", fg='blue'))
        anonymizer = ImageAnonymizer(
            blur_radius=blur_radius,
            deny_list=deny_list_terms if deny_list_terms else None
        )

        # Preview mode - just show detected text
        if preview:
            click.echo(click.style("Detecting text in image...", fg='blue'))
            text_regions = anonymizer.get_text_preview(input_file)

            if not text_regions:
                click.echo(click.style("[OK] No text detected in image", fg='green'))
                return

            click.echo(click.style(f"\n[CHART] Detected {len(text_regions)} text regions:", fg='cyan', bold=True))
            for i, region in enumerate(text_regions, 1):
                match_indicator = " [MATCH]" if region['matches_deny_list'] else ""
                click.echo(
                    click.style(f"  {i}. ", fg='white') +
                    click.style(f"'{region['text']}'", fg='yellow') +
                    click.style(f" at {region['position']}", fg='white') +
                    click.style(f" (conf: {region['confidence']:.1f}%)", fg='cyan') +
                    click.style(match_indicator, fg='red', bold=True)
                )
            return

        # Check if we have something to blur
        if not blur_all and not deny_list_terms:
            click.echo(click.style(
                "[WARN] No terms specified to blur. Use --deny-list or --blur-all",
                fg='yellow'
            ))
            return

        # Perform anonymization
        click.echo(click.style("Detecting and blurring text...", fg='blue'))
        output_path, summary = anonymizer.anonymize_image(
            input_file,
            output_file,
            blur_all_text=blur_all
        )

        # Print success summary
        click.echo()
        click.echo(click.style("=" * 50, fg='green'))
        click.echo(click.style(
            f"[OK] Blurred {summary['regions_blurred']} of {summary['total_text_regions']} text regions",
            fg='green', bold=True
        ))
        click.echo(click.style(f"[IMAGE] Output saved to: {output_path}", fg='green'))
        click.echo(click.style("=" * 50, fg='green'))

    except Exception as e:
        if 'ImageAnonymizerError' in str(type(e)):
            click.echo(click.style(f"[X] Image error: {e}", fg='red'), err=True)
        else:
            logger.exception("Unexpected error")
            click.echo(click.style(f"[X] Unexpected error: {e}", fg='red'), err=True)
        sys.exit(1)


if __name__ == '__main__':
    cli()
