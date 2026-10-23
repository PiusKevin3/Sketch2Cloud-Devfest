import contextvars
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional, Sequence

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
if os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
elif os.getenv("GOOGLE_API_KEY") and not os.getenv("GEMINI_API_KEY"):
    os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]

from pydantic import BaseModel, Field
from google import genai
from google.genai import types as genai_types
from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.antigravity import (
    Agent,
    BuiltinTools,
    CapabilitiesConfig,
    Image,
    LocalAgentConfig,
    SystemInstructionSection,
    TemplatedSystemInstructions,
    ToolContext,
    types as agy_types,
)
from google.antigravity.hooks import hooks, policy

# Define paths. Repo-relative by default (this file is <repo>/backend/agents/
# agent.py, so parents[2] is the repo root); override with S2C_WORKSPACE_ROOT
# for containers / alternate deployments.
WORKSPACE_ROOT = Path(os.getenv("S2C_WORKSPACE_ROOT") or (Path(__file__).resolve().parents[2] / "workspace"))
DEFAULT_WORKSPACE_DIR = WORKSPACE_ROOT / "generated_app"
# Backward-compatible alias, still used for the read-only /preview static mount.
WORKSPACE_DIR = DEFAULT_WORKSPACE_DIR

# Single source of truth for the LLM model id, overridable without code changes.
# `gemini-3.5-flash` is a current stable model; bump to `gemini-3.7-flash` (also
# stable) via the env var when you want the newer one.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

# Hard ceiling on build/self-heal iterations so a bad generation can't loop forever.
MAX_BUILD_ATTEMPTS = 4
# Generous per-command ceiling: a cold `npm ci` + `next build` routinely needs
# several minutes, so the old 120s cap turned normal installs into "build failures".
COMMAND_TIMEOUT_SECONDS = 600

# A cold `gcloud run deploy --source .` (upload -> Cloud Build -> Artifact
# Registry push -> service create) commonly takes 3-6 min. Kept under
# main.DEPLOY_PHASE_TIMEOUT. Override with S2C_DEPLOY_TIMEOUT.
DEPLOY_TIMEOUT_SECONDS = int(os.getenv("S2C_DEPLOY_TIMEOUT", "600"))
API_ENABLE_TIMEOUT_SECONDS = int(os.getenv("S2C_API_ENABLE_TIMEOUT", "240"))

# Google Cloud APIs a source deploy to Cloud Run needs. Enabled on the target
# project at deploy time using the user's OAuth token, so the only manual GCP
# step left is creating the OAuth client. (serviceusage.googleapis.com, needed
# to enable the rest, is on by default for every project.)
REQUIRED_GCP_APIS = (
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
)

# Per-run context variables so custom tools invoked by the Antigravity SDK
# always resolve the active job's isolated workspace and build-attempt counter.
_ACTIVE_WORKSPACE_VAR: contextvars.ContextVar[Optional[Path]] = contextvars.ContextVar(
    "s2c_active_workspace", default=None
)
_ACTIVE_RUN_STATE_VAR: contextvars.ContextVar[Optional[dict[str, Any]]] = contextvars.ContextVar(
    "s2c_active_run_state", default=None
)


def _get_state_value(tool_context: Optional[Any], key: str, default: Any = None) -> Any:
    """Read a state value from Antigravity ToolContext, legacy state dict, or active run state."""
    if tool_context is not None:
        if hasattr(tool_context, "get_state"):
            try:
                val = tool_context.get_state(key)
                if val is not None:
                    return val
            except Exception:
                pass
        if hasattr(tool_context, "state") and isinstance(tool_context.state, dict):
            val = tool_context.state.get(key)
            if val is not None:
                return val
    run_state = _ACTIVE_RUN_STATE_VAR.get()
    if isinstance(run_state, dict) and key in run_state:
        return run_state[key]
    return default


def _set_state_value(tool_context: Optional[Any], key: str, value: Any) -> None:
    """Store a state value in Antigravity ToolContext, legacy state dict, and active run state."""
    if tool_context is not None:
        if hasattr(tool_context, "set_state"):
            try:
                tool_context.set_state(key, value)
            except Exception:
                pass
        if hasattr(tool_context, "state") and isinstance(tool_context.state, dict):
            try:
                tool_context.state[key] = value
            except Exception:
                pass
    run_state = _ACTIVE_RUN_STATE_VAR.get()
    if isinstance(run_state, dict):
        run_state[key] = value


def _workspace_dir(tool_context: Optional[ToolContext] = None) -> Path:
    """Per-job workspace directory, read from session/context state.

    main.py seeds ``workspace_dir`` with ``workspace/<job_id>`` so concurrent
    pipeline runs never write over each other. Falls back to the shared
    directory when unset (ad-hoc local runs).
    """
    wd = _get_state_value(tool_context, "workspace_dir")
    if not wd:
        wd = _ACTIVE_WORKSPACE_VAR.get()
    base = Path(wd) if wd else DEFAULT_WORKSPACE_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe_target(base: Path, rel_path: str) -> Path:
    """Resolve ``rel_path`` under ``base`` and refuse anything that escapes it."""
    base_resolved = base.resolve()
    full = (base_resolved / rel_path).resolve()
    if not full.is_relative_to(base_resolved):
        raise ValueError(f"Path '{rel_path}' resolves outside the workspace")
    return full


# =============================================================================
# Custom Workspace Tools (registered with Google Antigravity SDK)
# =============================================================================

