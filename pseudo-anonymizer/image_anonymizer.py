"""
Image Anonymizer Module

Detects and blurs sensitive text in images using OCR.
Supports standalone images and images extracted from PDFs/DOCX files.
"""

import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

try:
    from PIL import Image, ImageFilter, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_FORMATS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.gif'}


class ImageAnonymizerError(Exception):
    """Custom exception for image anonymization errors."""
    pass


@dataclass
class TextRegion:
    """Represents a detected text region in an image."""
    text: str
    x: int
    y: int
    width: int
    height: int
    confidence: float


class ImageAnonymizer:
    """
    Detects and anonymizes text in images using OCR and blur.

    Uses pytesseract for OCR text detection and Pillow for image processing.
    Applies Gaussian blur to regions containing sensitive text.
    """

    def __init__(
        self,
        blur_radius: int = 15,
        min_confidence: float = 30.0,
        deny_list: Optional[List[str]] = None,
        tesseract_cmd: Optional[str] = None
    ):
        """
        Initialize the image anonymizer.

        Args:
            blur_radius: Radius for Gaussian blur (higher = more blur)
            min_confidence: Minimum OCR confidence to consider a detection (0-100)
            deny_list: List of specific terms to blur (if None, blur all detected text)
            tesseract_cmd: Path to tesseract executable (auto-detected if None)
        """
        if not PIL_AVAILABLE:
            raise ImageAnonymizerError(
                "Pillow is required for image anonymization. "
                "Install with: pip install Pillow"
            )

        if not TESSERACT_AVAILABLE:
            raise ImageAnonymizerError(
                "pytesseract is required for image anonymization. "
                "Install with: pip install pytesseract\n"
                "Also install Tesseract OCR: https://github.com/tesseract-ocr/tesseract"
            )

        self.blur_radius = blur_radius
        self.min_confidence = min_confidence
        self.deny_list = [term.lower() for term in (deny_list or [])]

        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

        # Test tesseract availability
        try:
            pytesseract.get_tesseract_version()
            logger.info("Tesseract OCR initialized successfully")
        except Exception as e:
            raise ImageAnonymizerError(
                f"Tesseract OCR not found or not working: {e}\n"
                "Install Tesseract: https://github.com/tesseract-ocr/tesseract"
            )

    def detect_text_regions(self, image: Image.Image) -> List[TextRegion]:
        """
        Detect text regions in an image using OCR.

        Args:
            image: PIL Image object

        Returns:
            List of TextRegion objects with bounding boxes
        """
        # Get detailed OCR data with bounding boxes
        ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

        regions = []
        n_boxes = len(ocr_data['text'])

        for i in range(n_boxes):
            text = ocr_data['text'][i].strip()
            conf = float(ocr_data['conf'][i])

            # Skip empty text or low confidence detections
            if not text or conf < self.min_confidence:
                continue

            region = TextRegion(
                text=text,
                x=ocr_data['left'][i],
                y=ocr_data['top'][i],
                width=ocr_data['width'][i],
                height=ocr_data['height'][i],
                confidence=conf
            )
            regions.append(region)

        logger.info(f"Detected {len(regions)} text regions")
        return regions

    def filter_regions_by_deny_list(self, regions: List[TextRegion]) -> List[TextRegion]:
        """
        Filter text regions to only include those matching deny list terms.

        Args:
            regions: List of detected text regions

        Returns:
            Filtered list of regions matching deny list
        """
        if not self.deny_list:
            return regions

        filtered = []
        for region in regions:
            text_lower = region.text.lower()
            for term in self.deny_list:
                if term in text_lower or text_lower in term:
                    filtered.append(region)
                    break

        logger.info(f"Filtered to {len(filtered)} regions matching deny list")
        return filtered

    def blur_region(self, image: Image.Image, region: TextRegion, padding: int = 5) -> Image.Image:
        """
        Apply Gaussian blur to a specific region of an image.

        Args:
            image: PIL Image object
            region: TextRegion to blur
            padding: Extra pixels around the region to blur

        Returns:
            Image with blurred region
        """
        # Calculate bounding box with padding
        x1 = max(0, region.x - padding)
        y1 = max(0, region.y - padding)
        x2 = min(image.width, region.x + region.width + padding)
        y2 = min(image.height, region.y + region.height + padding)

        # Crop the region
        region_crop = image.crop((x1, y1, x2, y2))

        # Apply Gaussian blur
        blurred = region_crop.filter(ImageFilter.GaussianBlur(radius=self.blur_radius))

        # Paste blurred region back
        image.paste(blurred, (x1, y1))

        return image

    def anonymize_image(
        self,
        image_path: str,
        output_path: Optional[str] = None,
        blur_all_text: bool = False
    ) -> Tuple[str, Dict[str, int]]:
        """
        Anonymize an image by blurring detected text.

        Args:
            image_path: Path to input image
            output_path: Path for output image (auto-generated if None)
            blur_all_text: If True, blur all detected text; if False, only deny list terms

        Returns:
            Tuple of (output_path, summary dict with counts)
        """
        path = Path(image_path)

        if not path.exists():
            raise ImageAnonymizerError(f"Image file not found: {image_path}")

        if path.suffix.lower() not in SUPPORTED_IMAGE_FORMATS:
            raise ImageAnonymizerError(
                f"Unsupported image format: {path.suffix}. "
                f"Supported: {', '.join(SUPPORTED_IMAGE_FORMATS)}"
            )

        # Load image
        logger.info(f"Loading image: {image_path}")
        image = Image.open(image_path)

        # Convert to RGB if necessary (for RGBA or palette images)
        if image.mode in ('RGBA', 'P'):
            image = image.convert('RGB')

        # Detect text regions
        regions = self.detect_text_regions(image)

        # Filter by deny list unless blur_all_text is True
        if not blur_all_text and self.deny_list:
            regions_to_blur = self.filter_regions_by_deny_list(regions)
        else:
            regions_to_blur = regions

        # Apply blur to each region
        for region in regions_to_blur:
            image = self.blur_region(image, region)
            logger.debug(f"Blurred region: '{region.text}' at ({region.x}, {region.y})")

        # Generate output path if not provided
        if not output_path:
            output_path = str(path.parent / f"{path.stem}_anon{path.suffix}")

        # Save anonymized image
        image.save(output_path)
        logger.info(f"Saved anonymized image to: {output_path}")

        summary = {
            'total_text_regions': len(regions),
            'regions_blurred': len(regions_to_blur),
            'image_width': image.width,
            'image_height': image.height
        }

        return output_path, summary

    def anonymize_image_bytes(
        self,
        image_bytes: bytes,
        blur_all_text: bool = False
    ) -> Tuple[bytes, Dict[str, int]]:
        """
        Anonymize an image from bytes (useful for embedded images).

        Args:
            image_bytes: Image data as bytes
            blur_all_text: If True, blur all detected text

        Returns:
            Tuple of (anonymized image bytes, summary dict)
        """
        from io import BytesIO

        # Load image from bytes
        image = Image.open(BytesIO(image_bytes))

        if image.mode in ('RGBA', 'P'):
            image = image.convert('RGB')

        # Detect and filter regions
        regions = self.detect_text_regions(image)

        if not blur_all_text and self.deny_list:
            regions_to_blur = self.filter_regions_by_deny_list(regions)
        else:
            regions_to_blur = regions

        # Apply blur
        for region in regions_to_blur:
            image = self.blur_region(image, region)

        # Convert back to bytes
        output_buffer = BytesIO()
        image.save(output_buffer, format=image.format or 'PNG')

        summary = {
            'total_text_regions': len(regions),
            'regions_blurred': len(regions_to_blur)
        }

        return output_buffer.getvalue(), summary

    def get_text_preview(self, image_path: str) -> List[Dict]:
        """
        Get a preview of detected text without anonymizing.

        Args:
            image_path: Path to image

        Returns:
            List of dicts with text and position info
        """
        image = Image.open(image_path)
        regions = self.detect_text_regions(image)

        return [
            {
                'text': r.text,
                'position': (r.x, r.y),
                'size': (r.width, r.height),
                'confidence': r.confidence,
                'matches_deny_list': any(
                    term in r.text.lower() or r.text.lower() in term
                    for term in self.deny_list
                ) if self.deny_list else False
            }
            for r in regions
        ]


def anonymize_image(
    image_path: str,
    output_path: Optional[str] = None,
    deny_list: Optional[List[str]] = None,
    blur_all_text: bool = False,
    blur_radius: int = 15
) -> Tuple[str, Dict[str, int]]:
    """
    Convenience function to anonymize an image.

    Args:
        image_path: Path to input image
        output_path: Path for output (auto-generated if None)
        deny_list: List of terms to blur (if None and blur_all_text=False, nothing is blurred)
        blur_all_text: If True, blur all detected text
        blur_radius: Gaussian blur radius

    Returns:
        Tuple of (output_path, summary dict)
    """
    anonymizer = ImageAnonymizer(
        blur_radius=blur_radius,
        deny_list=deny_list
    )
    return anonymizer.anonymize_image(image_path, output_path, blur_all_text)
