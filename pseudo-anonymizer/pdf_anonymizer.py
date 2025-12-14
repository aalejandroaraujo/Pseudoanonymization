"""
PDF Anonymizer Module

Handles PDF-to-PDF anonymization by directly modifying text in PDFs
while preserving the original format and layout.
Also handles embedded images using OCR.
"""

import logging
import io
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    from PIL import Image, ImageFilter
    import pytesseract
    # Configure Tesseract path for Windows
    import os
    if os.name == 'nt':  # Windows
        tesseract_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
        ]
        for path in tesseract_paths:
            if os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                break
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate the Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def fuzzy_match(text: str, term: str, threshold: float = 0.8) -> bool:
    """
    Check if text fuzzy-matches a term using Levenshtein distance.

    Args:
        text: OCR-detected text
        term: Search term to match against
        threshold: Minimum similarity ratio (0.0-1.0) for a match

    Returns:
        True if text is similar enough to term
    """
    text_lower = text.lower()
    term_lower = term.lower()

    # Exact match (only if lengths are similar to avoid false positives)
    if text_lower == term_lower:
        return True

    # Substring match only if the shorter string is at least 60% of the longer
    shorter_len = min(len(text_lower), len(term_lower))
    longer_len = max(len(text_lower), len(term_lower))
    if shorter_len >= 4 and shorter_len / longer_len >= 0.6:
        if term_lower in text_lower or text_lower in term_lower:
            return True

    # For very short strings (less than 4 chars), require exact match
    if len(text_lower) < 4 or len(term_lower) < 4:
        return text_lower == term_lower

    # Fuzzy match using Levenshtein distance
    distance = levenshtein_distance(text_lower, term_lower)
    max_len = max(len(text_lower), len(term_lower))
    similarity = 1 - (distance / max_len)

    return similarity >= threshold


class PDFAnonymizerError(Exception):
    """Custom exception for PDF anonymization errors."""
    pass


def check_pymupdf():
    """Check if PyMuPDF is available."""
    if not PYMUPDF_AVAILABLE:
        raise PDFAnonymizerError(
            "PyMuPDF is required for PDF anonymization. "
            "Install it with: pip install pymupdf"
        )


def blur_image_regions(image: Image.Image, deny_list: List[str], blur_radius: int = 15) -> Tuple[Image.Image, int]:
    """
    Blur text regions in an image that match deny list terms.

    Args:
        image: PIL Image object
        deny_list: List of terms to blur (case-insensitive)
        blur_radius: Gaussian blur radius

    Returns:
        Tuple of (blurred image, number of regions blurred)
    """
    if not OCR_AVAILABLE:
        logger.warning("OCR not available - skipping image text detection")
        return image, 0

    try:
        # Get OCR data with bounding boxes
        ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

        regions_blurred = 0
        n_boxes = len(ocr_data['text'])
        deny_list_lower = [term.lower() for term in deny_list]

        logger.debug(f"OCR detected {n_boxes} text boxes in image")

        for i in range(n_boxes):
            text = ocr_data['text'][i].strip()
            conf = float(ocr_data['conf'][i])

            if not text or conf < 30:
                continue

            text_lower = text.lower()

            # Check if text matches any deny list term
            should_blur = any(
                term in text_lower or text_lower in term
                for term in deny_list_lower
            )

            if should_blur:
                x, y = ocr_data['left'][i], ocr_data['top'][i]
                w, h = ocr_data['width'][i], ocr_data['height'][i]

                logger.info(f"Blurring text '{text}' at ({x}, {y}) in image")

                # Add padding
                padding = 5
                x1 = max(0, x - padding)
                y1 = max(0, y - padding)
                x2 = min(image.width, x + w + padding)
                y2 = min(image.height, y + h + padding)

                # Crop, blur, and paste back
                region = image.crop((x1, y1, x2, y2))
                blurred = region.filter(ImageFilter.GaussianBlur(radius=blur_radius))
                image.paste(blurred, (x1, y1))
                regions_blurred += 1

        logger.info(f"Blurred {regions_blurred} text regions in image")
        return image, regions_blurred

    except Exception as e:
        logger.error(f"Error during image OCR: {e}")
        return image, 0