def write_workspace_file(
    path: str,
    content: str,
    tool_context: Optional[ToolContext] = None,
) -> dict:
    """Writes content to a file inside the generated application workspace.

    Args:
        path: The relative path of the file (e.g. 'src/app/page.tsx', 'package.json', 'main.py').
        content: The text content of the file.

    Returns:
        A dictionary with a status message.
    """
    try:
        base = _workspace_dir(tool_context)
        full_path = _safe_target(base, path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        return {"status": "success", "message": f"Successfully wrote file to {path}"}
    except Exception as e:
        return {"status": "error", "message": f"Failed to write file {path}: {str(e)}"}


def read_workspace_file(
    path: str,
    tool_context: Optional[ToolContext] = None,
) -> dict:
    """Reads the content of a file inside the generated application workspace.

    Args:
        path: The relative path of the file (e.g. 'src/app/page.tsx').

    Returns:
        A dictionary with the file content or an error message.
    """
    try:
        base = _workspace_dir(tool_context)
        full_path = _safe_target(base, path)
        if not full_path.exists():
            return {"status": "error", "message": f"File {path} does not exist"}
        content = full_path.read_text(encoding="utf-8")
        return {"status": "success", "content": content}
    except Exception as e:
        return {"status": "error", "message": f"Failed to read file {path}: {str(e)}"}


def run_workspace_command(
    command: str,
    tool_context: Optional[ToolContext] = None,
) -> dict:
    """Runs a terminal command in the generated application workspace (e.g. 'npm ci' or 'npm run build').

    Args:
        command: The shell command to run.

    Returns:
        A dictionary with the command stdout, stderr, and exit code. Build
        commands are capped at MAX_BUILD_ATTEMPTS per pipeline run; a command
        that exceeds the time limit comes back with status 'timed_out' (an
        infrastructure signal, not a code defect).
    """
    base = _workspace_dir(tool_context)

    is_build = bool(re.search(r"(npm|pnpm|yarn)\s+(run\s+)?build|next\s+build", command))
    if is_build:
        try:
            attempts = int(_get_state_value(tool_context, "build_attempts", 0) or 0) + 1
        except Exception:
            attempts = 1
        _set_state_value(tool_context, "build_attempts", attempts)
        if attempts > MAX_BUILD_ATTEMPTS:
            return {
                "status": "error",
                "exit_code": None,
                "stdout": "",
                "stderr": (
                    f"Build attempt limit reached ({MAX_BUILD_ATTEMPTS}). Stop the "
                    f"self-heal loop now and summarise the outstanding errors in your "
                    f"testing report instead of re-running the build."
                ),
            }

    try:
        base.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(base),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        return {
            "status": "success" if result.returncode == 0 else "error",
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired as e:
        def _decode(v):
            if not v:
                return ""
            return v if isinstance(v, str) else v.decode("utf-8", "replace")

        return {
            "status": "timed_out",
            "exit_code": None,
            "stdout": _decode(e.stdout),
            "stderr": _decode(e.stderr) or f"Command exceeded {COMMAND_TIMEOUT_SECONDS}s and was terminated.",
            "message": (
                f"'{command}' timed out after {COMMAND_TIMEOUT_SECONDS}s. This is an "
                f"infrastructure limit, not necessarily a code defect - retry once, then "
                f"note it in the report and move on."
            ),
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to run command: {str(e)}"}


def deploy_workspace_app(
    workspace_dir: Path,
    access_token: Optional[str],
    project: Optional[str],
    region: str = "us-central1",
) -> dict:
    """Deploy the generated app in ``workspace_dir`` to Google Cloud Run.

    Uses the access token the end user connected via OAuth (never the host
    machine's own ``gcloud`` session), passed to ``gcloud`` per-invocation via
    ``--access-token-file``. With no token or no project it performs a mock
    deployment and returns a placeholder URL.

    The required Google Cloud APIs are enabled on the target project here, at
    deploy time, so the only manual GCP step a user has to do is create the
    OAuth client. (Billing still has to be enabled on the project - that can't
    be done with a user token.)

    This is a plain, deterministic function - the deployment step does not need
    an LLM turn - called directly from the backend's deploy phase.

    Returns a dict with ``status`` ("success"/"error"), ``mode`` ("real"/"mock")
    and, on success, ``url``.
    """
    workspace_dir = Path(workspace_dir)

    if not access_token or not project:
        # No connected Google Cloud account / no project selected - mock deployment.
        import random
        service_id = f"sketch2cloud-app-{random.randint(1000, 9999)}"
        return {
            "status": "success",
            "mode": "mock",
            "url": f"https://{service_id}-uc.a.run.app",
            "message": "No connected Google Cloud account or project selected - deployed in simulated Cloud Run sandbox.",
        }

    gcloud_bin = shutil.which("gcloud") or "gcloud"  # gcloud.cmd on Windows
    service = "sketch2cloud-gen"

    # Pin the interpreter gcloud (and its child processes) use. On Windows a bare
    # `python` otherwise resolves to the Microsoft Store stub ("Python was not
    # found...") and gcloud helpers fail. sys.executable is this venv's python.
    gcloud_env = {**os.environ, "CLOUDSDK_PYTHON": sys.executable}

    token_file = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".token", delete=False) as f:
            f.write(access_token)
            token_file = f.name

        # --project/--token/--quiet apply to every gcloud call; --region is
        # only valid on the Cloud Run subcommands.
        auth_flags = [
            "--project", project,
            "--access-token-file", token_file,
            "--quiet",  # non-interactive: auto-accept the Artifact Registry repo prompt
        ]
        run_flags = [*auth_flags, "--region", region]

        # 1. Enable the APIs a source deploy needs (no-op when already on).
        enable = subprocess.run(
            [gcloud_bin, "services", "enable", *REQUIRED_GCP_APIS, *auth_flags],
            cwd=str(workspace_dir),
            env=gcloud_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=API_ENABLE_TIMEOUT_SECONDS,
        )
        if enable.returncode != 0:
            enable_out = ((enable.stdout or "") + (enable.stderr or "")).strip()
            return {
                "status": "error",
                "mode": "real",
                "message": "Could not enable the required Google Cloud APIs "
                           "(run, cloudbuild, artifactregistry, cloudresourcemanager). "
                           "Usually this means the project has no billing account linked, "
                           "or your account lacks serviceusage.services.enable. "
                           + enable_out[-1200:],
            }

        # 2. Deploy. No shell=True: project/region are user-supplied. stdin is
        # closed so a stray prompt fails fast instead of hanging until timeout.
        result = subprocess.run(
            [gcloud_bin, "run", "deploy", service, "--source", ".",
             "--allow-unauthenticated", *run_flags],
            cwd=str(workspace_dir),
            env=gcloud_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=DEPLOY_TIMEOUT_SECONDS,
        )
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        if result.returncode != 0:
            return {
                "status": "error",
                "mode": "real",
                "message": f"gcloud run deploy failed (exit {result.returncode}). "
                           + combined.strip()[-1500:],
            }

        match = re.search(r"https://\S+\.run\.app\S*", combined)
        live_url = match.group(0).rstrip(".,)") if match else None
        if not live_url:
            # Fall back to querying the service for its URL.
            describe = subprocess.run(
                [gcloud_bin, "run", "services", "describe", service,
                 "--format", "value(status.url)", *run_flags],
                cwd=str(workspace_dir), env=gcloud_env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60,
            )
            if describe.returncode == 0:
                live_url = describe.stdout.strip() or None

        return {
            "status": "success",
            "mode": "real",
            "url": live_url,
            "message": "Application deployed to Google Cloud Run.",
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "mode": "real",
                "message": f"gcloud run deploy exceeded {DEPLOY_TIMEOUT_SECONDS}s and was terminated."}
    except FileNotFoundError:
        return {"status": "error", "mode": "real",
                "message": "The `gcloud` CLI was not found on PATH."}
    except Exception as e:
        return {"status": "error", "mode": "real", "message": f"Real deployment failed: {e}"}
    finally:
        if token_file:
            try:
                os.unlink(token_file)
            except OSError:
                pass


# =============================================================================
# Google Antigravity SDK Governance: Policies & Lifecycle Hooks
# =============================================================================

_FORBIDDEN_COMMAND_PATTERNS = re.compile(
    r"(^|\s|;|&|\|)(sudo|rm\s+-rf\s+/|gcloud\s+run\s+deploy|shutdown|reboot|mkfs|dd\s+if=|format\s+[a-zA-Z]:)(\s|$)",
    re.IGNORECASE,
)


def _is_forbidden_command(tool_call: agy_types.ToolCall) -> bool:
    """Predicate used by Antigravity policy.deny to block unsafe shell commands.

    Note: Deployment to Cloud Run is strictly gated behind the human approval
    step in main.py, so agents are never allowed to invoke `gcloud run deploy`
    directly during the build/test loop.
    """
    cmd = str(tool_call.args.get("command") or tool_call.args.get("CommandLine") or "")
    return bool(_FORBIDDEN_COMMAND_PATTERNS.search(cmd))


def build_workspace_policies(workspace_dir: Path) -> list:
    """Build declarative Google Antigravity SDK safety policies for a job workspace."""
    ws_path = str(Path(workspace_dir).resolve())
    deny_reason = (
        "Destructive shell commands and direct gcloud deployments are blocked by "
        "Sketch2Cloud governance policy. Cloud Run deployment requires human approval."
    )
    return [
        # 1. Restrict built-in file tools strictly to the isolated per-job workspace.
        policy.workspace_only([ws_path]),
        # 2. Block destructive or privilege-escalating commands and direct deploys.
        policy.deny(
            "run_workspace_command",
            when=_is_forbidden_command,
            reason=deny_reason,
        ),
        policy.deny(
            BuiltinTools.RUN_COMMAND,
            when=_is_forbidden_command,
            reason=deny_reason,
        ),
        # 3. Allow safe workspace file and build verification tools.
        policy.allow("write_workspace_file"),
        policy.allow("read_workspace_file"),
        policy.allow("run_workspace_command"),
        policy.allow(BuiltinTools.VIEW_FILE),
        policy.allow(BuiltinTools.LIST_DIR),
        policy.allow(BuiltinTools.CREATE_FILE),
        policy.allow(BuiltinTools.EDIT_FILE),
    ]


@hooks.on_tool_error
def _recover_workspace_tool_error(ctx: hooks.HookContext, error: agy_types.ToolResult) -> agy_types.ToolResult:
    """Antigravity lifecycle hook: convert unexpected tool exceptions into structured diagnostics."""
    return agy_types.ToolResult(
        id=error.id,
        name=error.name,
        result={
            "status": "error",
            "message": f"Tool '{error.name}' encountered an error: {error.error}. Inspect the path/arguments and retry.",
        },
    )


def build_pipeline_hooks() -> list:
    """Return the Google Antigravity SDK lifecycle hooks attached to each agent."""
    return [_recover_workspace_tool_error]


# =============================================================================
# Google Antigravity SDK Agent Stage Definitions
# =============================================================================

@dataclasses.dataclass
class AntigravityStageAgent:
    """Defines a specialized Sketch2Cloud agent stage powered by Google Antigravity SDK."""

    name: str
    description: str
    instruction: str
    model: str = GEMINI_MODEL
    tools: list[Callable[..., Any]] = dataclasses.field(default_factory=list)
    output_key: Optional[str] = None

    def build_system_instructions(self) -> TemplatedSystemInstructions:
        """Construct structured Antigravity TemplatedSystemInstructions for this agent."""
        return TemplatedSystemInstructions(
            identity=(
                f"You are `{self.name}` in the Sketch2Cloud autonomous software engineering pipeline. "
                f"{self.description}"
            ),
            sections=[
                SystemInstructionSection(
                    title="role_and_responsibilities",
                    content=self.instruction.strip(),
                ),
                SystemInstructionSection(
                    title="governance_and_safety",
                    content=(
                        "Operate strictly within the isolated job workspace using your registered tools. "
                        "Never attempt to deploy to Google Cloud directly; Cloud Run deployment is "
                        "gated behind a mandatory human approval step after build verification."
                    ),
                ),
            ],
        )

    def to_subagent_config(self, workspace_dir: Optional[Path] = None) -> agy_types.SubagentConfig:
        """Convert this stage definition into an Antigravity SubagentConfig."""
        return agy_types.SubagentConfig(
            name=self.name,
            description=self.description,
            system_instructions=self.build_system_instructions(),
            tools=list(self.tools),
            model=self.model,
            capabilities=agy_types.SubagentCapabilities(
                enabled_tools=BuiltinTools.none(),
            ),
        )

    def to_local_agent_config(self, workspace_dir: Optional[Path] = None) -> LocalAgentConfig:
        """Build a LocalAgentConfig for running this agent with `google.antigravity.Agent`."""
        ws = (workspace_dir or DEFAULT_WORKSPACE_DIR).resolve()
        ws.mkdir(parents=True, exist_ok=True)
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        return LocalAgentConfig(
            system_instructions=self.build_system_instructions(),
            tools=list(self.tools),
            policies=build_workspace_policies(ws),
            hooks=build_pipeline_hooks(),
            workspaces=[str(ws)],
            model=self.model,
            api_key=api_key,
            capabilities=CapabilitiesConfig(
                enable_subagents=False,
                enabled_tools=BuiltinTools.none(),
            ),
            budget_config=agy_types.BudgetConfig(max_model_calls=25, max_tool_calls=40),
        )


@dataclasses.dataclass
class AntigravityPipelineOrchestrator:
    """Sequential multi-agent pipeline powered by the Google Antigravity SDK."""

    name: str
    description: str
    sub_agents: list[AntigravityStageAgent]

    def to_subagent_configs(self, workspace_dir: Optional[Path] = None) -> list[agy_types.SubagentConfig]:
        """Return Antigravity SubagentConfig objects for every stage in the pipeline."""
        return [stage.to_subagent_config(workspace_dir) for stage in self.sub_agents]


# =============================================================================
# Google ADK Sketch Comprehension Agent (Stage 0 / Stage 1 Vision & Layout)
# =============================================================================

class SketchDetectedComponent(BaseModel):
    name: str = Field(description="Name of the UI section or component (e.g. 'Top Navigation Bar', 'Hero Showcase Banner').")
    region: str = Field(description="Spatial position in the layout (e.g. 'Top Header', 'Left Sidebar', 'Main Canvas', '3-Column Grid', 'Footer').")
    component_type: str = Field(description="Component category: navbar, sidebar, hero, card-grid, chart, table, form, modal, cta, or footer.")
    details: str = Field(description="Detailed description of visual layout, interactive behavior, and content in this region.")


class SketchComprehensionResult(BaseModel):
    app_title: str = Field(description="Suggested polished title for the application based on the sketch.")
    app_category: str = Field(description="Application category (e.g. 'Analytics Dashboard', 'SaaS Landing & Pricing', 'Creative Portfolio & Showcase', 'Kanban Workflow Studio').")
    comprehension_summary: str = Field(description="Clear 2-3 sentence summary explaining what the AI sees in the uploaded sketch and its overall purpose.")
    layout_structure: str = Field(description="High-level spatial layout description (e.g. 'Sticky top navbar + split hero banner + 3-column feature card grid + interactive data section').")
    detected_text_labels: list[str] = Field(description="Exact text labels, headings, or handwritten annotations read directly from the sketch.")
    detected_components: list[SketchDetectedComponent] = Field(description="Ordered list of layout sections and UI components identified in the sketch from top to bottom.")
    layout_conclusions: list[str] = Field(description="Actionable architectural & UI layout conclusions drawn from the sketch to hand off to the Planner and UI Builder agents.")
    style_direction: str = Field(description="Recommended color palette, typography, spacing, and visual styling cues derived from the sketch.")
    suggested_backend: list[str] = Field(description="Recommended FastAPI REST endpoints (/api/...) and SQLite data models to power the interactive components.")
    synthesized_prompt: str = Field(description="A comprehensive, production-ready prompt combining the sketch layout conclusions, component hierarchy, and backend requirements.")


ADK_SKETCH_INTERPRETER_INSTRUCTION = """
You are the Sketch2Cloud Google ADK Sketch Comprehension Agent (`adk_sketch_interpreter_agent`).
Your job is to inspect an uploaded hand-drawn wireframe, UI sketch, whiteboard drawing, or screenshot,
understand its visual structure, draw concrete layout and design conclusions, and prepare a rich
architectural specification for the next phase of the pipeline.

Follow these rules carefully:
1. Inspect every box, container, navigation bar, sidebar, image placeholder, card grid, table, chart, button, and annotation in the image.
2. Record the raw text/handwritten labels visible in the sketch inside `detected_text_labels` so the user can verify you read their handwriting accurately.
3. IMPORTANT — Translate generic wireframe placeholder words (such as "MY WEBSITE", "COOL IMAGE", "LOGO", "TEXT HERE", "TITLE", "BOX 1") into realistic, domain-appropriate, production-grade UI sections and content in your `layout_conclusions` and `synthesized_prompt`. Never instruct downstream agents to render literal placeholder words like "COOL IMAGE" as final copy; explain what visual component belongs in that slot (e.g. an interactive hero showcase card with live metrics, media preview, and filterable gallery).
4. Draw clear, specific `layout_conclusions` covering:
   - Page grid/flexbox structure (header height, sidebar width, hero split, number of columns in card grids).
   - Interactive states (search/filter, tabs, modals, CRUD actions, toggles).
   - Data flow between the Next.js frontend and FastAPI `/api/*` endpoints.
5. Produce a rich `synthesized_prompt` that the user can review and edit before launching the build pipeline.
"""

adk_sketch_interpreter_agent = LlmAgent(
    name="adk_sketch_interpreter_agent",
    model=GEMINI_MODEL,
    description="Google ADK multimodal agent that interprets uploaded UI sketches and draws structured layout conclusions before code generation.",
    instruction=ADK_SKETCH_INTERPRETER_INSTRUCTION,
    output_schema=SketchComprehensionResult,
    output_key="sketch_comprehension",
)


def format_sketch_comprehension_markdown(analysis: dict[str, Any]) -> str:
    """Format an ADK sketch comprehension dictionary into readable Markdown + `### Layout Schema` JSON."""
    title = analysis.get("app_title") or "Uploaded Sketch Application"
    category = analysis.get("app_category") or "Web Application"
    summary = analysis.get("comprehension_summary") or ""
    layout_structure = analysis.get("layout_structure") or ""
    labels = analysis.get("detected_text_labels") or []
    components = analysis.get("detected_components") or []
    conclusions = analysis.get("layout_conclusions") or []
    style_dir = analysis.get("style_direction") or ""
    backend_list = analysis.get("suggested_backend") or []

    lines: list[str] = [
        f"## Google ADK Sketch Comprehension: {title} ({category})",
        "",
        f"**Overview:** {summary}",
        "",
        f"**Spatial Layout Structure:** {layout_structure}",
    ]

    if labels:
        lines.append("")
        lines.append("**Detected Sketch Labels & Annotations:** " + ", ".join(f"`{lbl}`" for lbl in labels))

    if components:
        lines.append("")
        lines.append("### Detected Wireframe Regions & Components")
        for idx, comp in enumerate(components, 1):
            c_name = comp.get("name", f"Section {idx}")
            c_region = comp.get("region", "Main")
            c_type = comp.get("component_type", "section")
            c_details = comp.get("details", "")
            lines.append(f"{idx}. **{c_name}** (`{c_region}` · `{c_type}`): {c_details}")

    if conclusions:
        lines.append("")
        lines.append("### ADK Layout & Design Conclusions (Handoff to Planner & UI Builder)")
        for item in conclusions:
            lines.append(f"- {item}")

    if style_dir:
        lines.append("")
        lines.append(f"**Visual & Theme Direction:** {style_dir}")

    if backend_list:
        lines.append("")
        lines.append("### Implied FastAPI Endpoints & Data Models")
        for ep in backend_list:
            lines.append(f"- {ep}")

    schema_payload = {
        "app_title": title,
        "app_category": category,
        "layout_structure": layout_structure,
        "detected_text_labels": labels,
        "detected_components": components,
        "layout_conclusions": conclusions,
        "style_direction": style_dir,
        "suggested_backend": backend_list,
    }
    lines.extend([
        "",
        "### Layout Schema",
        "```json",
        json.dumps(schema_payload, indent=2),
        "```",
    ])
    return "\n".join(lines)


async def analyze_sketch_with_adk(
    image_bytes: bytes,
    mime_type: str = "image/png",
    user_hint: str = "",
) -> dict[str, Any]:
    """Run the Google ADK `adk_sketch_interpreter_agent` on an uploaded sketch image.

    Returns a dictionary matching `SketchComprehensionResult` plus `markdown_report`.
    """
    prompt_text = (
        "Analyze this uploaded UI sketch / wireframe image carefully using Google ADK Vision. "
        "Identify every drawn layout box, navigation bar, hero section, card grid, widget, and handwritten label. "
        "Draw concrete layout and design conclusions for the next pipeline phase."
    )
    if user_hint and user_hint.strip():
        prompt_text += f"\n\nUser notes / context:\n{user_hint.strip()}"

    user_content = genai_types.Content(
        role="user",
        parts=[
            genai_types.Part(text=prompt_text),
            genai_types.Part(inline_data=genai_types.Blob(mime_type=mime_type, data=image_bytes)),
        ],
    )

    parsed_dict: Optional[dict[str, Any]] = None

    # 1. Primary path: Execute Google ADK LlmAgent via InMemoryRunner
    try:
        runner = InMemoryRunner(
            agent=adk_sketch_interpreter_agent,
            app_name="sketch2cloud_adk_vision",
        )
        session_id = f"adk_sketch_{uuid.uuid4().hex[:10]}"
        user_id = "studio_user"
        await runner.session_service.create_session(
            app_name="sketch2cloud_adk_vision",
            user_id=user_id,
            session_id=session_id,
        )
        collected_text: list[str] = []
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_content,
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if getattr(part, "text", None):
                        collected_text.append(part.text)

        raw_output = "".join(collected_text).strip()
        if raw_output:
            clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_output, flags=re.IGNORECASE).strip()
            parsed_obj = SketchComprehensionResult.model_validate_json(clean_json)
            parsed_dict = parsed_obj.model_dump()
    except Exception as adk_err:
        print(f"[adk_sketch_interpreter] ADK runner fallback triggered: {adk_err}")

    # 2. Resilient fallback via google.genai structured output if needed
    if not parsed_dict:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        client = genai.Client(api_key=api_key)
        resp = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_content,
            config=genai_types.GenerateContentConfig(
                system_instruction=ADK_SKETCH_INTERPRETER_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=SketchComprehensionResult,
            ),
        )
        parsed_obj = SketchComprehensionResult.model_validate_json(resp.text)
        parsed_dict = parsed_obj.model_dump()

    parsed_dict["markdown_report"] = format_sketch_comprehension_markdown(parsed_dict)
    return parsed_dict


