import os
from dotenv import load_dotenv
load_dotenv()

import json
import uuid
import time
import shutil
import base64
import asyncio
import requests
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Response
from fastapi.responses import StreamingResponse, RedirectResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from authlib.integrations.requests_client import OAuth2Session
from google.genai import types
from agents.agent import (
    build_agent,
    refine_agent,
    deploy_workspace_app,
    run_antigravity_agent,
    analyze_sketch_with_adk,
    WORKSPACE_DIR,
    WORKSPACE_ROOT,
)
import jobs
import oauth_sessions
import deploy_scaffold

app = FastAPI(title="Sketch2Cloud API")
jobs.init_db()
oauth_sessions.init_db()

# A server restart kills any in-flight background job (asyncio task + in-memory
# agent session). Mark those as errored on startup so clients stop waiting on them.
_orphaned = jobs.reconcile_orphaned_jobs()
if _orphaned:
    print(f"[startup] marked {_orphaned} orphaned job(s) as error")

# Whole-phase wall-clock ceilings: a wedged agent can't hang a job forever.
BUILD_PHASE_TIMEOUT = 1800   # 30 min for the full build pipeline
DEPLOY_PHASE_TIMEOUT = 1200  # 20 min: API enable + cold Cloud Build source deploy

GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
OAUTH_REDIRECT_BASE_URL = os.getenv("OAUTH_REDIRECT_BASE_URL", "http://localhost:8080")
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000")
OAUTH_REDIRECT_URI = f"{OAUTH_REDIRECT_BASE_URL}/api/gcloud/oauth/callback"
SESSION_COOKIE_NAME = "s2c_session"

# Session-cookie cross-site policy. Local dev (backend + frontend both on
# localhost) works with the default "lax". When the two are deployed to
# different domains, the browser only sends the cookie back on the OAuth
# redirect / SSE / fetch if it is "none" + Secure - set COOKIE_SAMESITE=none
# (COOKIE_SECURE is then forced on).
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax").lower()
if COOKIE_SAMESITE not in ("lax", "strict", "none"):
    COOKIE_SAMESITE = "lax"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "").lower() in ("1", "true", "yes") or COOKIE_SAMESITE == "none"

# Enable CORS - a specific origin (not "*") is required for cookies to ride along.
# "localhost" and "127.0.0.1" are different origins to the browser, so allow
# both for local dev regardless of which one FRONTEND_BASE_URL happens to use.
_cors_origins = {FRONTEND_BASE_URL}
if "localhost" in FRONTEND_BASE_URL:
    _cors_origins.add(FRONTEND_BASE_URL.replace("localhost", "127.0.0.1"))
