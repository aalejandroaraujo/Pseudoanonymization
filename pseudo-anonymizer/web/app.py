"""
FastAPI Web Application for Pseudo-Anonymizer.

Run with: uvicorn web.app:app --reload
"""

import os
import sys
import uuid
import json
import shutil
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

# Configure logging to show debug info
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from anonymizer import DocumentAnonymizer
from file_handler import extract_text_from_file, write_output_file
from mapping_manager import MappingManager, MappingManagerError
from config import Config, parse_deny_list, ENTITY_DESCRIPTIONS, OPERATOR_CONFIGS
from pdf_anonymizer import anonymize_pdf_with_mapping, PYMUPDF_AVAILABLE
import base64
import io


# ============== Pydantic Models for Text API ==============

class EntityFound(BaseModel):
    """Represents a detected entity in the text."""
    type: str = Field(..., description="Entity type (e.g., PERSON, CUSTOM)")
    original: str = Field(..., description="Original text that was detected")
    replacement: str = Field(..., description="Replacement placeholder")


class AnonymizeTextRequest(BaseModel):
    """Request model for text anonymization."""
    text: str = Field(..., description="Text to anonymize")
    operator: str = Field(default="replace", description="Anonymization operator: replace, mask, redact, hash")
    deny_list: Optional[List[str]] = Field(default=None, description="Custom terms to anonymize")
    language: str = Field(default="en", description="Language code for analysis")


class AnonymizeTextResponse(BaseModel):
    """Response model for text anonymization."""
    anonymized: str = Field(..., description="Anonymized text")
    mapping_id: str = Field(..., description="UUID for the mapping file")
    entities_found: List[EntityFound] = Field(default_factory=list, description="List of detected entities")


class DeanonymizeTextRequest(BaseModel):
    """Request model for text de-anonymization."""
    text: str = Field(..., description="Anonymized text to restore")
    mapping_id: str = Field(..., description="UUID of the mapping file to use")


class DeanonymizeTextResponse(BaseModel):
    """Response model for text de-anonymization."""
    original: str = Field(..., description="De-anonymized text")


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str = Field(default="healthy", description="Service status")
    version: str = Field(default="1.0.0", description="API version")


class ErrorResponse(BaseModel):
    """Response model for errors."""
    detail: str = Field(..., description="Error message")

# Initialize FastAPI app
app = FastAPI(
    title="Pseudo-Anonymizer",
    description="Anonymize sensitive documents for safe LLM sharing",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup paths
BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
UPLOADS_DIR = BASE_DIR.parent / "uploads"
MAPPINGS_DIR = Path(os.environ.get("MAPPINGS_DIR", BASE_DIR.parent / "mappings"))

# Ensure directories exist
UPLOADS_DIR.mkdir(exist_ok=True)
MAPPINGS_DIR.mkdir(exist_ok=True)

# Setup templates and static files
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory session storage (for demo - use Redis/DB in production)
sessions: Dict[str, Dict[str, Any]] = {}

# Load config
config = Config()


def get_session(session_id: str) -> Dict[str, Any]:
    """Get or create a session."""
    if session_id not in sessions:
        sessions[session_id] = {
            "id": session_id,
            "created_at": datetime.now().isoformat(),
            "file_path": None,
            "file_name": None,
            "file_type": None,
            "deny_list": [],
            "entities": list(config.DEFAULT_ENTITIES),
            "operator": config.DEFAULT_OPERATOR,
            "status": "pending",
            "progress": 0,
            "result": None,
            "mapping": None,
            "anonymized_text": None,
            "original_text": None,
            "entity_summary": {},
            "processing_logs": [],  # Store all processing logs
            "manual_blur_regions": [],  # User-drawn regions to blur in images
            "pdf_images": [],  # Extracted PDF images info
        }
    return sessions[session_id]


def cleanup_session(session_id: str):
    """Clean up session files."""
    if session_id in sessions:
        session = sessions[session_id]
        session_dir = UPLOADS_DIR / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir)
        del sessions[session_id]