vision_agent = AntigravityStageAgent(
    model=GEMINI_MODEL,
    name="vision_agent",
    description="Interprets uploaded sketches using Google ADK Vision and synthesizes layout conclusions for the build pipeline.",
    instruction="""
    You are the Vision & Sketch Comprehension Agent (powered by Google ADK + Gemini Vision).
    Your task is to analyze the user's sketch, wireframe, screenshot, or landing page layout
    and draw concrete layout conclusions for the Planner and UI Builder agents.

    1. Examine the image and any user instructions provided in the message.
    2. Extract:
       - All visible wireframe sections, navigation bars, hero banners, grids, sidebars, forms, and footers from top to bottom.
       - Exact handwritten or printed labels in the sketch, translating generic wireframe placeholder text (like 'MY WEBSITE' or 'COOL IMAGE') into rich, realistic UI components and content rather than literal placeholder words.
       - Concrete layout conclusions: grid column counts, spacing hierarchy, interactive states, color palette, and typography.
    3. Write a clear markdown explanation of your layout conclusions, followed by a structured JSON summary under '### Layout Schema'.
    """,
    output_key="layout_overview",
)

planner_agent = AntigravityStageAgent(
    model=GEMINI_MODEL,
    name="planner_agent",
    description="Structures the project plan based on the ADK Vision layout conclusions and schema.",
    instruction="""
    You are the Planner Agent. Your task is to structure the application plan from the ADK Sketch Comprehension & Layout Conclusions.

    1. Read the layout overview, detected wireframe components, and layout conclusions generated by the Vision Agent.
    2. Write a comprehensive `SPEC.md` file to the workspace using `write_workspace_file` AND output the markdown project plan detailing:
       - How each region from the uploaded sketch maps to Next.js components and layout containers.
       - State variables, interactive filters/modals, and realistic seed data (never use literal wireframe placeholder strings like 'MY WEBSITE' or 'COOL IMAGE' as final user-facing copy).
       - FastAPI backend endpoints (`/api/...`) and SQLite database schema structure.
    """,
    tools=[write_workspace_file],
    output_key="project_plan",
)