def anonymize_pdf(
    input_path: str,
    output_path: str,
    replacements: Dict[str, str],
    redact_style: str = "replace",
    process_images: bool = True
) -> Tuple[str, int]:
    """
    Anonymize a PDF by replacing or redacting specified text.
    Also processes embedded images using OCR if enabled.

    Args:
        input_path: Path to the input PDF file.
        output_path: Path to save the anonymized PDF.
        replacements: Dictionary mapping original text to replacement text.
        redact_style: How to handle redactions - "replace" for text replacement,
                     "blackout" for black rectangles over text.
        process_images: If True, also process embedded images with OCR.

    Returns:
        Tuple of (output_path, number of replacements made).

    Raises:
        PDFAnonymizerError: If anonymization fails.
    """
    check_pymupdf()

    logger.info(f"Starting PDF anonymization: {input_path}")
    logger.debug(f"Replacements to make: {list(replacements.keys())}")
    logger.debug(f"Redact style: {redact_style}, Process images: {process_images}")

    try:
        doc = fitz.open(input_path)
        total_replacements = 0
        total_image_blurs = 0

        logger.info(f"PDF has {doc.page_count} pages")

        for page_num, page in enumerate(doc):
            logger.debug(f"Processing page {page_num + 1}/{doc.page_count}")
            page_replacements = 0

            # Process text replacements
            for original, replacement in replacements.items():
                # Search for all instances of the text
                text_instances = page.search_for(original)

                if text_instances:
                    logger.debug(f"Found {len(text_instances)} instances of '{original}' on page {page_num + 1}")

                for rect in text_instances:
                    total_replacements += 1
                    page_replacements += 1

                    if redact_style == "blackout":
                        # Add a black redaction annotation
                        page.add_redact_annot(rect, fill=(0, 0, 0))
                    else:
                        # Add redaction with replacement text
                        page.add_redact_annot(
                            rect,
                            text=replacement,
                            fill=(1, 1, 1),  # White background
                            text_color=(0, 0, 0),  # Black text
                        )

            # Apply all text redactions on this page
            page.apply_redactions()

            logger.debug(f"Page {page_num + 1}: {page_replacements} text replacements")

            # Process embedded images if enabled
            if process_images and OCR_AVAILABLE:
                image_list = page.get_images(full=True)
                logger.debug(f"Page {page_num + 1} has {len(image_list)} embedded images")

                for img_index, img_info in enumerate(image_list):
                    xref = img_info[0]

                    try:
                        # Extract image
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]

                        logger.debug(f"Processing image {img_index + 1} (xref={xref}, format={image_ext})")

                        # Convert to PIL Image
                        pil_image = Image.open(io.BytesIO(image_bytes))
                        if pil_image.mode in ('RGBA', 'P'):
                            pil_image = pil_image.convert('RGB')

                        # Blur matching text in image
                        deny_list = list(replacements.keys())
                        blurred_image, blur_count = blur_image_regions(pil_image, deny_list)
                        total_image_blurs += blur_count

                        if blur_count > 0:
                            # Convert back to bytes and replace in PDF
                            img_buffer = io.BytesIO()
                            blurred_image.save(img_buffer, format='PNG')
                            img_buffer.seek(0)

                            # Replace the image in the PDF
                            page.replace_image(xref, stream=img_buffer.getvalue())
                            logger.info(f"Replaced image {img_index + 1} on page {page_num + 1} with {blur_count} blurred regions")

                    except Exception as img_error:
                        logger.warning(f"Could not process image {img_index + 1} on page {page_num + 1}: {img_error}")

        # Save the modified PDF
        doc.save(output_path, garbage=4, deflate=True)
        doc.close()

        logger.info(f"PDF anonymization complete:")
        logger.info(f"  - Text replacements: {total_replacements}")
        logger.info(f"  - Image regions blurred: {total_image_blurs}")
        logger.info(f"  - Output saved to: {output_path}")

        return output_path, total_replacements + total_image_blurs

    except Exception as e:
        logger.error(f"PDF anonymization failed: {e}", exc_info=True)
        raise PDFAnonymizerError(f"Failed to anonymize PDF: {e}")