elif "127.0.0.1" in FRONTEND_BASE_URL:
    _cors_origins.add(FRONTEND_BASE_URL.replace("127.0.0.1", "localhost"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_session_id(request: Request) -> str:
    return oauth_sessions.get_or_create_session(request.cookies.get(SESSION_COOKIE_NAME))


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME, session_id,
        httponly=True, samesite=COOKIE_SAMESITE, secure=COOKIE_SECURE,
        max_age=60 * 60 * 24 * 30
    )


def job_workspace_dir(job_id: str) -> Path:
    """Filesystem workspace for one pipeline run: workspace/<job_id>/.

    Per-job isolation keeps concurrent runs (or a run + a refinement pass) from
    overwriting each other's generated files.
    """
    return WORKSPACE_ROOT / job_id


def _require_job_owner(job_id: str, request: Request) -> dict:
    """Return the job iff the caller's session cookie owns it, else 404.

    A non-owner gets 404 (not 403) so job ids aren't confirmable by outsiders.
    """
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if job.get("session_id") and job.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _resolve_workspace_base(request: Request, job_id: Optional[str]) -> Optional[Path]:
    """Workspace dir the caller may browse: the named job (if they own it) or
    their most recent run. Returns None when there's nothing to show."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if job_id:
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.get("session_id") and job.get("session_id") != session_id:
            raise HTTPException(status_code=404, detail="Job not found")
        return job_workspace_dir(job_id)
    if not session_id:
        return None
    latest = jobs.get_latest_job_for_session(session_id)
    return job_workspace_dir(latest["id"]) if latest else None


# Upload directory (repo-relative by default; override with S2C_UPLOAD_DIR).
UPLOAD_DIR = Path(os.getenv("S2C_UPLOAD_DIR") or (Path(__file__).resolve().parent / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _validated_upload_path(image_path: Optional[str]) -> Optional[str]:
    """Return a safe absolute path for a client-supplied ``image_path``.

    The client echoes this string back from ``/api/upload-sketch``. Without a
    guard it could name *any* file on the host and have its bytes read into the
    model prompt and the persisted event log. Only files that actually live
    inside ``UPLOAD_DIR`` are accepted.
    """
    if not image_path:
        return None
    path = Path(image_path)
    if not _is_within(path, UPLOAD_DIR):
        raise HTTPException(status_code=400, detail="image_path must reference an uploaded sketch")
    resolved = path.resolve()
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="image_path does not exist")
    return str(resolved)

ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
IMAGE_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

class RunAgentRequest(BaseModel):
    message: str
    image_path: Optional[str] = None
    image_data_url: Optional[str] = None
    sketch_analysis: Optional[dict] = None
    gcp_project: Optional[str] = None
    gcp_region: Optional[str] = "us-central1"
    # Refinement pass: reuse a prior run's generated files and apply one change
    # in place instead of regenerating from the sketch.
    refine: bool = False
    parent_job_id: Optional[str] = None


class AnalyzeSketchRequest(BaseModel):
    image_path: Optional[str] = None
    image_data_url: Optional[str] = None
    prompt_hint: Optional[str] = ""


def _decode_data_url_to_upload(data_url: str) -> tuple[bytes, str, str]:
    """Decode a base64 `data:image/...;base64,...` string and persist it in UPLOAD_DIR."""
    if not data_url or not data_url.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="Invalid image_data_url format")
    header, _, b64_payload = data_url.partition(",")
    mime_type = header.split(";")[0].replace("data:", "").strip().lower()
    ext_by_mime = {v: k for k, v in IMAGE_MIME_BY_EXT.items()}
    ext = ext_by_mime.get(mime_type, ".png")
    raw_bytes = base64.b64decode(b64_payload)
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds maximum upload size")
    filename = f"sketch_{uuid.uuid4().hex}{ext}"
    target_path = UPLOAD_DIR / filename
    target_path.write_bytes(raw_bytes)
    return raw_bytes, mime_type, str(target_path.resolve())


@app.post("/api/upload-sketch")
async def upload_sketch(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image type '{ext or file.filename}'. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTS))}",
        )
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail=f"Expected an image upload, got content-type '{file.content_type}'")

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Image exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")

    try:
        filename = f"sketch_{uuid.uuid4().hex}{ext}"
        target_path = UPLOAD_DIR / filename
        target_path.write_bytes(data)
        return {
            "status": "success",
            "image_path": str(target_path),
            "filename": filename,
            "image_url": f"/api/uploads/{filename}",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/uploads/{filename}")
def get_uploaded_sketch(filename: str):
    """Serve an uploaded sketch image safely from UPLOAD_DIR for Studio preview."""
    candidate = UPLOAD_DIR / filename
    if not _is_within(candidate, UPLOAD_DIR) or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Uploaded sketch not found")
    mime_type = IMAGE_MIME_BY_EXT.get(candidate.suffix.lower(), "image/png")
    return FileResponse(str(candidate.resolve()), media_type=mime_type)


@app.post("/api/analyze-sketch")
async def analyze_sketch_endpoint(body: AnalyzeSketchRequest):
    """Run the Google ADK Sketch Comprehension Agent (`adk_sketch_interpreter_agent`)
    on an uploaded sketch or blueprint image BEFORE the build pipeline starts.
    """
    img_bytes: Optional[bytes] = None
    mime_type = "image/png"
    resolved_path: Optional[str] = None

    if body.image_path:
        try:
            resolved_path = _validated_upload_path(body.image_path)
            if resolved_path and os.path.exists(resolved_path):
                p = Path(resolved_path)
                img_bytes = p.read_bytes()
                mime_type = IMAGE_MIME_BY_EXT.get(p.suffix.lower(), "image/png")
        except HTTPException:
            # If the Cloud Run instance scaled/restarted and we also have image_data_url, fall back to it
            resolved_path = None

    if img_bytes is None and body.image_data_url:
        img_bytes, mime_type, resolved_path = _decode_data_url_to_upload(body.image_data_url)

    if img_bytes is None:
        raise HTTPException(status_code=400, detail="Provide a valid uploaded sketch image_path or image_data_url")

    try:
        analysis = await analyze_sketch_with_adk(
            image_bytes=img_bytes,
            mime_type=mime_type,
            user_hint=body.prompt_hint or "",
        )
        return {
            "status": "success",
            "engine": "Google ADK (LlmAgent + Gemini Vision)",
            "image_path": resolved_path,
            "analysis": analysis,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ADK sketch comprehension failed: {str(e)}")

def serialize_event(event) -> dict:
    """Helper to convert an event object or dict into a JSON-serializable dict matching frontend expectations."""
    if isinstance(event, dict):
        return event
    parts = []
    if event.content and hasattr(event.content, "parts") and event.content.parts:
        for part in event.content.parts:
            part_dict = {}
            if hasattr(part, "text") and part.text:
                part_dict["text"] = part.text
            if hasattr(part, "inline_data") and part.inline_data:
                part_dict["inline_data"] = {
                    "mime_type": part.inline_data.mime_type,
                    "data": base64.b64encode(part.inline_data.data).decode("utf-8") if isinstance(part.inline_data.data, bytes) else part.inline_data.data
                }
            if hasattr(part, "function_call") and part.function_call:
                part_dict["functionCall"] = {
                    "name": part.function_call.name,
                    "args": part.function_call.args,
                    "id": part.function_call.id
                }
            if hasattr(part, "function_response") and part.function_response:
                part_dict["functionResponse"] = {
                    "name": part.function_response.name,
                    "response": part.function_response.response,
                    "id": part.function_response.id
                }
            if part_dict:
                parts.append(part_dict)
                
    actions_dict = {}
    if getattr(event, "actions", None):
        actions_dict["stateDelta"] = event.actions.state_delta or {}
        if event.actions.transfer_to_agent:
            actions_dict["transferToAgent"] = event.actions.transfer_to_agent
            
    return {
        "author": getattr(event, "author", None) or "coordinator",
        "content": {"parts": parts} if parts else None,
        "partial": getattr(event, "partial", False),
        "actions": actions_dict if actions_dict else None
    }

def _build_message_content(message: str, image_path: Optional[str]) -> types.Content:
    if image_path and _is_within(Path(image_path), UPLOAD_DIR) and os.path.exists(image_path):
        img_path = Path(image_path)
        img_content = img_path.read_bytes()
        mime_type = IMAGE_MIME_BY_EXT.get(img_path.suffix.lower(), "image/png")
        return types.Content(
            parts=[
                types.Part(text=message),
                types.Part(inline_data=types.Blob(mime_type=mime_type, data=img_content))
            ]
        )
    return types.Content(parts=[types.Part(text=message)])


async def _run_phase(job_id: str, agent, message: str, image_path: Optional[str],
                      gcp_project: Optional[str], gcp_region: Optional[str],
                      gcp_access_token: Optional[str] = None,
                      workspace_dir: Optional[Path] = None,
                      sketch_analysis: Optional[dict] = None) -> None:
    """Runs one Google Antigravity SDK agent or pipeline to completion, persisting every event to the job's log."""
    initial_state: dict = {}
    if gcp_project:
        initial_state["gcp_project"] = gcp_project
    if gcp_region:
        initial_state["gcp_region"] = gcp_region
    if gcp_access_token:
        initial_state["gcp_access_token"] = gcp_access_token
    if workspace_dir:
        initial_state["workspace_dir"] = str(workspace_dir)
    if sketch_analysis:
        initial_state["sketch_analysis"] = sketch_analysis

    valid_image_path: Optional[str] = None
    if image_path and _is_within(Path(image_path), UPLOAD_DIR) and os.path.exists(image_path):
        valid_image_path = str(Path(image_path).resolve())

    async for event in run_antigravity_agent(
        agent_or_pipeline=agent,
        message=message,
        image_path=valid_image_path,
        workspace_dir=workspace_dir,
        initial_state=initial_state,
    ):
        jobs.append_event(job_id, serialize_event(event))


def _emit_metric(job_id: str, text: str) -> None:
    jobs.append_event(job_id, {"author": "system", "content": {"parts": [{"text": text}]}})


def _fail_phase(job_id: str, log_text: str, result_msg: str, emit_done: bool = True) -> None:
    jobs.set_status(job_id, "error", result={"message": result_msg})
    jobs.append_event(job_id, {"author": "system", "content": {"parts": [{"text": log_text}]}})
    jobs.append_event(job_id, {"author": "system", "control": "error"})
    if emit_done:
        jobs.append_event(job_id, {"author": "system", "control": "done"})


def _finalize_verified_build(job_id: str, workspace_dir: Path, elapsed: float) -> None:
    # Lay down a known-good container/deploy scaffold so `gcloud run deploy
    # --source .` actually produces a running service (the agents write app
    # code, not a Dockerfile / .gcloudignore).
    try:
        written = deploy_scaffold.write_deploy_scaffold(workspace_dir)
        if written:
            _emit_metric(job_id, "Prepared deployment scaffold: " + ", ".join(written))
    except Exception as scaffold_err:
        _emit_metric(job_id, f"Deployment scaffold step skipped: {scaffold_err}")
    _emit_metric(job_id, f"Build phase finished in {elapsed:.0f}s.")
    jobs.set_status(job_id, "awaiting_approval")
    jobs.append_event(job_id, {"author": "system", "control": "awaiting_approval"})


async def _run_build_like_phase(job_id: str, agent, message: str, image_path: Optional[str],
                                 gcp_project: Optional[str], gcp_region: Optional[str], label: str,
                                 sketch_analysis: Optional[dict] = None) -> None:
    workspace_dir = job_workspace_dir(job_id)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        await asyncio.wait_for(
            _run_phase(job_id, agent, message, image_path, gcp_project, gcp_region,
                       workspace_dir=workspace_dir, sketch_analysis=sketch_analysis),
            timeout=BUILD_PHASE_TIMEOUT,
        )
        _finalize_verified_build(job_id, workspace_dir, time.monotonic() - started)
    except asyncio.TimeoutError:
        _fail_phase(job_id, f"{label} timed out after {BUILD_PHASE_TIMEOUT}s - aborted.",
                    f"{label} exceeded {BUILD_PHASE_TIMEOUT}s and was aborted.")
    except Exception as e:
        _fail_phase(job_id, f"Error running {label.lower()}: {str(e)}", str(e))


async def run_build_phase(job_id: str, message: str, image_path: Optional[str],
                           gcp_project: Optional[str], gcp_region: Optional[str],
                           sketch_analysis: Optional[dict] = None) -> None:
    await _run_build_like_phase(job_id, build_agent, message, image_path,
                                gcp_project, gcp_region, "Build pipeline",
                                sketch_analysis=sketch_analysis)


async def run_refine_phase(job_id: str, message: str,
                            gcp_project: Optional[str], gcp_region: Optional[str]) -> None:
    await _run_build_like_phase(job_id, refine_agent, message, None,
                                gcp_project, gcp_region, "Refinement pass")


def _resolve_deploy_credentials(job: dict) -> tuple[Optional[str], Optional[str]]:
    """Resolve (access_token, gcp_project) for Cloud Run deployment.

    1. Uses the user's OAuth token and project from the job or session.
    2. If the user is authenticated via OAuth but didn't explicitly pick a project
       in the dropdown, auto-discovers their active GCP project (preferring
       'agents-461209' or their first ACTIVE project).
    3. When running inside Cloud Run (`K_SERVICE` is set), falls back to the
       Cloud Run service account token from the GCP Metadata Server and project
       'agents-461209' so live Cloud Run runs never unexpectedly fall back to mock.
    """
    access_token: Optional[str] = None
    session_project: Optional[str] = None
    session_id = job.get("session_id")

    if session_id:
        session = oauth_sessions.get_session(session_id)
        if session:
            session_project = session.get("gcp_project")
        if GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET:
            access_token = oauth_sessions.get_valid_access_token(
                session_id, GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET
            )

    target_project = job.get("gcp_project") or session_project

    # Auto-discover project from the user's OAuth token if none was selected in the UI.
    if access_token and not target_project:
        try:
            res = requests.get(
                "https://cloudresourcemanager.googleapis.com/v1/projects",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if res.status_code == 200:
                active = [
                    p["projectId"]
                    for p in res.json().get("projects", [])
                    if p.get("lifecycleState") == "ACTIVE" and p.get("projectId")
                ]
                if "agents-461209" in active:
                    target_project = "agents-461209"
                elif active:
                    target_project = active[0]
        except Exception:
            pass

    # Fallback when running on Cloud Run (`K_SERVICE` is set) so live builds always deploy for real.
    if os.getenv("K_SERVICE"):
        if not target_project:
            target_project = os.getenv("GOOGLE_CLOUD_PROJECT") or "agents-461209"
        if not access_token:
            try:
                meta = requests.get(
                    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                    headers={"Metadata-Flavor": "Google"},
                    timeout=5,
                )
                if meta.status_code == 200:
                    access_token = meta.json().get("access_token")
            except Exception:
                pass

    return access_token, target_project


async def run_deploy_phase(
    job_id: str,
    override_project: Optional[str] = None,
    override_region: Optional[str] = None,
) -> None:
    job = jobs.get_job(job_id)
    if job is None:
        return
    started = time.monotonic()
    try:
        access_token, resolved_project = _resolve_deploy_credentials(job)
        target_project = override_project or resolved_project
        target_region = override_region or job.get("gcp_region") or "us-central1"

        jobs.append_event(job_id, {
            "author": "deployment_agent",
            "content": {"parts": [{"text": f"Deploying the build-verified application to Cloud Run ({target_project or 'sandbox'} / {target_region})..."}]},
        })

        # Deterministic deploy - one `gcloud` invocation, no LLM turn. It's a
        # blocking subprocess, so run it off the event loop; the inner
        # subprocess timeout (300s) keeps it under DEPLOY_PHASE_TIMEOUT.
        result = await asyncio.wait_for(
            asyncio.to_thread(
                deploy_workspace_app,
                job_workspace_dir(job_id),
                access_token,
                target_project,
                target_region,
            ),
            timeout=DEPLOY_PHASE_TIMEOUT,
        )

        # Emit a tool-shaped event so the existing frontend log/URL handling
        # (part.functionResponse.response.url) works unchanged.
        jobs.append_event(job_id, {
            "author": "deployment_agent",
            "content": {"parts": [{"functionResponse": {"name": "deploy_workspace_app", "response": result}}]},
        })
        _emit_metric(job_id, f"Deploy phase finished in {time.monotonic() - started:.0f}s.")

        if result.get("status") == "error":
            jobs.set_status(job_id, "error", result={"message": result.get("message", "Deployment failed.")})
            jobs.append_event(job_id, {
                "author": "system",
                "content": {"parts": [{"text": result.get("message", "Deployment failed.")}]},
            })
            jobs.append_event(job_id, {"author": "system", "control": "error"})
        else:
            jobs.set_status(job_id, "success", result={"url": result.get("url"), "mode": result.get("mode")})
    except asyncio.TimeoutError:
        jobs.set_status(job_id, "error", result={"message": f"Deployment exceeded {DEPLOY_PHASE_TIMEOUT}s and was aborted."})
        jobs.append_event(job_id, {
            "author": "system",
            "content": {"parts": [{"text": f"Deployment timed out after {DEPLOY_PHASE_TIMEOUT}s - aborted."}]}
        })
        jobs.append_event(job_id, {"author": "system", "control": "error"})
    except Exception as e:
        jobs.set_status(job_id, "error", result={"message": str(e)})
        jobs.append_event(job_id, {
            "author": "system",
            "content": {"parts": [{"text": f"Error running deployment: {str(e)}"}]}
        })
        jobs.append_event(job_id, {"author": "system", "control": "error"})
    finally:
        jobs.append_event(job_id, {"author": "system", "control": "done"})


@app.post("/api/runs")
async def create_run(body: RunAgentRequest, request: Request, response: Response):
    session_id = get_session_id(request)
    set_session_cookie(response, session_id)

    # Remember the chosen project so it survives a page reload.
    if body.gcp_project:
        oauth_sessions.set_project(session_id, body.gcp_project)

    # A refinement pass reuses a prior run's generated files.
    refine = bool(body.refine and body.parent_job_id)
    if refine:
        parent = jobs.get_job(body.parent_job_id)
        if parent is None or (parent.get("session_id") and parent["session_id"] != session_id):
            raise HTTPException(status_code=404, detail="Parent job not found")

    # A refinement pass never takes a sketch; otherwise the image_path must be
    # one this server handed out from /api/upload-sketch (see _validated_upload_path),
    # or materialized from image_data_url if the Cloud Run container scaled across instances.
    image_path: Optional[str] = None
    if not refine:
        if body.image_path:
            try:
                image_path = _validated_upload_path(body.image_path)
            except HTTPException:
                if body.image_data_url:
                    _, _, image_path = _decode_data_url_to_upload(body.image_data_url)
                else:
                    raise
        elif body.image_data_url:
            _, _, image_path = _decode_data_url_to_upload(body.image_data_url)

    job_id = jobs.create_job(
        message=body.message,
        image_path=image_path,
        gcp_project=body.gcp_project,
        gcp_region=body.gcp_region,
        session_id=session_id,
        parent_job_id=body.parent_job_id if refine else None,
    )

    if refine:
        src, dst = job_workspace_dir(body.parent_job_id), job_workspace_dir(job_id)
        try:
            if src.exists():
                shutil.copytree(
                    src, dst, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("node_modules", ".next", ".git"),
                )
        except Exception as e:
            _fail_phase(job_id, f"Could not seed refinement workspace: {e}",
                        f"Refinement seed failed: {e}")
            return {"job_id": job_id}
        _emit_metric(job_id, f"Refinement pass - seeded from job {body.parent_job_id}.")
        asyncio.create_task(run_refine_phase(
            job_id, body.message, body.gcp_project, body.gcp_region
        ))
    else:
        asyncio.create_task(run_build_phase(
            job_id, body.message, image_path, body.gcp_project, body.gcp_region,
            sketch_analysis=body.sketch_analysis,
        ))
    return {"job_id": job_id}


@app.get("/api/runs")
def list_runs(request: Request, response: Response):
    """Every run owned by this browser session, newest first - powers the
    Projects list so a user can see and reopen past generations."""
    session_id = get_session_id(request)
    set_session_cookie(response, session_id)
    jobs_list = jobs.list_jobs_for_session(session_id)
    return [
        {
            "id": j["id"],
            "status": j["status"],
            "message": j["message"],
            "parent_job_id": j.get("parent_job_id"),
            "url": (j.get("result") or {}).get("url"),
            "mode": (j.get("result") or {}).get("mode"),
            "created_at": j["created_at"],
            "updated_at": j["updated_at"],
        }
        for j in jobs_list
    ]


@app.get("/api/runs/{job_id}")
def get_run(job_id: str, request: Request):
    return _require_job_owner(job_id, request)


class ApproveRequest(BaseModel):
    approved: bool
    gcp_project: Optional[str] = None
    gcp_region: Optional[str] = None


@app.post("/api/runs/{job_id}/approve")
async def approve_run(job_id: str, body: ApproveRequest, request: Request):
    job = _require_job_owner(job_id, request)
    if job["status"] != "awaiting_approval":
        raise HTTPException(status_code=409, detail=f"Job is not awaiting approval (status: {job['status']})")

    if body.gcp_project and job.get("session_id"):
        oauth_sessions.set_project(job["session_id"], body.gcp_project)

    if body.approved:
        jobs.set_status(job_id, "deploying")
        jobs.append_event(job_id, {"author": "system", "control": "deploying"})
        asyncio.create_task(run_deploy_phase(job_id, body.gcp_project, body.gcp_region))
    else:
        jobs.set_status(job_id, "rejected")
        jobs.append_event(job_id, {"author": "system", "control": "rejected"})
        jobs.append_event(job_id, {"author": "system", "control": "done"})

    return {"status": jobs.get_job(job_id)["status"]}


class RedeployRequest(BaseModel):
    gcp_project: Optional[str] = None
    gcp_region: Optional[str] = None


@app.post("/api/runs/{job_id}/redeploy")
async def redeploy_run(job_id: str, body: RedeployRequest, request: Request):
    """Trigger a live Cloud Run deployment for an already-built job workspace."""
    job = _require_job_owner(job_id, request)
    ws = job_workspace_dir(job_id)
    if not ws.exists():
        raise HTTPException(status_code=404, detail="Workspace files not found for this job")

    if body.gcp_project and job.get("session_id"):
        oauth_sessions.set_project(job["session_id"], body.gcp_project)

    jobs.set_status(job_id, "deploying")
    jobs.append_event(job_id, {"author": "system", "control": "deploying"})
    asyncio.create_task(run_deploy_phase(job_id, body.gcp_project, body.gcp_region))
    return {"status": "deploying"}


@app.get("/api/runs/{job_id}/history")
def get_run_history(job_id: str, request: Request):
    """Return non-partial events for a job so completed runs can render their full agent execution log."""
    _require_job_owner(job_id, request)
    events = [ev for _seq, ev in jobs.get_events_after(job_id, 0) if not ev.get("partial")]
    return {"events": events}


@app.get("/api/runs/{job_id}/events")
async def stream_run_events(job_id: str, request: Request, after: int = 0):
    _require_job_owner(job_id, request)

    last_event_id = request.headers.get("last-event-id")
    since_seq = int(last_event_id) if last_event_id else after

    async def event_generator():
        cursor = since_seq
        # SSE comment lines keep proxies (Cloud Run drops idle streams at ~60s)
        # from killing the connection during long, quiet steps like `npm install`.
        HEARTBEAT_SECONDS = 15
        idle = 0.0
        while True:
            # Stop tailing the moment the client goes away - otherwise a closed
            # tab (especially one abandoned at the approval gate, which is not a
            # terminal status) leaves this coroutine polling SQLite forever.
            if await request.is_disconnected():
                return

            sent_any = False
            for seq, event in jobs.get_events_after(job_id, cursor):
                cursor = seq
                sent_any = True
                yield f"id: {seq}\ndata: {json.dumps(event)}\n\n"
                if event.get("control") == "done":
                    return

            job = jobs.get_job(job_id)
            if job and job["status"] in jobs.TERMINAL_STATUSES:
                return

            if sent_any:
                idle = 0.0
            else:
                idle += 0.3
                if idle >= HEARTBEAT_SECONDS:
                    idle = 0.0
                    yield ": keep-alive\n\n"

            await asyncio.sleep(0.3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )

@app.get("/api/workspace/files")
def list_workspace_files(request: Request, job_id: Optional[str] = None):
    base = _resolve_workspace_base(request, job_id)
    if base is None or not base.exists():
        return {"files": []}

    def get_tree(dir_path: Path) -> list:
        tree = []
        for item in dir_path.iterdir():
            if item.name in [".git", "node_modules", ".next", "dist", "__pycache__"]:
                continue
            relative = item.relative_to(base).as_posix()
            if item.is_dir():
                tree.append({
                    "name": item.name,
                    "path": relative,
                    "isDir": True,
                    "children": get_tree(item)
                })
            else:
                tree.append({
                    "name": item.name,
                    "path": relative,
                    "isDir": False
                })
        return tree

    return {"files": get_tree(base)}

@app.get("/api/workspace/file")
def read_file_content(path: str, request: Request, job_id: Optional[str] = None):
    base = _resolve_workspace_base(request, job_id)
    if base is None:
        raise HTTPException(status_code=404, detail="File not found")
    full_path = (base / path).resolve()
    if not _is_within(full_path, base):
        raise HTTPException(status_code=403, detail="Invalid path")
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = full_path.read_text(encoding="utf-8")
        return {"content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/gcloud/status")
def gcloud_status(request: Request, response: Response):
    session_id = get_session_id(request)
    set_session_cookie(response, session_id)
    session = oauth_sessions.get_session(session_id)
    is_authenticated = bool(session and session.get("access_token"))
    return {
        "is_authenticated": is_authenticated,
        "project": session.get("gcp_project") if session else None,
        "email": session.get("email") if session else None
    }


class ProjectSelection(BaseModel):
    project: Optional[str] = None


@app.post("/api/gcloud/project")
def gcloud_set_project(body: ProjectSelection, request: Request, response: Response):
    session_id = get_session_id(request)
    set_session_cookie(response, session_id)
    oauth_sessions.set_project(session_id, body.project)
    return {"project": body.project or None}

@app.get("/api/gcloud/projects")
def gcloud_projects(request: Request):
    if not GOOGLE_OAUTH_CLIENT_ID or not GOOGLE_OAUTH_CLIENT_SECRET:
        return []
    session_id = get_session_id(request)
    access_token = oauth_sessions.get_valid_access_token(session_id, GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET)
    if not access_token:
        return []
    try:
        res = requests.get(
            "https://cloudresourcemanager.googleapis.com/v1/projects",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if res.status_code != 200:
            return []
        projects = res.json().get("projects", [])
        return [
            {"id": p["projectId"], "name": p.get("name", p["projectId"])}
            for p in projects if p.get("lifecycleState") == "ACTIVE"
        ]
    except Exception:
        return []

@app.get("/api/gcloud/oauth/login")
def gcloud_oauth_login(request: Request):
    if not GOOGLE_OAUTH_CLIENT_ID or not GOOGLE_OAUTH_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured on the server (missing GOOGLE_OAUTH_CLIENT_ID/SECRET)")

    session_id = get_session_id(request)
    client = OAuth2Session(
        GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET,
        scope="openid email https://www.googleapis.com/auth/cloud-platform",
        redirect_uri=OAUTH_REDIRECT_URI
    )
    auth_url, state = client.create_authorization_url(
        "https://accounts.google.com/o/oauth2/v2/auth",
        access_type="offline",
        prompt="consent"
    )
    oauth_sessions.set_pending_state(session_id, state)

    redirect = RedirectResponse(auth_url)
    set_session_cookie(redirect, session_id)
    return redirect

@app.get("/api/gcloud/oauth/callback")
def gcloud_oauth_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    if error or not code or not state:
        return RedirectResponse(f"{FRONTEND_BASE_URL}/?gcp_error=1")

    session_id = oauth_sessions.consume_state(state)
    if session_id is None:
        return RedirectResponse(f"{FRONTEND_BASE_URL}/?gcp_error=1")

    client = OAuth2Session(
        GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET,
        redirect_uri=OAUTH_REDIRECT_URI
    )
    token = client.fetch_token("https://oauth2.googleapis.com/token", code=code)

    email = None
    try:
        userinfo = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {token['access_token']}"}
        )
        if userinfo.status_code == 200:
            email = userinfo.json().get("email")
    except Exception:
        pass

    oauth_sessions.save_tokens(
        session_id, token["access_token"], token.get("refresh_token"), token.get("expires_in"), email
    )

    # Auto-select a default active GCP project (preferring agents-461209) if none is saved yet
    existing_session = oauth_sessions.get_session(session_id)
    if not (existing_session and existing_session.get("gcp_project")):
        try:
            proj_res = requests.get(
                "https://cloudresourcemanager.googleapis.com/v1/projects",
                headers={"Authorization": f"Bearer {token['access_token']}"},
                timeout=10,
            )
            if proj_res.status_code == 200:
                active_ids = [
                    p["projectId"]
                    for p in proj_res.json().get("projects", [])
                    if p.get("lifecycleState") == "ACTIVE" and p.get("projectId")
                ]
                chosen = "agents-461209" if "agents-461209" in active_ids else (active_ids[0] if active_ids else None)
                if chosen:
                    oauth_sessions.set_project(session_id, chosen)
        except Exception:
            pass

    redirect = RedirectResponse(f"{FRONTEND_BASE_URL}/?gcp_connected=1")
    set_session_cookie(redirect, session_id)
    return redirect

@app.post("/api/gcloud/oauth/logout")
def gcloud_oauth_logout(request: Request, response: Response):
    session_id = get_session_id(request)
    set_session_cookie(response, session_id)
    # Revoke the grant with Google, not just locally, then wipe the row.
    oauth_sessions.revoke_and_clear(session_id)
    return {"status": "success"}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    """Full sign-out: revoke the Google grant and drop the session cookie, so
    the browser gets a fresh identity that no longer owns the prior jobs.

    Jobs and their workspaces are left in place (a non-owner just can't see
    them); this is an end-of-session action, not a data wipe.
    """
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        oauth_sessions.revoke_and_clear(session_id)
    response.delete_cookie(
        SESSION_COOKIE_NAME, path="/",
        samesite=COOKIE_SAMESITE, secure=COOKIE_SECURE, httponly=True,
    )
    return {"status": "ok"}

@app.get("/api/gcloud/regions")
def gcloud_regions():
    return [
        {"id": "us-central1", "name": "us-central1 (Iowa)"},
        {"id": "us-east1", "name": "us-east1 (South Carolina)"},
        {"id": "europe-west1", "name": "europe-west1 (Belgium)"},
        {"id": "europe-west4", "name": "europe-west4 (Netherlands)"},
        {"id": "asia-east1", "name": "asia-east1 (Taiwan)"},
        {"id": "asia-northeast1", "name": "asia-northeast1 (Tokyo)"}
    ]

# Ensure the per-job workspace root exists.
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

# Read-only static mount over the workspace root (paths are /preview/<job_id>/...).
if os.path.exists(WORKSPACE_ROOT):
    app.mount("/preview", StaticFiles(directory=str(WORKSPACE_ROOT), html=True), name="preview")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)