ui_agent = AntigravityStageAgent(
    model=GEMINI_MODEL,
    name="ui_agent",
    description="Generates the Next.js React and CSS frontend code matching the sketch layout conclusions and saves it to workspace files.",
    instruction="""
    You are the UI Builder Agent. Your task is to write high-fidelity Next.js + React components styled with Tailwind CSS that faithfully implement the ADK Sketch Layout Conclusions and the Planner's SPEC.md.

    1. Use the `write_workspace_file` tool to save components to disk.
    2. Create:
       - `package.json` with Next.js, React, Lucide-React, and Tailwind dependencies,
         and `"build": "next build"` / `"start": "next start"` scripts.
       - Tailwind and PostCSS configurations.
       - Next.js layout (`src/app/layout.tsx`) and main page (`src/app/page.tsx`).
       - Modular UI components in `src/components/` matching every section detected in the sketch.
    3. Faithfully preserve the spatial arrangement from the uploaded sketch (e.g. header nav, split hero, multi-column card grids, sidebars, tables, forms) while elevating it to a modern, polished, interactive UI with realistic content.
    4. When the UI calls the backend, always use relative paths (`fetch('/api/...')`) -
       never a hard-coded `http://localhost` URL. The deployment step co-locates the
       API behind the same origin and proxies `/api/*` to it.
    5. Do not write `next.config.*` when the app has a backend - the deployment step
       owns it (it injects the `/api` proxy). A `next.config` with no backend is fine.
    """,
    tools=[write_workspace_file],
    output_key="ui_code_summary",
)