def anonymize_pdf_with_mapping(
    input_path: str,
    output_path: str,
    mapping: Dict[str, str],
    operator: str = "replace",
    manual_blur_regions: Optional[List[Dict]] = None
) -> Tuple[str, int, List[str]]:
    """
    Anonymize a PDF using an anonymization mapping.

    Args:
        input_path: Path to the input PDF file.
        output_path: Path to save the anonymized PDF.
        mapping: Dictionary mapping original values to anonymized values.
        operator: Anonymization operator used (affects redact style).
        manual_blur_regions: Optional list of manually specified regions to blur.
            Each region dict has: page (1-indexed), image_index, x, y, width, height
            (coordinates are in original image pixels).

    Returns:
        Tuple of (output_path, number of replacements made, list of log messages).
    """
    logs = []

    # Initialize manual blur regions
    if manual_blur_regions is None:
        manual_blur_regions = []

    # Determine redact style based on operator
    redact_style = "blackout" if operator == "mask" else "replace"

    check_pymupdf()

    logs.append(f"Opening PDF: {Path(input_path).name}")
    logs.append(f"Terms to find: {list(mapping.keys())}")
    logs.append(f"Redact style: {redact_style}")
    if manual_blur_regions:
        logs.append(f"Manual blur regions: {len(manual_blur_regions)} region(s) to apply")

    try:
        doc = fitz.open(input_path)
        total_replacements = 0
        total_image_blurs = 0
        total_manual_blurs = 0

        logs.append(f"PDF has {doc.page_count} page(s)")

        for page_num, page in enumerate(doc):
            page_replacements = 0
            logs.append(f"Processing page {page_num + 1}/{doc.page_count}...")

            # Process text replacements
            for original, replacement in mapping.items():
                text_instances = page.search_for(original)

                if text_instances:
                    logs.append(f"  Found {len(text_instances)} instance(s) of '{original}' on page {page_num + 1}")

                for rect in text_instances:
                    total_replacements += 1
                    page_replacements += 1

                    if redact_style == "blackout":
                        page.add_redact_annot(rect, fill=(0, 0, 0))
                    else:
                        page.add_redact_annot(
                            rect,
                            text=replacement,
                            fill=(1, 1, 1),
                            text_color=(0, 0, 0),
                        )

            page.apply_redactions()

            # Process embedded images if OCR is available
            if OCR_AVAILABLE:
                image_list = page.get_images(full=True)
                if image_list:
                    logs.append(f"  Page {page_num + 1} has {len(image_list)} embedded image(s)")

                for img_index, img_info in enumerate(image_list):
                    xref = img_info[0]

                    try:
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]

                        logs.append(f"    Processing image {img_index + 1} ({image_ext} format)...")

                        pil_image = Image.open(io.BytesIO(image_bytes))
                        if pil_image.mode in ('RGBA', 'P'):
                            pil_image = pil_image.convert('RGB')

                        # Apply manual blur regions first (page is 1-indexed in regions)
                        manual_regions_for_image = [
                            r for r in manual_blur_regions
                            if r.get('page') == page_num + 1 and r.get('image_index') == img_index
                        ]
                        manual_blurs_applied = 0
                        if manual_regions_for_image:
                            logs.append(f"    Applying {len(manual_regions_for_image)} manual blur region(s)...")
                            for region in manual_regions_for_image:
                                try:
                                    x = int(region['x'])
                                    y = int(region['y'])
                                    w = int(region['width'])
                                    h = int(region['height'])

                                    # Ensure coordinates are within image bounds
                                    x1 = max(0, x)
                                    y1 = max(0, y)
                                    x2 = min(pil_image.width, x + w)
                                    y2 = min(pil_image.height, y + h)

                                    if x2 > x1 and y2 > y1:
                                        region_img = pil_image.crop((x1, y1, x2, y2))
                                        blurred = region_img.filter(ImageFilter.GaussianBlur(radius=15))
                                        pil_image.paste(blurred, (x1, y1))
                                        manual_blurs_applied += 1
                                        logs.append(f"      Manual blur applied at ({x1}, {y1}, {x2-x1}x{y2-y1})")
                                except Exception as region_error:
                                    logs.append(f"      Warning: Could not apply manual region: {region_error}")

                            total_manual_blurs += manual_blurs_applied

                            # If we applied manual blurs but no OCR blurs, we still need to save the image
                            # The OCR section will handle saving if there are OCR blurs too

                        # Run OCR - ensure tesseract path is set for Windows
                        import os as os_module
                        if os_module.name == 'nt':
                            pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

                        # Scale image 3x for better OCR accuracy (helps with small/stylized text)
                        scale_factor = 3
                        scaled_image = pil_image.resize(
                            (pil_image.width * scale_factor, pil_image.height * scale_factor),
                            Image.LANCZOS
                        )

                        # Apply image preprocessing to improve OCR on stylized text
                        from PIL import ImageEnhance, ImageOps

                        # Increase contrast
                        contrast_enhancer = ImageEnhance.Contrast(scaled_image)
                        scaled_image = contrast_enhancer.enhance(1.5)

                        # Increase sharpness
                        sharpness_enhancer = ImageEnhance.Sharpness(scaled_image)
                        scaled_image = sharpness_enhancer.enhance(2.0)

                        logs.append(f"    Scaled image from {pil_image.size} to {scaled_image.size} (3x) with contrast/sharpness enhancement")

                        # Multi-pass OCR: try different preprocessing to catch all text
                        all_ocr_results = []
                        import numpy as np

                        # Pass 1: Standard enhanced image
                        ocr_data = pytesseract.image_to_data(scaled_image, output_type=pytesseract.Output.DICT)
                        all_ocr_results.append(('standard', ocr_data))

                        # Pass 2: Inverted image (white text on dark becomes dark on white)
                        inverted_image = ImageOps.invert(scaled_image.convert('RGB'))
                        ocr_data_inv = pytesseract.image_to_data(inverted_image, output_type=pytesseract.Output.DICT)
                        all_ocr_results.append(('inverted', ocr_data_inv))

                        # Pass 3: High contrast grayscale
                        gray_image = scaled_image.convert('L')
                        contrast_enhancer2 = ImageEnhance.Contrast(gray_image)
                        high_contrast = contrast_enhancer2.enhance(2.5)
                        ocr_data_gray = pytesseract.image_to_data(high_contrast, output_type=pytesseract.Output.DICT)
                        all_ocr_results.append(('grayscale', ocr_data_gray))

                        # Pass 4: Simple binary threshold (black/white) - using PIL
                        # Calculate threshold using mean pixel value (simple Otsu approximation)
                        gray_np = np.array(gray_image)
                        threshold = int(np.mean(gray_np))
                        binary_image = gray_image.point(lambda x: 255 if x > threshold else 0, '1').convert('L')
                        ocr_data_binary = pytesseract.image_to_data(binary_image, output_type=pytesseract.Output.DICT)
                        all_ocr_results.append(('binary', ocr_data_binary))

                        # Pass 5: Inverted binary
                        binary_inv_image = ImageOps.invert(binary_image)
                        ocr_data_binary_inv = pytesseract.image_to_data(binary_inv_image, output_type=pytesseract.Output.DICT)
                        all_ocr_results.append(('binary_inv', ocr_data_binary_inv))

                        # Pass 6: High threshold binary (catches lighter text)
                        high_threshold = min(255, int(threshold * 1.3))
                        binary_high = gray_image.point(lambda x: 255 if x > high_threshold else 0, '1').convert('L')
                        ocr_data_binary_high = pytesseract.image_to_data(binary_high, output_type=pytesseract.Output.DICT)
                        all_ocr_results.append(('binary_high', ocr_data_binary_high))

                        # Pass 7: Low threshold binary (catches darker text)
                        low_threshold = max(0, int(threshold * 0.7))
                        binary_low = gray_image.point(lambda x: 255 if x > low_threshold else 0, '1').convert('L')
                        ocr_data_binary_low = pytesseract.image_to_data(binary_low, output_type=pytesseract.Output.DICT)
                        all_ocr_results.append(('binary_low', ocr_data_binary_low))

                        # Merge all OCR results, tracking unique detections by position
                        merged_ocr = {'text': [], 'conf': [], 'left': [], 'top': [], 'width': [], 'height': []}
                        seen_positions = set()

                        for pass_name, ocr_result in all_ocr_results:
                            n = len(ocr_result['text'])
                            for i in range(n):
                                text = ocr_result['text'][i].strip()
                                if not text:
                                    continue
                                # Create position key (rounded to avoid near-duplicates)
                                pos_key = (ocr_result['left'][i] // 20, ocr_result['top'][i] // 20, text.lower())
                                if pos_key not in seen_positions:
                                    seen_positions.add(pos_key)
                                    merged_ocr['text'].append(text)
                                    merged_ocr['conf'].append(ocr_result['conf'][i])
                                    merged_ocr['left'].append(ocr_result['left'][i])
                                    merged_ocr['top'].append(ocr_result['top'][i])
                                    merged_ocr['width'].append(ocr_result['width'][i])
                                    merged_ocr['height'].append(ocr_result['height'][i])

                        ocr_data = merged_ocr
                        num_passes = len(all_ocr_results)
                        logs.append(f"    Multi-pass OCR: {num_passes} passes (standard, inverted, grayscale, binary, binary_inv, binary_high, binary_low)")

                        n_boxes = len(ocr_data['text'])
                        deny_list_lower = [term.lower() for term in mapping.keys()]

                        logs.append(f"    OCR detected {n_boxes} unique text boxes (merged from {num_passes} passes)")

                        # Lower confidence threshold to 15% to catch more stylized text
                        MIN_CONFIDENCE = 15

                        # Log all detected text for debugging (show more detail)
                        detected_words = []
                        for i in range(n_boxes):
                            text = ocr_data['text'][i].strip()
                            conf = float(ocr_data['conf'][i])
                            if text and conf >= MIN_CONFIDENCE:
                                detected_words.append(f"{text}({conf:.0f}%)")
                        logs.append(f"    Detected words (conf>={MIN_CONFIDENCE}%): {detected_words[:40]}{'...' if len(detected_words) > 40 else ''}")

                        # Also log any potential matches even at very low confidence for debugging
                        potential_matches = []
                        for i in range(n_boxes):
                            text = ocr_data['text'][i].strip()
                            conf = float(ocr_data['conf'][i])
                            if text and len(text) >= 4:
                                for term in mapping.keys():
                                    if fuzzy_match(text, term, threshold=0.7):
                                        potential_matches.append(f"{text}({conf:.0f}%)")
                                        break
                        if potential_matches:
                            logs.append(f"    Potential matches (any conf): {potential_matches}")

                        regions_blurred = 0
                        for i in range(n_boxes):
                            text = ocr_data['text'][i].strip()
                            conf = float(ocr_data['conf'][i])

                            if not text or conf < MIN_CONFIDENCE:
                                continue

                            # Check for fuzzy match against all deny list terms
                            matched_term = None
                            for term in mapping.keys():
                                if fuzzy_match(text, term, threshold=0.8):
                                    matched_term = term
                                    break

                            if matched_term:
                                # Coordinates are from scaled image, convert back to original
                                x = ocr_data['left'][i] // scale_factor
                                y = ocr_data['top'][i] // scale_factor
                                w = ocr_data['width'][i] // scale_factor
                                h = ocr_data['height'][i] // scale_factor

                                # Calculate similarity for logging
                                distance = levenshtein_distance(text.lower(), matched_term.lower())
                                similarity = 1 - (distance / max(len(text), len(matched_term)))
                                logs.append(f"    MATCH: '{text}' ~ '{matched_term}' (similarity={similarity:.0%}, conf={conf:.0f}%) at ({x}, {y}, {w}x{h})")

                                padding = 5
                                x1 = max(0, x - padding)
                                y1 = max(0, y - padding)
                                x2 = min(pil_image.width, x + w + padding)
                                y2 = min(pil_image.height, y + h + padding)

                                region = pil_image.crop((x1, y1, x2, y2))
                                blurred = region.filter(ImageFilter.GaussianBlur(radius=15))
                                pil_image.paste(blurred, (x1, y1))
                                regions_blurred += 1

                        total_image_blurs += regions_blurred

                        # Save image if either OCR or manual blurs were applied
                        total_blurs_for_image = regions_blurred + manual_blurs_applied
                        if total_blurs_for_image > 0:
                            img_buffer = io.BytesIO()
                            pil_image.save(img_buffer, format='PNG')
                            img_buffer.seek(0)
                            page.replace_image(xref, stream=img_buffer.getvalue())
                            blur_details = []
                            if regions_blurred > 0:
                                blur_details.append(f"{regions_blurred} OCR")
                            if manual_blurs_applied > 0:
                                blur_details.append(f"{manual_blurs_applied} manual")
                            logs.append(f"    Replaced image with {total_blurs_for_image} blurred region(s) ({', '.join(blur_details)})")
                        else:
                            logs.append(f"    No matching text found in image")

                    except Exception as img_error:
                        logs.append(f"    Warning: Could not process image: {img_error}")
            else:
                logs.append(f"  OCR not available - skipping image processing")

        doc.save(output_path, garbage=4, deflate=True)
        doc.close()

        logs.append(f"PDF anonymization complete:")
        logs.append(f"  - Text replacements: {total_replacements}")
        logs.append(f"  - Image regions blurred (OCR): {total_image_blurs}")
        logs.append(f"  - Image regions blurred (manual): {total_manual_blurs}")
        logs.append(f"  - Output saved to: {Path(output_path).name}")

        return output_path, total_replacements + total_image_blurs + total_manual_blurs, logs

    except Exception as e:
        logs.append(f"ERROR: {e}")
        raise PDFAnonymizerError(f"Failed to anonymize PDF: {e}")


def extract_text_from_pdf(input_path: str) -> str:
    """
    Extract text from a PDF file.

    Args:
        input_path: Path to the PDF file.

    Returns:
        Extracted text content.
    """
    check_pymupdf()

    try:
        doc = fitz.open(input_path)
        text_parts = []

        for page in doc:
            text_parts.append(page.get_text())

        doc.close()
        return "\n".join(text_parts)

    except Exception as e:
        raise PDFAnonymizerError(f"Failed to extract text from PDF: {e}")


def get_pdf_info(input_path: str) -> Dict:
    """
    Get information about a PDF file.

    Args:
        input_path: Path to the PDF file.

    Returns:
        Dictionary with PDF metadata.
    """
    check_pymupdf()

    try:
        doc = fitz.open(input_path)
        info = {
            "page_count": doc.page_count,
            "metadata": doc.metadata,
            "is_encrypted": doc.is_encrypted,
        }
        doc.close()
        return info

    except Exception as e:
        raise PDFAnonymizerError(f"Failed to get PDF info: {e}")
