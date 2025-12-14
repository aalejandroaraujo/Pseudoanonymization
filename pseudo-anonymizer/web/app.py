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

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from anonymizer import DocumentAnonymizer
from file_handler import extract_text_from_file, write_output_file
from mapping_manager import MappingManager
from config import Config, parse_deny_list, ENTITY_DESCRIPTIONS, OPERATOR_CONFIGS
from pdf_anonymizer import anonymize_pdf_with_mapping, PYMUPDF_AVAILABLE

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

# Ensure directories exist
UPLOADS_DIR.mkdir(exist_ok=True)

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
    session_id: str = Form(...)
):
    """Handle file upload."""
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

            # Diagnostic logging
            diag_msg = f"DEBUG: file_type={file_type}, is_pdf={is_pdf}, PYMUPDF={PYMUPDF_AVAILABLE}, mapping_count={len(mapping) if mapping else 0}"
            yield f"data: {json.dumps({'progress': 90, 'message': log_message(diag_msg)})}\n\n"
            await asyncio.sleep(0.1)

            if is_pdf and PYMUPDF_AVAILABLE and mapping:
                # Output as PDF with same format
                output_file = session_dir / f"{file_stem}_anon.pdf"
                try:
                    yield f"data: {json.dumps({'progress': 91, 'message': log_message('Entering PDF processing branch...')})}\n\n"
                    await asyncio.sleep(0.1)

                    yield f"data: {json.dumps({'progress': 91, 'message': log_message(f'Calling anonymize_pdf_with_mapping with {len(mapping)} terms...')})}\n\n"
                    await asyncio.sleep(0.1)

                    _, replacement_count, pdf_logs = anonymize_pdf_with_mapping(
                        session["file_path"],
                        str(output_file),
                        mapping,
                        operator=session.get("operator", "replace")
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
                yield f"data: {json.dumps({'progress': 91, 'message': log_message(f'Using TEXT output (not PDF branch). is_pdf={is_pdf}, PYMUPDF={PYMUPDF_AVAILABLE}, mapping={bool(mapping)}')})}\n\n"
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


# Run with: uvicorn web.app:app --reload --port 8000
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