backend_agent = AntigravityStageAgent(
    model=GEMINI_MODEL,
    name="backend_agent",
    description="Generates the FastAPI Python backend and saves it to workspace files.",
    instruction="""
    You are the Backend Agent. Your task is to build a robust Python FastAPI server.
    
    1. Use the `write_workspace_file` tool to save backend files.
    2. Create:
       - `api/main.py` with FastAPI, endpoint routes for the app, and database interactions (using SQLite/Pydantic).
       - Database helper/models (`api/db.py`).
       - `api/requirements.txt` listing every Python dependency you use (fastapi, uvicorn[standard], pydantic, ...).
    3. Every route must be mounted under the `/api` path prefix (e.g. `/api/items`), because
       the deployed app is served as one Cloud Run service that proxies `/api/*` to this server.
    4. Ensure endpoints match the backend features proposed in the Planner Agent's plan.
    """,
    tools=[write_workspace_file],
    output_key="backend_code_summary",
)

testing_agent = AntigravityStageAgent(
    model=GEMINI_MODEL,
    name="testing_agent",
    description="Tests the generated application and automatically self-heals any build errors.",
    instruction="""
    You are the Testing Agent. Your task is to verify that the generated code builds successfully.

    1. Use `run_workspace_command` to run `npm ci` (fall back to `npm install` if there is no lockfile), then `npm run build`.
    2. If the build fails with syntax errors, missing imports, or misconfiguration:
       - Read the error output.
       - Use `read_workspace_file` to inspect the problematic file.
       - Correct the code and overwrite it using `write_workspace_file`.
       - Re-run the build.
    3. If `run_workspace_command` returns status `timed_out`, that is an infrastructure limit,
       not a code defect - retry the command once, then move on. Do not "fix" code that only timed out.
    4. You get at most 4 build attempts. When they are exhausted (the tool will tell you), stop the
       loop and write a report listing the remaining errors instead of re-running the build.
    5. Finish by writing a concise testing report: whether the build passed and any known issues.
    """,
    tools=[run_workspace_command, read_workspace_file, write_workspace_file],
    output_key="testing_report",
)