# ============== Page Routes ==============

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Dashboard page."""
    # Get recent sessions for display
    recent_sessions = sorted(
        sessions.values(),
        key=lambda x: x.get("created_at", ""),
        reverse=True
    )[:10]

    return templates.TemplateResponse("index.html", {
        "request": request,
        "recent_sessions": recent_sessions,
        "stats": {
            "total_processed": len([s for s in sessions.values() if s.get("status") == "completed"]),
            "total_entities": sum(
                sum(s.get("entity_summary", {}).values())
                for s in sessions.values()
                if s.get("status") == "completed"
            ),
        }
    })


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    """File upload page."""
    session_id = str(uuid.uuid4())
    get_session(session_id)  # Initialize session

    return templates.TemplateResponse("upload.html", {
        "request": request,
        "session_id": session_id,
        "supported_formats": list(config.SUPPORTED_FORMATS),
    })


@app.get("/configure/{session_id}", response_class=HTMLResponse)
async def configure_page(request: Request, session_id: str):
    """Configuration page for deny list, entities, and operator."""
    session = get_session(session_id)

    if not session.get("file_path"):
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "No file uploaded. Please upload a file first.",
            "redirect_url": "/upload"
        })

    return templates.TemplateResponse("configure.html", {
        "request": request,
        "session": session,
        "all_entities": config.ALL_ENTITIES,
        "default_entities": config.DEFAULT_ENTITIES,
        "entity_descriptions": ENTITY_DESCRIPTIONS,
        "operators": OPERATOR_CONFIGS,
        "default_operator": config.DEFAULT_OPERATOR,
    })


@app.get("/process/{session_id}", response_class=HTMLResponse)
async def process_page(request: Request, session_id: str):
    """Processing page with progress updates."""
    session = get_session(session_id)

    if not session.get("file_path"):
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "No file uploaded. Please upload a file first.",
            "redirect_url": "/upload"
        })

    return templates.TemplateResponse("process.html", {
        "request": request,
        "session": session,
    })


@app.get("/results/{session_id}", response_class=HTMLResponse)
async def results_page(request: Request, session_id: str):
    """Results page with anonymized output and mapping."""
    session = get_session(session_id)

    if session.get("status") != "completed":
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "Processing not complete. Please wait for processing to finish.",
            "redirect_url": f"/process/{session_id}"
        })

    return templates.TemplateResponse("results.html", {
        "request": request,
        "session": session,
    })


# ============== API Routes ==============

@app.post("/api/upload")
async def api_upload(
    file: UploadFile = File(...),
    session_id: str = Form(None)
):
    """Handle file upload."""
    # Generate session_id if not provided (e.g., from dashboard drag & drop)
    if not session_id:
        session_id = str(uuid.uuid4())
    session = get_session(session_id)

    # Validate file type
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in config.SUPPORTED_FORMATS and file_ext not in {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.gif'}:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file_ext}. Supported: {config.SUPPORTED_FORMATS}"
        )

    # Create session directory
    session_dir = UPLOADS_DIR / session_id
    session_dir.mkdir(exist_ok=True)

    # Save uploaded file
    file_path = session_dir / file.filename
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    # Update session
    session["file_path"] = str(file_path)
    session["file_name"] = file.filename
    session["file_type"] = file_ext
    session["file_size"] = len(content)

    return {
        "success": True,
        "session_id": session_id,
        "file_name": file.filename,
        "file_type": file_ext,
        "file_size": len(content),
        "redirect_url": f"/configure/{session_id}"
    }


@app.post("/api/configure/{session_id}")
async def api_configure(
    session_id: str,
    deny_list: str = Form(""),
    entities: str = Form(""),
    operator: str = Form("replace")
):
    """Save configuration settings."""
    session = get_session(session_id)

    # Parse deny list
    if deny_list.strip():
        session["deny_list"] = parse_deny_list(deny_list.strip())
    else:
        session["deny_list"] = []

    # Parse entities
    if entities.strip():
        session["entities"] = [e.strip() for e in entities.split(",") if e.strip()]
    else:
        session["entities"] = list(config.DEFAULT_ENTITIES)

    # Validate operator
    if operator in config.SUPPORTED_OPERATORS:
        session["operator"] = operator
    else:
        session["operator"] = config.DEFAULT_OPERATOR

    return {
        "success": True,
        "session_id": session_id,
        "deny_list": session["deny_list"],
        "entities": session["entities"],
        "operator": session["operator"],
        "redirect_url": f"/process/{session_id}"
    }


@app.get("/api/entities")
async def api_entities():
    """Get available entity types."""
    return {
        "entities": config.ALL_ENTITIES,
        "defaults": list(config.DEFAULT_ENTITIES),
        "descriptions": ENTITY_DESCRIPTIONS,
    }


@app.get("/api/operators")
async def api_operators():
    """Get available anonymization operators."""
    return {
        "operators": OPERATOR_CONFIGS,
        "default": config.DEFAULT_OPERATOR,
    }


@app.get("/api/pdf-images/{session_id}")
async def api_pdf_images(session_id: str):
    """Extract and return images from a PDF file."""
    session = get_session(session_id)

    if not session.get("file_path"):
        raise HTTPException(status_code=400, detail="No file uploaded")

    if session.get("file_type", "").lower() != ".pdf":
        return {"images": [], "message": "Not a PDF file"}

    if not PYMUPDF_AVAILABLE:
        return {"images": [], "message": "PyMuPDF not available"}

    try:
        import fitz
        from PIL import Image

        doc = fitz.open(session["file_path"])
        images = []

        for page_num in range(doc.page_count):
            page = doc[page_num]
            image_list = page.get_images(full=True)

            for img_index, img_info in enumerate(image_list):
                xref = img_info[0]
                try:
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]

                    # Convert to PIL to get dimensions
                    pil_image = Image.open(io.BytesIO(image_bytes))
                    if pil_image.mode in ('RGBA', 'P'):
                        pil_image = pil_image.convert('RGB')

                    # Convert to base64 for frontend
                    buffered = io.BytesIO()
                    pil_image.save(buffered, format="PNG")
                    img_base64 = base64.b64encode(buffered.getvalue()).decode()

                    images.append({
                        "page": page_num + 1,
                        "index": img_index,
                        "xref": xref,
                        "width": pil_image.width,
                        "height": pil_image.height,
                        "format": image_ext,
                        "data": f"data:image/png;base64,{img_base64}"
                    })
                except Exception as img_error:
                    logger.warning(f"Could not extract image {img_index} from page {page_num}: {img_error}")

        doc.close()

        # Store image info in session
        session["pdf_images"] = [
            {"page": img["page"], "index": img["index"], "xref": img["xref"],
             "width": img["width"], "height": img["height"]}
            for img in images
        ]

        return {"images": images, "count": len(images)}

    except Exception as e:
        logger.error(f"Error extracting PDF images: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/blur-regions/{session_id}")
async def api_get_blur_regions(session_id: str):
    """Get existing blur regions for a session."""
    session = get_session(session_id)
    regions = session.get("manual_blur_regions", [])

    # Convert back to frontend format (imageIndex instead of image_index)
    frontend_regions = [
        {
            "page": r["page"],
            "imageIndex": r["image_index"],
            "x": r["x"],
            "y": r["y"],
            "width": r["width"],
            "height": r["height"],
        }
        for r in regions
    ]

    return {
        "success": True,
        "regions": frontend_regions,
        "count": len(frontend_regions)
    }


@app.post("/api/blur-regions/{session_id}")
async def api_save_blur_regions(session_id: str, request: Request):
    """Save manually drawn blur regions."""
    session = get_session(session_id)

    try:
        data = await request.json()
        regions = data.get("regions", [])

        # Validate and store regions
        validated_regions = []
        for region in regions:
            if all(k in region for k in ["page", "imageIndex", "x", "y", "width", "height"]):
                validated_regions.append({
                    "page": int(region["page"]),
                    "image_index": int(region["imageIndex"]),
                    "x": float(region["x"]),
                    "y": float(region["y"]),
                    "width": float(region["width"]),
                    "height": float(region["height"]),
                })

        session["manual_blur_regions"] = validated_regions

        return {
            "success": True,
            "regions_count": len(validated_regions)
        }

    except Exception as e:
        logger.error(f"Error saving blur regions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/process/{session_id}")
async def api_process(session_id: str):
    """Start the anonymization process."""
    session = get_session(session_id)

    if not session.get("file_path"):
        raise HTTPException(status_code=400, detail="No file uploaded")

    if not os.path.exists(session["file_path"]):
        raise HTTPException(status_code=400, detail="File not found")

    session["status"] = "processing"
    session["progress"] = 0

    return {
        "success": True,
        "session_id": session_id,
        "status": "processing"
    }


@app.get("/api/progress/{session_id}")
async def api_progress(session_id: str):
    """Server-Sent Events endpoint for progress updates."""
    session = get_session(session_id)

    def log_message(msg):
        """Helper to add log message to session and return SSE data."""
        session["processing_logs"].append(msg)
        return msg

    async def event_generator():
        try:
            # Clear previous logs
            session["processing_logs"] = []

            # Update progress: Extracting text
            session["progress"] = 10
            session["status_message"] = "Extracting text from document..."
            yield f"data: {json.dumps({'progress': 10, 'message': log_message('Extracting text from document...')})}\n\n"
            await asyncio.sleep(0.5)

            # Extract text
            try:
                original_text = extract_text_from_file(session["file_path"])
                session["original_text"] = original_text
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                return

            session["progress"] = 30
            yield f"data: {json.dumps({'progress': 30, 'message': log_message('Text extracted successfully')})}\n\n"
            await asyncio.sleep(0.3)

            # Initialize anonymizer
            session["progress"] = 40
            yield f"data: {json.dumps({'progress': 40, 'message': log_message('Initializing PII detection engine...')})}\n\n"
            await asyncio.sleep(0.3)

            try:
                anonymizer = DocumentAnonymizer(
                    deny_list=session.get("deny_list"),
                    entities=session.get("entities")
                )
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                return

            session["progress"] = 50
            yield f"data: {json.dumps({'progress': 50, 'message': log_message('Detecting PII entities...')})}\n\n"
            await asyncio.sleep(0.3)

            # Anonymize
            try:
                # Get entity summary first
                entity_summary = anonymizer.get_entity_summary(original_text)

                # Then anonymize
                anonymized_text, mapping = anonymizer.anonymize_text(
                    original_text,
                    operator=session.get("operator", "replace")
                )
                session["anonymized_text"] = anonymized_text
                session["mapping"] = mapping
                session["entity_summary"] = entity_summary
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                return

            session["progress"] = 80
            yield f"data: {json.dumps({'progress': 80, 'message': log_message(f'Anonymized {sum(entity_summary.values())} entities'), 'entity_summary': entity_summary})}\n\n"
            await asyncio.sleep(0.3)

            # Log the mapping details
            if mapping:
                yield f"data: {json.dumps({'progress': 81, 'message': log_message(f'Mapping contains {len(mapping)} term(s): {list(mapping.keys())}')})}\n\n"
            else:
                yield f"data: {json.dumps({'progress': 81, 'message': log_message('No mapping generated (no entities detected)')})}\n\n"

            # Save output files
            session["progress"] = 90
            yield f"data: {json.dumps({'progress': 90, 'message': log_message('Saving output files...')})}\n\n"

            session_dir = UPLOADS_DIR / session_id
            file_stem = Path(session['file_name']).stem
            mapping_file = session_dir / f"{file_stem}_mapping.json"

            # Check if input is PDF and PyMuPDF is available for PDF output
            file_type = session.get("file_type", "")
            is_pdf = file_type.lower() == ".pdf"

            # Check if we have manual blur regions (for re-processing)
            manual_regions = session.get("manual_blur_regions", [])
            has_manual_regions = len(manual_regions) > 0

            # Ensure custom deny list terms are included in the mapping for image OCR
            deny_list = session.get("deny_list", [])
            if deny_list:
                for term in deny_list:
                    if term not in mapping:
                        # Add deny list term with a placeholder replacement
                        mapping[term] = f"<CUSTOM_{len(mapping)+1}>"

            # Diagnostic logging
            diag_msg = f"DEBUG: file_type={file_type}, is_pdf={is_pdf}, PYMUPDF={PYMUPDF_AVAILABLE}, mapping_count={len(mapping) if mapping else 0}, manual_regions={len(manual_regions)}"
            yield f"data: {json.dumps({'progress': 90, 'message': log_message(diag_msg)})}\n\n"
            await asyncio.sleep(0.1)

            if is_pdf and PYMUPDF_AVAILABLE:
                # Output as PDF - always preserve PDF format for PDF input
                output_file = session_dir / f"{file_stem}_anon.pdf"
                try:
                    yield f"data: {json.dumps({'progress': 91, 'message': log_message('Entering PDF processing branch...')})}\n\n"
                    await asyncio.sleep(0.1)

                    yield f"data: {json.dumps({'progress': 91, 'message': log_message(f'Calling anonymize_pdf_with_mapping with {len(mapping) if mapping else 0} terms and {len(manual_regions)} manual blur region(s)...')})}\n\n"
                    await asyncio.sleep(0.1)

                    _, replacement_count, pdf_logs = anonymize_pdf_with_mapping(
                        session["file_path"],
                        str(output_file),
                        mapping,
                        operator=session.get("operator", "replace"),
                        manual_blur_regions=manual_regions
                    )
                    session["output_format"] = "pdf"

                    yield f"data: {json.dumps({'progress': 92, 'message': log_message(f'PDF processing returned {replacement_count} replacements and {len(pdf_logs)} log entries')})}\n\n"
                    await asyncio.sleep(0.1)

                    # Send PDF processing logs to frontend
                    for i, log_msg in enumerate(pdf_logs):
                        progress = 92 + int((i / max(len(pdf_logs), 1)) * 5)
                        yield f"data: {json.dumps({'progress': progress, 'message': log_message(log_msg)})}\n\n"
                        await asyncio.sleep(0.02)

                except Exception as pdf_error:
                    # Fall back to text output if PDF anonymization fails
                    logger.warning(f"PDF anonymization failed, falling back to text: {pdf_error}")
                    yield f"data: {json.dumps({'progress': 92, 'message': log_message(f'PDF processing failed: {pdf_error}, falling back to text...')})}\n\n"
                    output_file = session_dir / f"{file_stem}_anon.txt"
                    write_output_file(anonymized_text, str(output_file))
                    session["output_format"] = "txt"
            else:
                # Output as text
                yield f"data: {json.dumps({'progress': 91, 'message': log_message(f'Using TEXT output (not PDF branch). is_pdf={is_pdf}, PYMUPDF={PYMUPDF_AVAILABLE}, mapping={bool(mapping)}, manual_regions={has_manual_regions}')})}\n\n"
                await asyncio.sleep(0.1)
                output_file = session_dir / f"{file_stem}_anon.txt"
                write_output_file(anonymized_text, str(output_file))
                session["output_format"] = "txt"

            # Save mapping using the proper method
            mapping_manager = MappingManager()
            mapping_manager.add_mappings(mapping)
            mapping_manager.save(str(mapping_file))

            session["output_file"] = str(output_file)
            session["mapping_file"] = str(mapping_file)

            session["progress"] = 100
            session["status"] = "completed"
            yield f"data: {json.dumps({'progress': 100, 'message': log_message('Anonymization complete!'), 'status': 'completed', 'redirect_url': f'/results/{session_id}'})}\n\n"

        except Exception as e:
            session["status"] = "error"
            session["error"] = str(e)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.get("/api/results/{session_id}")
async def api_results(session_id: str):
    """Get anonymization results."""
    session = get_session(session_id)

    if session.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Processing not complete")

    return {
        "success": True,
        "session_id": session_id,
        "file_name": session.get("file_name"),
        "anonymized_text": session.get("anonymized_text"),
        "entity_summary": session.get("entity_summary"),
        "mapping": session.get("mapping"),
        "operator": session.get("operator"),
    }


@app.get("/api/download/{session_id}/{file_type}")
async def api_download(session_id: str, file_type: str):
    """Download anonymized file or mapping."""
    session = get_session(session_id)

    if session.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Processing not complete")

    if file_type == "anonymized":
        file_path = session.get("output_file")
        # Use correct extension based on output format
        output_format = session.get("output_format", "txt")
        filename = f"{Path(session['file_name']).stem}_anon.{output_format}"
    elif file_type == "mapping":
        file_path = session.get("mapping_file")
        filename = f"{Path(session['file_name']).stem}_mapping.json"
    elif file_type == "log":
        # Generate log file on the fly
        logs = session.get("processing_logs", [])
        log_content = "\n".join([f"[{i+1}] {log}" for i, log in enumerate(logs)])

        # Return as text response
        from fastapi.responses import Response
        return Response(
            content=log_content,
            media_type="text/plain",
            headers={
                "Content-Disposition": f"attachment; filename={Path(session['file_name']).stem}_processing_log.txt"
            }
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid file type")

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/octet-stream"
    )


@app.post("/api/clear-sessions")
async def api_clear_sessions():
    """Clear all sessions and uploaded files - allows starting fresh."""
    global sessions

    # Clean up all session directories
    for session_id in list(sessions.keys()):
        session_dir = UPLOADS_DIR / session_id
        if session_dir.exists():
            try:
                shutil.rmtree(session_dir)
            except Exception as e:
                logger.warning(f"Could not remove session dir {session_id}: {e}")

    # Clear sessions dict
    sessions.clear()

    return {
        "success": True,
        "message": "All sessions cleared successfully"
    }


@app.post("/api/de-anonymize")
async def api_de_anonymize(
    text: str = Form(...),
    mapping_file: UploadFile = File(...)
):
    """De-anonymize text using a mapping file."""
    try:
        # Read mapping file
        mapping_content = await mapping_file.read()
        mapping_data = json.loads(mapping_content)

        # Get reverse mapping
        reverse_mapping = mapping_data.get("reverse_mapping", {})

        if not reverse_mapping:
            # Try to create reverse mapping from mapping
            mapping = mapping_data.get("mapping", {})
            reverse_mapping = {v: k for k, v in mapping.items()}

        # De-anonymize
        result = text
        for placeholder, original in reverse_mapping.items():
            result = result.replace(placeholder, original)

        return {
            "success": True,
            "de_anonymized_text": result,
        }
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid mapping file format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============== Text API v1 Routes (for programmatic integration) ==============

@app.get("/api/v1/health", response_model=HealthResponse)
async def api_v1_health():
    """Health check endpoint for service monitoring."""
    return HealthResponse(status="healthy", version="1.0.0")


@app.post("/api/v1/anonymize-text", response_model=AnonymizeTextResponse)
async def api_v1_anonymize_text(request: AnonymizeTextRequest):
    """
    Anonymize text and return the result with a mapping ID.

    The mapping is saved to a file for later de-anonymization.
    Default operator is "replace" which creates reversible pseudonyms.
    """
    try:
        # Validate operator
        valid_operators = {"replace", "mask", "redact", "hash"}
        if request.operator not in valid_operators:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid operator: {request.operator}. Valid options: {', '.join(valid_operators)}"
            )

        # Build entities list - include CUSTOM if deny_list provided
        entities = list(config.DEFAULT_ENTITIES)
        if request.deny_list and "CUSTOM" not in entities:
            entities.append("CUSTOM")

        # Initialize anonymizer
        anonymizer = DocumentAnonymizer(
            deny_list=request.deny_list,
            entities=entities,
            language=request.language
        )

        # Perform anonymization
        anonymized_text, mapping = anonymizer.anonymize_text(
            request.text,
            operator=request.operator
        )

        # Generate mapping ID and save mapping
        mapping_id = str(uuid.uuid4())
        mapping_file = MAPPINGS_DIR / f"{mapping_id}.json"

        mapping_manager = MappingManager()
        mapping_manager.add_mappings(mapping)
        mapping_manager.save(str(mapping_file))

        # Build entities_found list from the mapping
        entities_found = []
        for original, replacement in mapping.items():
            # Extract entity type from replacement (e.g., <PERSON_1> -> PERSON)
            entity_type = "UNKNOWN"
            if replacement.startswith("<") and replacement.endswith(">"):
                inner = replacement[1:-1]
                if "_" in inner:
                    entity_type = inner.rsplit("_", 1)[0]
                else:
                    entity_type = inner
            entities_found.append(EntityFound(
                type=entity_type,
                original=original,
                replacement=replacement
            ))

        return AnonymizeTextResponse(
            anonymized=anonymized_text,
            mapping_id=mapping_id,
            entities_found=entities_found
        )

    except Exception as e:
        logger.error(f"Anonymization error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/deanonymize-text", response_model=DeanonymizeTextResponse)
async def api_v1_deanonymize_text(request: DeanonymizeTextRequest):
    """
    De-anonymize text using a previously saved mapping.

    Requires the mapping_id from the original anonymize-text response.
    """
    try:
        # Validate mapping_id format (should be UUID)
        try:
            uuid.UUID(request.mapping_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid mapping_id format")

        # Load mapping file
        mapping_file = MAPPINGS_DIR / f"{request.mapping_id}.json"

        if not mapping_file.exists():
            raise HTTPException(status_code=404, detail=f"Mapping not found: {request.mapping_id}")

        try:
            mapping_manager = MappingManager(str(mapping_file))
        except MappingManagerError as e:
            raise HTTPException(status_code=400, detail=f"Invalid mapping file: {e}")

        # De-anonymize text
        original_text = mapping_manager.de_anonymize_text(request.text)

        return DeanonymizeTextResponse(original=original_text)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"De-anonymization error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/mappings/{mapping_id}")
async def api_v1_get_mapping(mapping_id: str):
    """
    Retrieve a mapping by its ID for inspection.

    Returns the full mapping JSON including metadata.
    """
    try:
        # Validate mapping_id format
        try:
            uuid.UUID(mapping_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid mapping_id format")

        mapping_file = MAPPINGS_DIR / f"{mapping_id}.json"

        if not mapping_file.exists():
            raise HTTPException(status_code=404, detail=f"Mapping not found: {mapping_id}")

        with open(mapping_file, 'r', encoding='utf-8') as f:
            mapping_data = json.load(f)

        return mapping_data

    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Corrupted mapping file")
    except Exception as e:
        logger.error(f"Error retrieving mapping: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v1/mappings/{mapping_id}")
async def api_v1_delete_mapping(mapping_id: str):
    """
    Delete a mapping file.

    Use this for cleanup after de-anonymization is no longer needed.
    """
    try:
        # Validate mapping_id format
        try:
            uuid.UUID(mapping_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid mapping_id format")

        mapping_file = MAPPINGS_DIR / f"{mapping_id}.json"

        if not mapping_file.exists():
            raise HTTPException(status_code=404, detail=f"Mapping not found: {mapping_id}")

        mapping_file.unlink()

        return {"success": True, "message": f"Mapping {mapping_id} deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting mapping: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Run with: uvicorn web.app:app --reload --port 8000
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