# Deployment is deliberately NOT an LLM agent: after human approval the backend
# calls `deploy_workspace_app` directly (see main.run_deploy_phase). The step is
# a single deterministic `gcloud` invocation - an LLM turn added latency, cost
# and a refusal/hallucination failure mode for no benefit.

# Build-phase orchestrator: sketch understanding through build-verified code.
# Deployment is intentionally excluded so the backend can gate it behind a
# separate human-approval step before deploying on its own.
build_agent = AntigravityPipelineOrchestrator(
    name="sketch2cloud_build",
    description="Orchestrates the Sketch2Cloud build pipeline from ADK sketch comprehension to build-verified code using the Google Antigravity SDK.",
    sub_agents=[
        vision_agent,
        planner_agent,
        ui_agent,
        backend_agent,
        testing_agent,
    ],
)

# Refinement pass: the workspace already holds a built app; apply one targeted
# change in place and re-verify. Runs instead of the full build pipeline so a
# "make the header purple" request doesn't regenerate the whole project.
refine_agent = AntigravityStageAgent(
    model=GEMINI_MODEL,
    name="refine_agent",
    description="Applies a targeted change to the already-generated app in the workspace and re-verifies the build.",
    instruction="""
    You are the Refinement Agent. The workspace already contains a complete, previously
    build-verified application. The user has asked for ONE change (see the message).

    1. Use `read_workspace_file` to inspect only the files relevant to the request.
    2. Make the smallest edit that satisfies the request and save it with `write_workspace_file`.
       Do not rewrite files wholesale, do not restructure the project, and do not touch
       `Dockerfile`, `start.sh`, `.gcloudignore` or `next.config.mjs` (the pipeline owns those).
    3. Run `npm run build` with `run_workspace_command` to verify. Fix real build errors and
       re-run. `timed_out` is infrastructure, not a defect - retry once then move on. You get
       at most 4 build attempts; when they're exhausted, stop and report the remaining errors.
    4. Finish with a one-paragraph summary of exactly what you changed.
    """,
    tools=[read_workspace_file, write_workspace_file, run_workspace_command],
    output_key="refine_summary",
)


# =============================================================================
# Google Antigravity SDK Execution & Event Streaming
# =============================================================================

def _build_antigravity_prompt(
    stage: AntigravityStageAgent,
    user_message: str,
    image_path: Optional[str],
    prior_outputs: dict[str, str],
    include_image: bool,
) -> Any:
    """Assemble the multimodal or text prompt for an Antigravity stage agent."""
    context_blocks: list[str] = [f"User Request:\n{user_message}"]
    if prior_outputs:
        context_blocks.append("=== Outputs from Previous Pipeline Stages ===")
        for key, text in prior_outputs.items():
            if text:
                context_blocks.append(f"#### {key}\n{text}")
    full_prompt = "\n\n".join(context_blocks)

    if include_image and image_path and os.path.exists(image_path):
        try:
            return [full_prompt, Image.from_file(image_path)]
        except ValueError:
            # e.g. unsupported image mime such as GIF; fall back to raw bytes if needed
            img_bytes = Path(image_path).read_bytes()
            return [full_prompt, Image.from_bytes(img_bytes, mime_type="image/png")]
    return full_prompt


async def _stream_single_stage(
    stage: AntigravityStageAgent,
    prompt: Any,
    workspace_dir: Path,
    prior_outputs: dict[str, str],
) -> AsyncIterator[dict[str, Any]]:
    """Run a single `AntigravityStageAgent` via `google.antigravity.Agent` and yield frontend SSE events."""
    config = stage.to_local_agent_config(workspace_dir)
    accumulated_text: list[str] = []

    async with Agent(config) as agy_agent:
        response = await agy_agent.chat(prompt)
        async for chunk in response.chunks:
            if isinstance(chunk, agy_types.Text):
                if chunk.text:
                    accumulated_text.append(chunk.text)
                    yield {
                        "author": stage.name,
                        "partial": True,
                        "content": {"parts": [{"text": chunk.text}]},
                    }
            elif isinstance(chunk, agy_types.ToolCall):
                tool_name = chunk.name.value if isinstance(chunk.name, BuiltinTools) else str(chunk.name)
                yield {
                    "author": stage.name,
                    "partial": False,
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "name": tool_name,
                                    "args": chunk.args or {},
                                    "id": chunk.id,
                                }
                            }
                        ]
                    },
                }
            elif isinstance(chunk, agy_types.ToolResult):
                tool_name = chunk.name.value if isinstance(chunk.name, BuiltinTools) else str(chunk.name)
                resp_payload = chunk.result
                if chunk.error and not resp_payload:
                    resp_payload = {"status": "error", "message": chunk.error}
                elif not isinstance(resp_payload, dict):
                    resp_payload = {"status": "success", "result": resp_payload}
                yield {
                    "author": stage.name,
                    "partial": False,
                    "content": {
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": tool_name,
                                    "response": resp_payload,
                                    "id": chunk.id,
                                }
                            }
                        ]
                    },
                }

        final_text = "".join(accumulated_text).strip()
        if final_text:
            if stage.output_key:
                prior_outputs[stage.output_key] = final_text
            yield {
                "author": stage.name,
                "partial": False,
                "content": {"parts": [{"text": final_text}]},
                "actions": {"stateDelta": {stage.output_key: final_text}} if stage.output_key else None,
            }


async def run_antigravity_agent(
    agent_or_pipeline: AntigravityPipelineOrchestrator | AntigravityStageAgent,
    message: str,
    image_path: Optional[str] = None,
    workspace_dir: Optional[Path] = None,
    initial_state: Optional[dict[str, Any]] = None,
) -> AsyncIterator[dict[str, Any]]:
    """Execute a Sketch2Cloud build pipeline or single refinement agent.

    Stage 1 (`vision_agent`) uses the Google ADK `adk_sketch_interpreter_agent`
    (or the user-reviewed pre-pipeline ADK sketch comprehension) to draw structured
    layout conclusions and hand them off to the Google Antigravity SDK stages
    (`planner_agent` -> `ui_agent` -> `backend_agent` -> `testing_agent`).
    """
    ws = (workspace_dir or DEFAULT_WORKSPACE_DIR).resolve()
    ws.mkdir(parents=True, exist_ok=True)

    run_state: dict[str, Any] = dict(initial_state or {})
    run_state["workspace_dir"] = str(ws)
    run_state.setdefault("build_attempts", 0)

    ws_token = _ACTIVE_WORKSPACE_VAR.set(ws)
    state_token = _ACTIVE_RUN_STATE_VAR.set(run_state)
    try:
        stages: Sequence[AntigravityStageAgent]
        if isinstance(agent_or_pipeline, AntigravityPipelineOrchestrator):
            stages = agent_or_pipeline.sub_agents
        else:
            stages = [agent_or_pipeline]

        prior_outputs: dict[str, str] = {}
        precomputed_analysis: Optional[dict[str, Any]] = run_state.get("sketch_analysis")

        for idx, stage in enumerate(stages):
            # Stage 1: Use Google ADK Sketch Comprehension (pre-reviewed or live on uploaded sketch)
            if idx == 0 and stage.name == "vision_agent":
                adk_report: Optional[str] = None
                if isinstance(precomputed_analysis, dict) and precomputed_analysis:
                    adk_report = (
                        precomputed_analysis.get("markdown_report")
                        or format_sketch_comprehension_markdown(precomputed_analysis)
                    )
                elif image_path and os.path.exists(image_path):
                    try:
                        img_file = Path(image_path)
                        mime_map = {
                            ".png": "image/png",
                            ".jpg": "image/jpeg",
                            ".jpeg": "image/jpeg",
                            ".webp": "image/webp",
                            ".gif": "image/gif",
                        }
                        mime_type = mime_map.get(img_file.suffix.lower(), "image/png")
                        live_analysis = await analyze_sketch_with_adk(
                            image_bytes=img_file.read_bytes(),
                            mime_type=mime_type,
                            user_hint=message,
                        )
                        adk_report = live_analysis.get("markdown_report")
                    except Exception as adk_stage_err:
                        print(f"[vision_agent] ADK live sketch analysis fallback to Antigravity: {adk_stage_err}")

                if adk_report:
                    prior_outputs[stage.output_key or "layout_overview"] = adk_report
                    try:
                        (ws / "SKETCH_ANALYSIS.md").write_text(adk_report, encoding="utf-8")
                    except Exception:
                        pass
                    yield {
                        "author": "vision_agent",
                        "partial": False,
                        "content": {"parts": [{"text": adk_report}]},
                        "actions": {"stateDelta": {"layout_overview": adk_report}},
                    }
                    continue

            prompt = _build_antigravity_prompt(
                stage=stage,
                user_message=message,
                image_path=image_path,
                prior_outputs=prior_outputs,
                include_image=(stage.name in ("vision_agent", "ui_agent")),
            )
            async for event_dict in _stream_single_stage(
                stage=stage,
                prompt=prompt,
                workspace_dir=ws,
                prior_outputs=prior_outputs,
            ):
                yield event_dict
    finally:
        _ACTIVE_WORKSPACE_VAR.reset(ws_token)
        _ACTIVE_RUN_STATE_VAR.reset(state_token)

