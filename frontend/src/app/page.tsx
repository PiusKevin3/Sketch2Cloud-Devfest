"use client";

import React, { useState, useEffect, useRef } from "react";
import {
  Cloud,
  Upload,
  Play,
  Code,
  Terminal,
  FileText,
  ChevronRight,
  ChevronDown,
  Send,
  ExternalLink,
  RefreshCw,
  FolderOpen,
  ShieldCheck,
  Sparkles,
  CheckCircle2,
  AlertTriangle,
  Cpu,
  Layers,
  Copy,
  Check,
  Eye,
  Plus,
  Wrench,
  Rocket,
  Lock,
} from "lucide-react";

interface PipelineStep {
  name: string;
  shortName: string;
  agent: string;
  description: string;
  status: "idle" | "running" | "success" | "error";
  log: string[];
}

interface SketchDetectedComponent {
  name: string;
  region: string;
  component_type: string;
  details: string;
}

interface SketchComprehension {
  app_title: string;
  app_category: string;
  comprehension_summary: string;
  layout_structure: string;
  detected_text_labels: string[];
  detected_components: SketchDetectedComponent[];
  layout_conclusions: string[];
  style_direction: string;
  suggested_backend: string[];
  synthesized_prompt: string;
  markdown_report?: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

const TEMPLATE_SCHEMAS = {
  ecommerce: {
    name: "E-Commerce Analytics",
    badge: "Dashboard + CRUD API",
    image: "/templates/ecommerce_dashboard.png",
    prompt:
      "Generate an E-Commerce dashboard. Include a clean left sidebar with menu items, a top search bar, stats widgets for revenue, orders, conversion rate, and an active users chart. Below, list recent transactions and top products.",
  },
  saas: {
    name: "SaaS Pricing & Billing",
    badge: "Interactive + Checkout API",
    image: "/templates/saas_pricing.png",
    prompt:
      "Generate a premium SaaS pricing plans page. Use a monthly/yearly toggle. Highlight a middle 'Pro' card with a badge. Include feature list checks and CTA buttons for Basic, Pro, and Enterprise.",
  },
  todo: {
    name: "Kanban Task Studio",
    badge: "Board + Task State API",
    image: "/templates/task_app.png",
    prompt:
      "Generate a Kanban task board layout. Columns are 'To Do', 'In Progress', 'Done'. Populate each with a few task cards. Task cards should show priority badges (High/Medium/Low) and user circles.",
  },
};

const QUICK_REFINEMENTS = [
  "Add a live search & filter bar in the top header",
  "Switch the accent palette to emerald & cyan glassmorphism",
  "Add an interactive summary modal with export button",
];

export default function Home() {
  // Google Cloud state
  const [gcpProject, setGcpProject] = useState("");
  const [gcpRegion, setGcpRegion] = useState("us-central1");
  const [gcloudStatus, setGcloudStatus] = useState<any>({
    is_authenticated: false,
    project: null,
    email: null,
  });
  const [projectsList, setProjectsList] = useState<any[]>([]);
  const [regionsList, setRegionsList] = useState<any[]>([]);
  const [showGcpSettings, setShowGcpSettings] = useState(true);

  // File/template upload & Google ADK Sketch Comprehension state
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>("saas");
  const [uploadImage, setUploadImage] = useState<File | null>(null);
  const [uploadedImagePath, setUploadedImagePath] = useState<string | null>(null);
  const [uploadedImageDataUrl, setUploadedImageDataUrl] = useState<string | null>(null);
  const [sketchAnalysis, setSketchAnalysis] = useState<SketchComprehension | null>(null);
  const [analyzingSketch, setAnalyzingSketch] = useState(false);
  const [sketchAnalysisError, setSketchAnalysisError] = useState<string | null>(null);
  const [showSketchLightbox, setShowSketchLightbox] = useState(false);
  const [customPrompt, setCustomPrompt] = useState(TEMPLATE_SCHEMAS.saas.prompt);

  // Pipeline state
  const [steps, setSteps] = useState<PipelineStep[]>([
    {
      name: "Vision Analysis",
      shortName: "Vision",
      agent: "vision_agent",
      description: "Parses layout hierarchy, components, color tokens, and typography from sketch.",
      status: "idle",
      log: [],
    },
    {
      name: "Project Planning",
      shortName: "Architecture",
      agent: "planner_agent",
      description: "Writes SPEC.md defining Next.js component tree and FastAPI REST contract.",
      status: "idle",
      log: [],
    },
    {
      name: "Frontend Building",
      shortName: "Frontend UI",
      agent: "ui_agent",
      description: "Generates responsive Next.js 14 App Router UI and interactive components.",
      status: "idle",
      log: [],
    },
    {
      name: "Backend APIs",
      shortName: "Backend API",
      agent: "backend_agent",
      description: "Implements FastAPI endpoints, Pydantic models, and seed data from SPEC.md.",
      status: "idle",
      log: [],
    },
    {
      name: "Build & Test QA",
      shortName: "QA & Self-Heal",
      agent: "testing_agent",
      description: "Runs npm build & Python syntax checks with up to 4 autonomous self-heal cycles.",
      status: "idle",
      log: [],
    },
    {
      name: "Cloud Run Deploy",
      shortName: "Cloud Run",
      agent: "deployment_agent",
      description: "Packages verified container and deploys to Google Cloud Run after human approval.",
      status: "idle",
      log: [],
    },
  ]);

  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [currentStepIndex, setCurrentStepIndex] = useState(-1);
  const [logs, setLogs] = useState<string[]>([]);
  const [partialText, setPartialText] = useState("");
  const [liveUrl, setLiveUrl] = useState<string | null>(null);
  const [deployMode, setDeployMode] = useState<string | null>(null);
  const [iframeKey, setIframeKey] = useState(0);

  // Interactive editing chat state
  const [chatInput, setChatInput] = useState("");

  const MAX_LOG_LINES = 600;

  // Code & Workspace file viewer state
  const [previewTab, setPreviewTab] = useState<"sketch" | "app" | "code" | "spec">("app");
  const [workspaceFiles, setWorkspaceFiles] = useState<any[]>([]);
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  const [selectedFileContent, setSelectedFileContent] = useState<string>("");
  const [specContent, setSpecContent] = useState<string>("");
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({
    src: true,
    "src/app": true,
    "src/components": true,
    api: true,
    frontend: true,
    "frontend/app": true,
    backend: true,
  });
  const [copiedCode, setCopiedCode] = useState(false);

  // Human approval states
  const [showApprovalDialog, setShowApprovalDialog] = useState(false);

  // Background job tracking
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Projects list (past runs for this browser session)
  const [projects, setProjects] = useState<any[]>([]);

  const logEndRef = useRef<HTMLDivElement>(null);

  // Fetch initial gcloud configuration
  useEffect(() => {
    fetchGcloudStatus();
    fetchGcloudRegions();
    fetchProjects();
  }, []);

  const fetchProjects = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/runs`, { credentials: "include" });
      if (res.ok) setProjects(await res.json());
    } catch (e) {
      console.error("Failed to load projects", e);
    }
  };

  // Replay persisted non-partial events into the Execution Log for completed or reloaded jobs
  const hydrateJobHistory = async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/runs/${id}/history`, { credentials: "include" });
      if (!res.ok) return;
      const data = await res.json();
      const events: any[] = data.events || [];
      const hydrated: string[] = [];
      let plannerSpec = "";
      const ts = new Date().toLocaleTimeString();

      for (const ev of events) {
        if (ev.control === "awaiting_approval") {
          hydrated.push(`[${ts}] ⚠️ Build verified. Human approval required before Cloud Run deployment.`);
          continue;
        }
        if (ev.control === "deploying") {
          hydrated.push(`[${ts}] 👤 Approval granted. Resuming pipeline for Cloud Run deployment...`);
          continue;
        }
        if (ev.control === "rejected") {
          hydrated.push(`[${ts}] 👤 Human approval denied. Deployment cancelled.`);
          continue;
        }
        const who = (ev.author || "agent").toUpperCase();
        if (ev.content?.parts) {
          for (const part of ev.content.parts) {
            if (part.text) {
              hydrated.push(`[${ts}] [${who}] ${part.text}`);
              if (ev.author === "planner_agent" || ev.author === "vision_agent") {
                plannerSpec += `### ${who}\n\n${part.text}\n\n---\n\n`;
              }
            }
            if (part.functionCall) {
              hydrated.push(
                `[${ts}] 🔧 Calling tool: ${part.functionCall.name} -> ${JSON.stringify(part.functionCall.args)}`
              );
            }
            if (part.functionResponse) {
              hydrated.push(`[${ts}] ✅ Tool ${part.functionResponse.name} completed.`);
              const resp = part.functionResponse.response;
              if (resp && resp.url) {
                hydrated.push(
                  `[${ts}] 🚀 ${resp.mode === "mock" ? "MOCK" : "LIVE"} LINK: ${resp.url}`
                );
              }
            }
          }
        }
      }
      if (hydrated.length > 0) {
        setLogs(hydrated.slice(-MAX_LOG_LINES));
      }
      if (plannerSpec) {
        setSpecContent(prev => prev || plannerSpec.trim());
      }
    } catch (e) {
      console.error("Failed to hydrate job history", e);
    }
  };

  // Open a past run: make it the active job so the chat box refines it, and
  // restore its deployed preview + files. Reconnects if it's still running.
  const loadProject = async (id: string) => {
    if (pipelineRunning) return;
    try {
      const res = await fetch(`${API_BASE}/api/runs/${id}`, { credentials: "include" });
      if (!res.ok) return;
      const job = await res.json();
      eventSourceRef.current?.close();
      setJobId(id);
      setJobStatus(job.status);
      localStorage.setItem("s2c_last_job", id);
      setLogs([]);
      setPartialText("");
      setSpecContent("");
      setPreviewTab("app");

      const terminal = ["success", "error", "rejected"].includes(job.status);
      if (!terminal) {
        localStorage.setItem("s2c_active_job", id);
        setPipelineRunning(true);
        if (job.status === "awaiting_approval") {
          setSteps(prev => prev.map((s, idx) => (idx < 5 ? { ...s, status: "success" } : s)));
          setShowApprovalDialog(true);
        } else {
          setSteps(prev => prev.map(s => ({ ...s, status: "idle", log: [] })));
        }
        await hydrateJobHistory(id);
        fetchWorkspaceFiles(id);
        connectToJobEvents(id);
        return;
      }

      setSteps(prev =>
        prev.map(s => ({ ...s, status: job.status === "success" ? "success" : "idle", log: [] }))
      );
      if (job.status === "success" && job.result?.url) {
        setLiveUrl(job.result.url);
        setDeployMode(job.result.mode || null);
      } else {
        setLiveUrl(null);
        setDeployMode(null);
      }
      await hydrateJobHistory(id);
      fetchWorkspaceFiles(id);
    } catch (e) {
      console.error("Failed to load project", e);
    }
  };

  // Clear the active project and go back to a fresh template/sketch run.
  const startNewProject = () => {
    if (pipelineRunning) return;
    eventSourceRef.current?.close();
    localStorage.removeItem("s2c_active_job");
    localStorage.removeItem("s2c_last_job");
    setJobId(null);
    setJobStatus(null);
    setLiveUrl(null);
    setDeployMode(null);
    setLogs([]);
    setPartialText("");
    setWorkspaceFiles([]);
    setSelectedFilePath(null);
    setSelectedFileContent("");
    setSpecContent("");
    setSteps(prev => prev.map(s => ({ ...s, status: "idle", log: [] })));
  };

  // On mount: reconnect to an in-flight job, or rehydrate the last finished one
  useEffect(() => {
    const urlParams =
      typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;
    const queryJobId = urlParams?.get("job");
    const activeJobId = localStorage.getItem("s2c_active_job");
    const lastJobId = localStorage.getItem("s2c_last_job");
    const targetId = queryJobId || activeJobId || lastJobId;
    if (!targetId) return;

    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/runs/${targetId}`, { credentials: "include" });
        if (!res.ok) {
          localStorage.removeItem("s2c_active_job");
          localStorage.removeItem("s2c_last_job");
          return;
        }
        const job = await res.json();
        setJobStatus(job.status);
        const terminal =
          job.status === "success" || job.status === "error" || job.status === "rejected";

        if (!terminal) {
          setJobId(targetId);
          setPipelineRunning(true);
          setLogs([]);
          if (job.status === "awaiting_approval") {
            setSteps(prev => prev.map((s, idx) => (idx < 5 ? { ...s, status: "success" } : s)));
            setShowApprovalDialog(true);
          } else {
            setSteps(prev => prev.map(s => ({ ...s, status: "idle", log: [] })));
          }
          await hydrateJobHistory(targetId);
          fetchWorkspaceFiles(targetId);
          connectToJobEvents(targetId);
          return;
        }

        // Finished job: clear the "active" marker and restore full logs, URL, and files.
        localStorage.removeItem("s2c_active_job");
        setJobId(targetId);
        if (job.status === "success") {
          setSteps(prev => prev.map(s => ({ ...s, status: "success" })));
          if (job.result?.url) {
            setLiveUrl(job.result.url);
            setDeployMode(job.result.mode || null);
          }
        }
        await hydrateJobHistory(targetId);
        fetchWorkspaceFiles(targetId);
      } catch (e) {
        console.error("Failed to restore job", e);
      }
    })();

    return () => {
      eventSourceRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Poll job status while pipelineRunning is true as a resilient fallback alongside SSE
  useEffect(() => {
    if (!pipelineRunning || !jobId) return;
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/runs/${jobId}`, { credentials: "include" });
        if (!res.ok) return;
        const job = await res.json();
        setJobStatus(job.status);
        if (job.status === "awaiting_approval" && !showApprovalDialog) {
          setSteps(prev => prev.map((s, idx) => (idx < 5 ? { ...s, status: "success" } : s)));
          fetchWorkspaceFiles(jobId);
          setShowApprovalDialog(true);
        } else if (job.status === "success") {
          setSteps(prev => prev.map(s => ({ ...s, status: "success" })));
          if (job.result?.url) {
            setLiveUrl(job.result.url);
            setDeployMode(job.result.mode || null);
          }
          handleJobFinished();
        } else if (job.status === "error" || job.status === "rejected") {
          handleJobFinished();
        }
      } catch {
        // Ignore transient network poll errors
      }
    }, 2500);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipelineRunning, jobId, showApprovalDialog]);

  // Open (or reopen) the SSE stream for a job's events
  const connectToJobEvents = (id: string) => {
    eventSourceRef.current?.close();

    const es = new EventSource(`${API_BASE}/api/runs/${id}/events`, { withCredentials: true });
    eventSourceRef.current = es;

    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        handleAgentEvent(event);
      } catch (err) {
        console.error("Error parsing event", err);
      }
    };

    es.onerror = () => {
      // EventSource retries automatically using Last-Event-ID
    };
  };

  const fetchGcloudStatus = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/gcloud/status`, { credentials: "include" });
      const data = await res.json();
      setGcloudStatus(data);
      if (data.is_authenticated) {
        if (data.project) {
          setGcpProject(data.project);
        }
        fetchGcloudProjects(data.project);
      }
    } catch (e) {
      console.error("Backend offline", e);
    }
  };

  const handleConnectGcloud = () => {
    window.location.href = `${API_BASE}/api/gcloud/oauth/login`;
  };

  const handleDisconnectGcloud = async () => {
    try {
      await fetch(`${API_BASE}/api/gcloud/oauth/logout`, {
        method: "POST",
        credentials: "include",
      });
    } finally {
      setGcloudStatus({ is_authenticated: false, project: null, email: null });
      setProjectsList([]);
      setGcpProject("");
    }
  };

  const handleLogout = async () => {
    if (pipelineRunning && !confirm("A pipeline is still running. Log out anyway?")) return;
    try {
      await fetch(`${API_BASE}/api/logout`, { method: "POST", credentials: "include" });
    } catch (e) {
      console.error("Logout request failed", e);
    }
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    localStorage.removeItem("s2c_active_job");
    localStorage.removeItem("s2c_last_job");
    setGcloudStatus({ is_authenticated: false, project: null, email: null });
    setProjectsList([]);
    setProjects([]);
    setGcpProject("");
    setJobId(null);
    setJobStatus(null);
    setLiveUrl(null);
    setDeployMode(null);
    setLogs([]);
    setPartialText("");
    setPipelineRunning(false);
    setWorkspaceFiles([]);
    setSelectedFilePath(null);
    setSelectedFileContent("");
    setSpecContent("");
    setSelectedTemplate("saas");
    setUploadImage(null);
    setUploadedImagePath(null);
    setUploadedImageDataUrl(null);
    setSketchAnalysis(null);
    setSketchAnalysisError(null);
    setCustomPrompt(TEMPLATE_SCHEMAS.saas.prompt);
    setSteps(prev => prev.map(s => ({ ...s, status: "idle", log: [] })));
    fetchGcloudStatus();
    fetchProjects();
  };

  const fetchGcloudProjects = async (savedProject?: string | null) => {
    try {
      const res = await fetch(`${API_BASE}/api/gcloud/projects`, { credentials: "include" });
      const data = await res.json();
      const list = Array.isArray(data) ? data : [];
      setProjectsList(list);

      // Auto-select agents-461209 or the first active project if none was selected yet
      const current = savedProject || gcpProject;
      if (!current && list.length > 0) {
        const preferred =
          list.find((p: any) => p.id === "agents-461209")?.id || list[0].id;
        if (preferred) {
          setGcpProject(preferred);
          persistProject(preferred);
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  const persistProject = (project: string) => {
    fetch(`${API_BASE}/api/gcloud/project`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: project || null }),
    }).catch(e => console.error("Failed to persist project", e));
  };

  const fetchGcloudRegions = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/gcloud/regions`);
      const data = await res.json();
      setRegionsList(data);
    } catch (e) {
      console.error(e);
    }
  };

  // Scroll logs to bottom
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs, partialText]);

  // Run Google ADK (`adk_sketch_interpreter_agent`) on an uploaded sketch or blueprint image
  const runAdkSketchAnalysis = async (opts: {
    imagePath?: string | null;
    imageDataUrl?: string | null;
    hint?: string;
  }) => {
    const pathToUse = opts.imagePath ?? uploadedImagePath;
    let dataUrlToUse = opts.imageDataUrl ?? uploadedImageDataUrl;

    // If analyzing a built-in template and we don't have a base64 dataUrl yet, fetch the template PNG
    if (!pathToUse && !dataUrlToUse && selectedTemplate) {
      const tpl = (TEMPLATE_SCHEMAS as any)[selectedTemplate];
      if (tpl?.image) {
        try {
          const imgRes = await fetch(tpl.image);
          const blob = await imgRes.blob();
          dataUrlToUse = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve(String(reader.result || ""));
            reader.onerror = reject;
            reader.readAsDataURL(blob);
          });
          setUploadedImageDataUrl(dataUrlToUse);
        } catch (err) {
          console.error("Failed to read template image", err);
        }
      }
    }

    if (!pathToUse && !dataUrlToUse) return;

    setAnalyzingSketch(true);
    setSketchAnalysisError(null);
    setPreviewTab("sketch");

    try {
      const res = await fetch(`${API_BASE}/api/analyze-sketch`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          image_path: pathToUse || null,
          image_data_url: dataUrlToUse || null,
          prompt_hint: opts.hint ?? "",
        }),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.detail || "ADK sketch analysis failed");
      }
      const data = await res.json();
      if (data.image_path) {
        setUploadedImagePath(data.image_path);
      }
      if (data.analysis) {
        setSketchAnalysis(data.analysis);
        if (data.analysis.synthesized_prompt) {
          setCustomPrompt(data.analysis.synthesized_prompt);
        }
        if (data.analysis.markdown_report) {
          setSpecContent(
            `### ADK_SKETCH_INTERPRETER_AGENT (Pre-Pipeline Comprehension)\n\n${data.analysis.markdown_report}`
          );
        }
      }
    } catch (err) {
      console.error("ADK sketch comprehension error:", err);
      setSketchAnalysisError(
        err instanceof Error ? err.message : "Failed to interpret sketch with Google ADK"
      );
    } finally {
      setAnalyzingSketch(false);
    }
  };

  // Handle template selection
  const handleSelectTemplate = (key: string) => {
    setSelectedTemplate(key);
    setUploadImage(null);
    setUploadedImagePath(null);
    setUploadedImageDataUrl(null);
    setSketchAnalysis(null);
    setSketchAnalysisError(null);
    setCustomPrompt((TEMPLATE_SCHEMAS as any)[key].prompt);
  };

  // Handle local sketch upload: immediately preview image & run Google ADK Sketch Comprehension
  const handleUploadImage = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      setUploadImage(file);
      setSelectedTemplate(null);
      setSketchAnalysis(null);
      setSketchAnalysisError(null);
      setPreviewTab("sketch");

      // 1. Read local Data URL immediately so user sees their uploaded sketch right away
      const localDataUrl = await new Promise<string>((resolve) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve(String(reader.result || ""));
        reader.readAsDataURL(file);
      });
      setUploadedImageDataUrl(localDataUrl);

      // 2. Upload sketch file to backend UPLOAD_DIR
      const formData = new FormData();
      formData.append("file", file);

      let serverImagePath: string | null = null;
      try {
        const res = await fetch(`${API_BASE}/api/upload-sketch`, {
          method: "POST",
          credentials: "include",
          body: formData,
        });
        const data = await res.json();
        if (data.status === "success") {
          serverImagePath = data.image_path;
          setUploadedImagePath(data.image_path);
        }
      } catch (err) {
        console.error("Upload failed, falling back to inline data URL", err);
      }

      // 3. Automatically run Google ADK Sketch Interpreter Agent to understand layout before pipeline starts
      await runAdkSketchAnalysis({
        imagePath: serverImagePath,
        imageDataUrl: localDataUrl,
        hint: "",
      });
    }
  };

  const addLog = (message: string) => {
    setLogs(prev => {
      const next = [...prev, `[${new Date().toLocaleTimeString()}] ${message}`];
      return next.length > MAX_LOG_LINES ? next.slice(-MAX_LOG_LINES) : next;
    });
  };

  const handleStartGeneration = async (opts?: { prompt?: string; refine?: boolean }) => {
    if (pipelineRunning || analyzingSketch) return;

    const refine = !!opts?.refine && !!jobId;
    const effectiveProject =
      gcpProject || (gcloudStatus.is_authenticated ? "agents-461209" : null);

    setPipelineRunning(true);
    if (!refine) {
      setLiveUrl(null);
      setDeployMode(null);
    }
    setPartialText("");
    setPreviewTab("app");
    setLogs([]);

    setSteps(prev =>
      prev.map((s, idx) => ({
        ...s,
        status: refine && idx < 4 ? "success" : "idle",
        log: [],
      }))
    );

    const activePrompt =
      opts?.prompt ||
      customPrompt ||
      sketchAnalysis?.synthesized_prompt ||
      "Build a responsive web application from this sketch.";
    addLog(
      refine
        ? `Starting Antigravity SDK refinement pass...`
        : `Initiating Sketch2Cloud Hybrid ADK + Antigravity SDK Pipeline...`
    );
    if (!refine && sketchAnalysis) {
      addLog(
        `✅ Passing pre-verified Google ADK Sketch Comprehension ("${sketchAnalysis.app_title}" · ${
          sketchAnalysis.detected_components?.length || 0
        } layout sections) to Stage 2 Planner & Stage 3 UI Builder...`
      );
    }
    addLog(`Target GCP Project: ${effectiveProject || "Cloud Run Default (agents-461209 / Sandbox)"}`);

    try {
      const response = await fetch(`${API_BASE}/api/runs`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: activePrompt,
          image_path: refine ? null : uploadedImagePath,
          image_data_url: refine ? null : uploadedImageDataUrl,
          sketch_analysis: refine ? null : sketchAnalysis,
          gcp_project: effectiveProject,
          gcp_region: gcpRegion,
          refine,
          parent_job_id: refine ? jobId : null,
        }),
      });

      if (!response.ok) throw new Error("Failed to start pipeline job");
      const { job_id } = await response.json();

      setJobId(job_id);
      setJobStatus("running");
      localStorage.setItem("s2c_active_job", job_id);
      localStorage.setItem("s2c_last_job", job_id);
      addLog(`Pipeline job started (${job_id}). Streaming agent events...`);
      fetchProjects();

      connectToJobEvents(job_id);
    } catch (err) {
      console.error(err);
      addLog(`Pipeline Error: ${err instanceof Error ? err.message : "Connection failed"}`);
      setPipelineRunning(false);
      setCurrentStepIndex(-1);
    }
  };

  const handleJobFinished = () => {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    localStorage.removeItem("s2c_active_job");
    setPipelineRunning(false);
    setCurrentStepIndex(-1);
    fetchWorkspaceFiles();
    fetchProjects();
  };

  const handleAgentEvent = (event: any) => {
    if (event.control) {
      setPartialText("");
      if (event.control === "awaiting_approval") {
        setJobStatus("awaiting_approval");
        addLog("⚠️ Build verified. Human approval required before Cloud Run deployment.");
        setSteps(prev => prev.map((s, idx) => (idx < 5 ? { ...s, status: "success" } : s)));
        fetchWorkspaceFiles();
        setShowApprovalDialog(true);
      } else if (event.control === "deploying") {
        setJobStatus("deploying");
        addLog("👤 Approval granted. Resuming pipeline for Cloud Run deployment...");
        setSteps(prev => prev.map((s, idx) => (idx === 5 ? { ...s, status: "running" } : s)));
        setCurrentStepIndex(5);
      } else if (event.control === "rejected") {
        setJobStatus("rejected");
        addLog("👤 Human approval denied. Deployment cancelled.");
        setSteps(prev => prev.map((s, idx) => (idx === 5 ? { ...s, status: "error" } : s)));
      } else if (event.control === "error") {
        setJobStatus("error");
        addLog("❌ Pipeline failed — see the errors above.");
        setSteps(prev => prev.map(s => (s.status === "running" ? { ...s, status: "error" } : s)));
      } else if (event.control === "done") {
        handleJobFinished();
      }
      return;
    }

    const author = event.author;

    let stepIndex = -1;
    if (author === "vision_agent") stepIndex = 0;
    else if (author === "planner_agent") stepIndex = 1;
    else if (author === "ui_agent") stepIndex = 2;
    else if (author === "backend_agent") stepIndex = 3;
    else if (author === "testing_agent" || author === "refine_agent") stepIndex = 4;
    else if (author === "deployment_agent") stepIndex = 5;

    if (stepIndex !== -1) {
      setCurrentStepIndex(stepIndex);
      setSteps(prev =>
        prev.map((s, idx) => {
          if (idx < stepIndex) return { ...s, status: "success" };
          if (idx === stepIndex) return { ...s, status: "running" };
          return s;
        })
      );
    }

    if (event.content && event.content.parts) {
      const who = (author || "agent").toUpperCase();
      for (const part of event.content.parts) {
        if (part.text) {
          if (event.partial) {
            setPartialText(prev => prev + part.text);
          } else {
            setPartialText("");
            addLog(`[${who}] ${part.text}`);
            if (author === "planner_agent" || author === "vision_agent") {
              setSpecContent(prev =>
                prev ? `${prev}\n\n### ${who}\n\n${part.text}` : `### ${who}\n\n${part.text}`
              );
            }
          }
        }

        if (part.functionCall) {
          setPartialText("");
          addLog(
            `🔧 Calling tool: ${part.functionCall.name} -> ${JSON.stringify(part.functionCall.args)}`
          );
        }
        if (part.functionResponse) {
          const resp = part.functionResponse.response;
          addLog(`✅ Tool ${part.functionResponse.name} completed.`);

          if (resp && resp.url) {
            setLiveUrl(resp.url);
            setDeployMode(resp.mode || null);
            setJobStatus("success");
            setPreviewTab("app");
            addLog(`🚀 ${resp.mode === "mock" ? "MOCK" : "LIVE"} LINK: ${resp.url}`);
            setSteps(prev => prev.map((s, idx) => (idx === 5 ? { ...s, status: "success" } : s)));
          }
        }
      }
    }
  };

  const handleApproveDeployment = async (approved: boolean) => {
    setShowApprovalDialog(false);
    if (!jobId) return;

    const effectiveProject =
      gcpProject || (gcloudStatus.is_authenticated ? "agents-461209" : null);

    try {
      await fetch(`${API_BASE}/api/runs/${jobId}/approve`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          approved,
          gcp_project: effectiveProject,
          gcp_region: gcpRegion,
        }),
      });
    } catch (e) {
      console.error("Failed to submit approval decision", e);
      addLog("Pipeline Error: failed to submit approval decision.");
    }
  };

  // Trigger a live Cloud Run deployment for an already-built job workspace
  const handleRedeployLive = async () => {
    if (!jobId || pipelineRunning) return;
    const effectiveProject = gcpProject || "agents-461209";
    setPipelineRunning(true);
    setJobStatus("deploying");
    setCurrentStepIndex(5);
    setSteps(prev => prev.map((s, idx) => (idx === 5 ? { ...s, status: "running" } : s)));
    addLog(`🚀 Triggering live Cloud Run deployment to ${effectiveProject} (${gcpRegion})...`);

    try {
      const res = await fetch(`${API_BASE}/api/runs/${jobId}/redeploy`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          gcp_project: effectiveProject,
          gcp_region: gcpRegion,
        }),
      });
      if (!res.ok) throw new Error("Redeploy request failed");
      connectToJobEvents(jobId);
    } catch (e) {
      console.error("Failed to redeploy", e);
      addLog("Pipeline Error: failed to start live deployment.");
      setPipelineRunning(false);
    }
  };

  // Count total files in tree
  const countFiles = (items: any[]): number => {
    let count = 0;
    for (const item of items) {
      if (item.isDir && item.children) count += countFiles(item.children);
      else if (!item.isDir) count += 1;
    }
    return count;
  };

  const collectAllFilePaths = (items: any[]): string[] => {
    const allPaths: string[] = [];
    const walk = (nodes: any[]) => {
      for (const n of nodes) {
        if (n.isDir && n.children) walk(n.children);
        else if (!n.isDir) allPaths.push(n.path);
      }
    };
    walk(items);
    return allPaths;
  };

  // Find a default file to preview in Code Explorer
  const findDefaultFile = (items: any[]): string | null => {
    const preferred = [
      "frontend/app/page.tsx",
      "frontend/src/app/page.tsx",
      "src/app/page.tsx",
      "src/components/PricingGrid.tsx",
      "backend/main.py",
      "api/main.py",
      "SPEC.md",
      "Dockerfile",
    ];
    const allPaths = collectAllFilePaths(items);
    for (const p of preferred) {
      if (allPaths.includes(p)) return p;
    }
    return allPaths.find(p => !p.startsWith(".")) || allPaths[0] || null;
  };

  // Fetch file list in workspace and auto-load SPEC.md + default file
  const fetchWorkspaceFiles = async (overrideJobId?: string) => {
    try {
      const activeId = overrideJobId || jobId;
      if (!activeId) return;
      const qs = `?job_id=${encodeURIComponent(activeId)}`;
      const res = await fetch(`${API_BASE}/api/workspace/files${qs}`, { credentials: "include" });
      if (!res.ok) return;
      const data = await res.json();
      const files = data.files || [];
      setWorkspaceFiles(files);

      // Load SPEC.md if available
      const jobParam = `&job_id=${encodeURIComponent(activeId)}`;
      fetch(`${API_BASE}/api/workspace/file?path=SPEC.md${jobParam}`, { credentials: "include" })
        .then(r => (r.ok ? r.json() : null))
        .then(d => {
          if (d?.content) setSpecContent(d.content);
        })
        .catch(() => {});

      // Auto-select a key file if none selected yet
      const defaultPath = findDefaultFile(files);
      if (defaultPath) {
        const fileRes = await fetch(
          `${API_BASE}/api/workspace/file?path=${encodeURIComponent(defaultPath)}${jobParam}`,
          { credentials: "include" }
        );
        if (fileRes.ok) {
          const fileData = await fileRes.json();
          setSelectedFilePath(defaultPath);
          setSelectedFileContent(fileData.content || "");
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  // View specific file code
  const handleViewFile = async (path: string, switchTab = true) => {
    setSelectedFilePath(path);
    try {
      const jobParam = jobId ? `&job_id=${encodeURIComponent(jobId)}` : "";
      const res = await fetch(
        `${API_BASE}/api/workspace/file?path=${encodeURIComponent(path)}${jobParam}`,
        { credentials: "include" }
      );
      const data = await res.json();
      setSelectedFileContent(data.content || "");
      if (switchTab) setPreviewTab("code");
    } catch (e) {
      console.error(e);
    }
  };

  const handleCopyCode = () => {
    if (!selectedFileContent) return;
    navigator.clipboard.writeText(selectedFileContent);
    setCopiedCode(true);
    setTimeout(() => setCopiedCode(false), 1800);
  };

  const toggleFolder = (path: string) => {
    setExpandedFolders(prev => ({ ...prev, [path]: !prev[path] }));
  };

  const handleChatSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!chatInput.trim() || pipelineRunning) return;

    const text = chatInput.trim();
    setChatInput("");

    if (!jobId) {
      addLog("Nothing to refine yet — run the pipeline once first.");
      return;
    }
    addLog(`✏️ Refinement requested: "${text}"`);
    handleStartGeneration({ prompt: text, refine: true });
  };

  // Render workspace tree recursively
  const renderTree = (items: any[]) => {
    return items.map((item, index) => {
      if (item.isDir) {
        const isExpanded = expandedFolders[item.path] ?? true;
        return (
          <div key={index} className="pl-2">
            <div
              onClick={() => toggleFolder(item.path)}
              className="flex items-center gap-1.5 py-1 px-2 rounded-md hover:bg-white/[0.05] cursor-pointer select-none text-slate-300 transition-colors"
            >
              {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              <FolderOpen size={13} className="text-amber-400/80 shrink-0" />
              <span className="text-xs font-medium truncate">{item.name}</span>
            </div>
            {isExpanded && item.children && (
              <div className="border-l border-white/[0.06] ml-2.5 pl-1">
                {renderTree(item.children)}
              </div>
            )}
          </div>
        );
      } else {
        const isSelected = selectedFilePath === item.path;
        return (
          <div
            key={index}
            onClick={() => handleViewFile(item.path)}
            className={`flex items-center gap-2 py-1 px-2.5 rounded-md cursor-pointer text-xs transition-all ml-3 ${
              isSelected
                ? "bg-indigo-500/15 text-indigo-300 font-medium border border-indigo-500/30"
                : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-200"
            }`}
          >
            <Code size={12} className={isSelected ? "text-indigo-400 shrink-0" : "text-slate-500 shrink-0"} />
            <span className="truncate">{item.name}</span>
          </div>
        );
      }
    });
  };

  // Format individual log entries for high visual clarity
  const renderFormattedLog = (log: string, index: number) => {
    const timeMatch = log.match(/^\[([^\]]+)\]\s*(.*)$/);
    const timestamp = timeMatch ? timeMatch[1] : "";
    const body = timeMatch ? timeMatch[2] : log;

    // Tool call line
    if (body.startsWith("🔧 Calling tool:")) {
      const toolMatch = body.match(/^🔧 Calling tool:\s*([^\s]+)\s*->\s*(.*)$/);
      const toolName = toolMatch ? toolMatch[1] : "tool";
      let argSummary = toolMatch ? toolMatch[2] : "";
      try {
        const parsed = JSON.parse(argSummary);
        if (parsed.file_path) argSummary = parsed.file_path;
        else if (parsed.app_name) argSummary = `${parsed.app_name} (${parsed.gcp_region || "us-central1"})`;
        else argSummary = JSON.stringify(parsed);
      } catch {
        // keep raw
      }
      return (
        <div
          key={index}
          className="flex items-center gap-2 py-1 px-2.5 rounded-md bg-amber-500/[0.06] border border-amber-500/15 text-xs"
        >
          <span className="text-[10px] text-slate-600 shrink-0">{timestamp}</span>
          <Wrench size={12} className="text-amber-400 shrink-0" />
          <span className="font-semibold text-amber-300">{toolName}</span>
          <span className="text-slate-400 truncate font-mono text-[11px]">{argSummary}</span>
        </div>
      );
    }

    // Tool completion line
    if (body.startsWith("✅ Tool")) {
      return (
        <div key={index} className="flex items-center gap-2 py-0.5 px-2 text-[11px] text-emerald-400/90">
          <span className="text-[10px] text-slate-600 shrink-0">{timestamp}</span>
          <CheckCircle2 size={12} className="shrink-0" />
          <span>{body.replace("✅ ", "")}</span>
        </div>
      );
    }

    // Deployment link line
    if (body.includes("LINK:")) {
      const cleanBody = body.replace(/^🚀\s*/, "");
      const urlMatch = cleanBody.match(/https?:\/\/\S+/);
      const extractedUrl = urlMatch ? urlMatch[0] : null;
      const isRealLink = cleanBody.includes("LIVE LINK:") && extractedUrl;
      return (
        <div
          key={index}
          className="flex items-center justify-between gap-2 py-2 px-3 rounded-lg bg-emerald-500/15 border border-emerald-500/30 text-xs text-emerald-200 font-medium"
        >
          <div className="flex items-center gap-2 min-w-0">
            <Rocket size={13} className="text-emerald-400 shrink-0" />
            {isRealLink ? (
              <a
                href={extractedUrl}
                target="_blank"
                rel="noreferrer"
                className="truncate underline decoration-emerald-400/60 hover:text-white"
              >
                {cleanBody}
              </a>
            ) : (
              <span className="truncate">{cleanBody}</span>
            )}
          </div>
          {isRealLink && (
            <a
              href={extractedUrl}
              target="_blank"
              rel="noreferrer"
              className="px-2 py-0.5 rounded bg-emerald-500/20 hover:bg-emerald-500/30 text-[10px] font-semibold text-emerald-200 shrink-0 flex items-center gap-1"
            >
              Open <ExternalLink size={10} />
            </a>
          )}
        </div>
      );
    }

    // Agent tagged line: [VISION_AGENT] ...
    const agentMatch = body.match(/^\[([A-Z_]+)\]\s*([\s\S]*)$/);
    if (agentMatch) {
      const agentTag = agentMatch[1];
      const text = agentMatch[2];
      const badgeColors: Record<string, string> = {
        VISION_AGENT: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30",
        PLANNER_AGENT: "bg-violet-500/15 text-violet-300 border-violet-500/30",
        UI_AGENT: "bg-indigo-500/15 text-indigo-300 border-indigo-500/30",
        BACKEND_AGENT: "bg-blue-500/15 text-blue-300 border-blue-500/30",
        TESTING_AGENT: "bg-amber-500/15 text-amber-300 border-amber-500/30",
        REFINE_AGENT: "bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30",
        DEPLOYMENT_AGENT: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
        SYSTEM: "bg-slate-500/15 text-slate-300 border-slate-500/30",
      };
      const badgeClass =
        badgeColors[agentTag] || "bg-indigo-500/15 text-indigo-300 border-indigo-500/30";

      return (
        <div
          key={index}
          className="p-2.5 rounded-lg bg-white/[0.02] border border-white/[0.05] space-y-1.5"
        >
          <div className="flex items-center justify-between gap-2">
            <span
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold border ${badgeClass}`}
            >
              <Cpu size={10} />
              {agentTag}
            </span>
            <span className="text-[10px] text-slate-600">{timestamp}</span>
          </div>
          <div className="whitespace-pre-wrap leading-relaxed text-slate-300 text-xs pl-0.5">
            {text}
          </div>
        </div>
      );
    }

    // Default / system line
    return (
      <div key={index} className="whitespace-pre-wrap leading-relaxed text-xs py-0.5 px-1">
        <span className="text-[10px] text-slate-600 mr-2">{timestamp}</span>
        <span
          className={
            body.includes("ERROR") || body.includes("❌")
              ? "text-red-400 font-medium"
              : body.includes("⚠️")
              ? "text-amber-300 font-medium"
              : body.includes("👤")
              ? "text-indigo-300 font-medium"
              : "text-slate-400"
          }
        >
          {body}
        </span>
      </div>
    );
  };

  const activeSketchImageSrc =
    uploadedImageDataUrl ||
    (selectedTemplate ? (TEMPLATE_SCHEMAS as any)[selectedTemplate]?.image : null);
  const canLaunch =
    (selectedTemplate || uploadImage || customPrompt.trim()) &&
    !pipelineRunning &&
    !analyzingSketch;
  const totalWorkspaceFiles = countFiles(workspaceFiles);
  const completedStepsCount = steps.filter(s => s.status === "success").length;

  return (
    <div className="flex h-screen bg-[#07090e] text-slate-200 font-sans antialiased overflow-hidden">
      {/* LEFT SIDEBAR - STUDIO CONTROLS */}
      <aside className="w-[290px] border-r border-white/[0.07] bg-[#0b0e16] flex flex-col overflow-hidden shrink-0">
        {/* BRAND HEADER */}
        <div className="px-5 pt-5 pb-4 border-b border-white/[0.06]">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500 via-indigo-600 to-violet-600 flex items-center justify-center shadow-lg shadow-indigo-500/20 ring-1 ring-white/15">
                <Cloud className="text-white" size={18} />
              </div>
              <div>
                <div className="flex items-center gap-1.5">
                  <h1 className="text-sm font-bold tracking-tight text-white">Sketch2Cloud</h1>
                  <span className="px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider rounded bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">
                    Studio
                  </span>
                </div>
                <p className="text-[11px] text-slate-400">Agentic Sketch-to-Cloud Run</p>
              </div>
            </div>
          </div>

          {/* SDK & GOVERNANCE PILL */}
          <div className="mt-3 flex items-center justify-between px-2.5 py-1.5 rounded-lg bg-white/[0.03] border border-white/[0.06] text-[11px]">
            <span className="flex items-center gap-1.5 text-slate-300 font-medium">
              <Sparkles size={12} className="text-indigo-400" />
              Google Antigravity SDK
            </span>
            <span className="flex items-center gap-1 text-[10px] text-emerald-400 font-medium">
              <ShieldCheck size={11} />
              Policy Gate
            </span>
          </div>
        </div>

        {/* SCROLLABLE CONTROLS */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {/* PROJECTS HISTORY */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h2 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                Workspace Runs ({projects.length})
              </h2>
              <button
                onClick={startNewProject}
                disabled={pipelineRunning}
                className="inline-flex items-center gap-1 text-xs font-medium text-indigo-400 hover:text-indigo-300 disabled:opacity-40 cursor-pointer"
              >
                <Plus size={12} /> New
              </button>
            </div>
            {projects.length === 0 ? (
              <p className="text-xs text-slate-500 py-1">No runs yet — pick a blueprint below.</p>
            ) : (
              <div className="max-h-36 overflow-y-auto space-y-1 pr-0.5">
                {projects.map(p => {
                  const active = p.id === jobId;
                  const statusBadge =
                    p.status === "success"
                      ? { dot: "bg-emerald-400", label: "DEPLOYED", text: "text-emerald-400" }
                      : p.status === "awaiting_approval"
                      ? { dot: "bg-amber-400 animate-pulse", label: "APPROVAL", text: "text-amber-300" }
                      : p.status === "error"
                      ? { dot: "bg-red-400", label: "FAILED", text: "text-red-400" }
                      : p.status === "rejected"
                      ? { dot: "bg-slate-500", label: "REJECTED", text: "text-slate-400" }
                      : { dot: "bg-indigo-400 animate-pulse", label: "RUNNING", text: "text-indigo-300" };
                  return (
                    <button
                      key={p.id}
                      onClick={() => loadProject(p.id)}
                      disabled={pipelineRunning}
                      title={p.message || p.id}
                      className={`w-full text-left px-2.5 py-2 rounded-lg text-xs flex items-start gap-2 transition-all cursor-pointer disabled:opacity-50 ${
                        active
                          ? "bg-indigo-500/15 text-white border border-indigo-500/30 shadow-sm"
                          : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-200 border border-transparent"
                      }`}
                    >
                      <span className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${statusBadge.dot}`} />
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium">
                          {p.parent_job_id ? "↳ " : ""}
                          {p.message || "(untitled run)"}
                        </div>
                        <div className="flex items-center justify-between mt-0.5">
                          <span className={`text-[10px] font-semibold ${statusBadge.text}`}>
                            {statusBadge.label}
                          </span>
                          <span className="text-[10px] text-slate-500 font-mono">
                            #{p.id.slice(0, 6)}
                          </span>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* TEMPLATE PICKER */}
          <div className="space-y-2">
            <h2 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              1. Wireframe Blueprint
            </h2>
            <div className="space-y-1.5">
              {Object.entries(TEMPLATE_SCHEMAS).map(([key, schema]) => {
                const isSelected = selectedTemplate === key;
                return (
                  <button
                    key={key}
                    title={schema.prompt}
                    onClick={() => handleSelectTemplate(key)}
                    className={`w-full text-left px-3 py-2 rounded-lg transition-all cursor-pointer border ${
                      isSelected
                        ? "bg-indigo-500/15 text-white border-indigo-500/40 shadow-sm"
                        : "bg-white/[0.02] text-slate-300 border-white/[0.05] hover:bg-white/[0.05] hover:border-white/10"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold">{schema.name}</span>
                      {isSelected && <CheckCircle2 size={13} className="text-indigo-400 shrink-0" />}
                    </div>
                    <span className="block text-[10px] text-slate-400 mt-0.5">{schema.badge}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* CUSTOM FILE UPLOAD + LIVE SKETCH PREVIEW */}
          <div className="space-y-2">
            <h2 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              Or Upload Hand-Drawn Sketch
            </h2>
            <label
              className={`flex items-center gap-2.5 px-3 py-2.5 rounded-lg border border-dashed text-xs cursor-pointer transition-all ${
                uploadImage
                  ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
                  : "border-white/15 bg-white/[0.02] hover:border-indigo-400/40 hover:bg-white/[0.04] text-slate-300"
              }`}
            >
              <Upload size={14} className="shrink-0 text-indigo-400" />
              <span className="truncate font-medium">
                {uploadImage ? uploadImage.name : "Drop or browse PNG, JPG, WebP"}
              </span>
              <input type="file" onChange={handleUploadImage} className="hidden" accept="image/*" />
            </label>

            {/* UPLOADED SKETCH / BLUEPRINT VISUAL THUMBNAIL & ADK COMPREHENSION STATUS */}
            {activeSketchImageSrc && (
              <div className="rounded-xl bg-[#07090e] border border-white/[0.08] p-2.5 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-indigo-300 flex items-center gap-1">
                    <Eye size={11} />
                    {uploadImage ? "Uploaded Sketch" : "Blueprint Wireframe"}
                  </span>
                  <button
                    type="button"
                    onClick={() => setShowSketchLightbox(true)}
                    className="text-[10px] text-slate-400 hover:text-white underline cursor-pointer"
                  >
                    Zoom image
                  </button>
                </div>

                <div
                  onClick={() => {
                    setPreviewTab("sketch");
                  }}
                  title="Click to inspect Sketch & ADK Comprehension"
                  className="relative group rounded-lg overflow-hidden border border-white/10 bg-black/40 h-28 flex items-center justify-center cursor-pointer"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={activeSketchImageSrc}
                    alt="Uploaded sketch preview"
                    className="max-h-full max-w-full object-contain group-hover:scale-[1.03] transition-transform"
                  />
                  <div className="absolute inset-0 bg-gradient-to-t from-black/75 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity flex items-end justify-between p-2">
                    <span className="text-[10px] text-white font-medium">
                      View Sketch & AI Comprehension
                    </span>
                  </div>
                </div>

                {/* ADK ANALYSIS STATUS OR TRIGGER */}
                {analyzingSketch ? (
                  <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-indigo-500/15 border border-indigo-500/30 text-[11px] text-indigo-200">
                    <RefreshCw size={12} className="animate-spin text-indigo-400 shrink-0" />
                    <span className="truncate">Google ADK interpreting layout…</span>
                  </div>
                ) : sketchAnalysis ? (
                  <div
                    onClick={() => setPreviewTab("sketch")}
                    className="px-2.5 py-2 rounded-lg bg-emerald-500/10 border border-emerald-500/30 space-y-1 cursor-pointer hover:bg-emerald-500/15 transition-colors"
                  >
                    <div className="flex items-center justify-between gap-1">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-300 flex items-center gap-1">
                        <CheckCircle2 size={11} /> ADK Understood Sketch
                      </span>
                      <span className="text-[10px] text-emerald-400/90 font-mono">
                        {sketchAnalysis.detected_components?.length || 0} sections
                      </span>
                    </div>
                    <p className="text-[11px] font-semibold text-white truncate">
                      {sketchAnalysis.app_title}
                    </p>
                    <p className="text-[10px] text-slate-300 line-clamp-2 leading-relaxed">
                      {sketchAnalysis.comprehension_summary}
                    </p>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => runAdkSketchAnalysis({ hint: customPrompt })}
                    className="w-full py-1.5 px-2.5 rounded-lg bg-indigo-500/15 hover:bg-indigo-500/25 border border-indigo-500/30 text-[11px] font-semibold text-indigo-200 flex items-center justify-center gap-1.5 cursor-pointer transition-colors"
                  >
                    <Sparkles size={12} className="text-indigo-400" />
                    Interpret Sketch with Google ADK
                  </button>
                )}

                {sketchAnalysisError && (
                  <p className="text-[10px] text-red-400 leading-snug">{sketchAnalysisError}</p>
                )}
              </div>
            )}
          </div>

          {/* PROMPT */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h2 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                2. Architecture Prompt
              </h2>
              <span className="text-[10px] text-indigo-400 font-medium">
                {sketchAnalysis ? "ADK Synthesized ✓" : "Gemini 3.5 Flash"}
              </span>
            </div>
            <textarea
              value={customPrompt}
              onChange={e => setCustomPrompt(e.target.value)}
              rows={4}
              placeholder="Describe the application requirements, routes, and visual theme..."
              className="w-full resize-none bg-[#07090e] border border-white/10 rounded-lg p-2.5 text-xs text-slate-200 placeholder-slate-500 leading-relaxed focus:outline-none focus:border-indigo-500/60"
            />
          </div>

          {/* GCP SETTINGS */}
          <div className="rounded-lg bg-white/[0.02] border border-white/[0.06] p-3 space-y-2">
            <button
              onClick={() => setShowGcpSettings(!showGcpSettings)}
              className="flex items-center justify-between w-full text-xs font-medium text-slate-300 hover:text-white cursor-pointer"
            >
              <span className="flex items-center gap-2">
                <span
                  className={`w-2 h-2 rounded-full ${
                    gcloudStatus.is_authenticated ? "bg-emerald-400" : "bg-amber-400"
                  }`}
                />
                <span>
                  {gcloudStatus.is_authenticated ? "Google Cloud OAuth" : "Cloud Run Target"}
                </span>
              </span>
              <span className="flex items-center gap-1 text-[10px] text-slate-400">
                {gcloudStatus.is_authenticated ? "Connected" : "Configure"}
                <ChevronDown
                  size={13}
                  className={`transition-transform ${showGcpSettings ? "rotate-180" : ""}`}
                />
              </span>
            </button>

            {showGcpSettings && (
              <div className="space-y-2.5 pt-2 border-t border-white/[0.06]">
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  {gcloudStatus.is_authenticated
                    ? `Authenticated as ${gcloudStatus.email}`
                    : "Connect Google Cloud OAuth to deploy live containers to your GCP project."}
                </p>
                {gcloudStatus.is_authenticated ? (
                  <>
                    <select
                      value={gcpProject}
                      onChange={e => {
                        setGcpProject(e.target.value);
                        persistProject(e.target.value);
                      }}
                      className="w-full bg-[#07090e] border border-white/10 rounded-lg py-1.5 px-2.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500/60"
                    >
                      <option value="">Select GCP Project…</option>
                      {projectsList.map(p => (
                        <option key={p.id} value={p.id}>
                          {p.name} ({p.id})
                        </option>
                      ))}
                    </select>
                    <select
                      value={gcpRegion}
                      onChange={e => setGcpRegion(e.target.value)}
                      className="w-full bg-[#07090e] border border-white/10 rounded-lg py-1.5 px-2.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500/60"
                    >
                      {regionsList.map(r => (
                        <option key={r.id} value={r.id}>
                          {r.name}
                        </option>
                      ))}
                    </select>
                    <button
                      onClick={handleDisconnectGcloud}
                      className="text-xs text-red-400 hover:text-red-300 cursor-pointer"
                    >
                      Disconnect Google Account
                    </button>
                  </>
                ) : (
                  <button
                    onClick={handleConnectGcloud}
                    className="w-full py-1.5 px-3 rounded-lg bg-indigo-500/15 hover:bg-indigo-500/25 border border-indigo-500/30 flex items-center justify-center gap-1.5 text-xs font-medium text-indigo-300 cursor-pointer transition-colors"
                  >
                    <RefreshCw size={12} /> Connect Google Cloud OAuth
                  </button>
                )}
              </div>
            )}
          </div>
        </div>

        {/* LAUNCH CTA FOOTER */}
        <div className="p-4 border-t border-white/[0.07] bg-[#090c14] space-y-2.5">
          <button
            disabled={!canLaunch}
            onClick={() => handleStartGeneration()}
            className="w-full py-2.5 px-4 bg-gradient-to-r from-indigo-500 to-violet-600 hover:from-indigo-400 hover:to-violet-500 disabled:from-white/5 disabled:to-white/5 disabled:text-slate-600 text-white text-xs font-semibold rounded-lg flex items-center justify-center gap-2 shadow-lg shadow-indigo-500/20 disabled:shadow-none transition-all cursor-pointer"
          >
            {pipelineRunning ? (
              <>
                <RefreshCw size={14} className="animate-spin" />
                Running Agent Pipeline…
              </>
            ) : analyzingSketch ? (
              <>
                <RefreshCw size={14} className="animate-spin" />
                ADK Interpreting Sketch…
              </>
            ) : (
              <>
                <Play size={14} fill="currentColor" />
                {sketchAnalysis
                  ? "Approve ADK Layout & Launch Pipeline"
                  : "Launch Agentic Pipeline"}
              </>
            )}
          </button>
          <div className="flex items-center justify-between text-[11px] px-0.5">
            <span className="text-slate-400 truncate">
              {gcloudStatus.is_authenticated ? gcloudStatus.email : "Guest Session"}
            </span>
            <button
              onClick={handleLogout}
              className="text-slate-400 hover:text-slate-200 shrink-0 cursor-pointer"
            >
              Reset session
            </button>
          </div>
        </div>
      </aside>

      {/* CENTER + RIGHT MAIN WORKSPACE */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* TOP 6-STAGE PIPELINE HEADER */}
        <header className="border-b border-white/[0.07] bg-[#0b0e16]/90 backdrop-blur px-5 py-2.5 shrink-0">
          <div className="flex items-center justify-between gap-4">
            <div className="grid grid-cols-6 gap-2 flex-1">
              {steps.map((step, idx) => {
                const isError = step.status === "error";
                const isRunning = !isError && (step.status === "running" || currentStepIndex === idx);
                const isSuccess = !isError && (step.status === "success" || currentStepIndex > idx);
                return (
                  <div
                    key={idx}
                    title={`${step.name} (${step.agent}): ${step.description}`}
                    className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg border transition-all ${
                      isError
                        ? "bg-red-500/10 border-red-500/30 text-red-300"
                        : isRunning
                        ? "bg-indigo-500/15 border-indigo-500/40 text-white shadow-sm shadow-indigo-500/10"
                        : isSuccess
                        ? "bg-emerald-500/[0.08] border-emerald-500/25 text-emerald-300"
                        : "bg-white/[0.02] border-white/[0.05] text-slate-500"
                    }`}
                  >
                    <span
                      className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 ${
                        isError
                          ? "bg-red-500/20 text-red-300"
                          : isSuccess
                          ? "bg-emerald-500/20 text-emerald-300"
                          : isRunning
                          ? "bg-indigo-500 text-white animate-pulse"
                          : "bg-white/5 text-slate-500"
                      }`}
                    >
                      {isError ? "!" : isSuccess ? "✓" : idx + 1}
                    </span>
                    <div className="min-w-0">
                      <div className="text-[11px] font-semibold truncate leading-tight">
                        {step.shortName}
                      </div>
                      <div className="text-[9px] font-mono opacity-75 truncate">{step.agent}</div>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* PROGRESS SUMMARY PILL */}
            <div className="hidden xl:flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/[0.03] border border-white/[0.06] shrink-0">
              <span className="text-[11px] font-medium text-slate-400">Stages</span>
              <span className="text-xs font-bold text-white">{completedStepsCount}/6</span>
            </div>
          </div>

          {/* INLINE GOVERNANCE APPROVAL BANNER IF AWAITING APPROVAL */}
          {(showApprovalDialog || jobStatus === "awaiting_approval") && (
            <div className="mt-2.5 flex items-center justify-between gap-4 px-4 py-2 rounded-lg bg-amber-500/15 border border-amber-500/40 text-amber-200">
              <div className="flex items-center gap-2 text-xs">
                <AlertTriangle size={15} className="text-amber-400 shrink-0" />
                <span>
                  <strong className="font-semibold text-white">
                    Human-in-the-Loop Gate (`policy.ask`):
                  </strong>{" "}
                  Frontend & Backend build passed QA verification. Approve Cloud Run deployment?
                </span>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => handleApproveDeployment(false)}
                  className="px-3 py-1 rounded-md bg-white/10 hover:bg-white/15 text-xs font-medium text-slate-200 cursor-pointer"
                >
                  Reject
                </button>
                <button
                  onClick={() => handleApproveDeployment(true)}
                  className="px-3 py-1 rounded-md bg-emerald-500 hover:bg-emerald-400 text-xs font-semibold text-slate-950 cursor-pointer"
                >
                  Approve & Deploy
                </button>
              </div>
            </div>
          )}
        </header>

        {/* SPLIT BODY: CENTER AGENT STREAM + RIGHT ARTIFACT EXPLORER */}
        <div className="flex-1 flex overflow-hidden">
          {/* CENTER PANEL - AGENT EXECUTION STREAM & REFINEMENT */}
          <section className="flex-1 flex flex-col bg-[#07090e] studio-grid-bg overflow-hidden min-w-[380px]">
            {/* LOGS HEADER */}
            <div className="flex items-center justify-between px-5 pt-4 pb-2.5 shrink-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                  <Terminal size={14} className="text-indigo-400" />
                  Antigravity Agent Telemetry
                </span>
                {jobId && (
                  <span className="px-2 py-0.5 rounded bg-white/[0.04] border border-white/[0.06] font-mono text-[10px] text-slate-400">
                    job:{jobId.slice(0, 8)}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setLogs([])}
                  className="text-xs text-slate-500 hover:text-slate-300 cursor-pointer"
                >
                  Clear view
                </button>
              </div>
            </div>

            {/* LOG STREAM BOX */}
            <div className="flex-1 mx-5 mb-3 bg-[#0b0e16]/95 border border-white/[0.07] rounded-xl p-4 font-mono text-xs overflow-y-auto space-y-2 select-text shadow-inner">
              {logs.length === 0 ? (
                analyzingSketch || sketchAnalysis || uploadImage ? (
                  <div className="font-sans space-y-4 p-1">
                    {/* PRE-LAUNCH UPLOADED SKETCH + GOOGLE ADK COMPREHENSION BANNER */}
                    <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-indigo-500/10 border border-indigo-500/25">
                      <div className="flex items-center gap-2.5">
                        <div className="w-8 h-8 rounded-lg bg-indigo-500/20 border border-indigo-500/40 flex items-center justify-center text-indigo-300 shrink-0">
                          <Sparkles size={16} />
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-bold text-white">
                              Pre-Pipeline Sketch Comprehension
                            </span>
                            <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-cyan-500/15 text-cyan-300 border border-cyan-500/30">
                              Google ADK Vision Agent
                            </span>
                          </div>
                          <p className="text-[11px] text-slate-400">
                            Inspect your uploaded sketch and verify the AI&apos;s layout conclusions before sending to the build pipeline.
                          </p>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => runAdkSketchAnalysis({ hint: customPrompt })}
                        disabled={analyzingSketch}
                        className="px-2.5 py-1.5 rounded-lg bg-white/[0.06] hover:bg-white/[0.12] border border-white/10 text-[11px] font-medium text-slate-200 flex items-center gap-1.5 shrink-0 cursor-pointer disabled:opacity-50"
                      >
                        <RefreshCw size={11} className={analyzingSketch ? "animate-spin" : ""} />
                        Re-Analyze
                      </button>
                    </div>

                    {/* UPLOADED SKETCH IMAGE PREVIEW CARD */}
                    {activeSketchImageSrc && (
                      <div className="rounded-xl bg-[#07090e] border border-white/[0.08] p-3 space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="text-[11px] font-semibold text-slate-300 flex items-center gap-1.5">
                            <Eye size={13} className="text-indigo-400" />
                            Your Uploaded Sketch ({uploadImage ? uploadImage.name : "Selected Blueprint"})
                          </span>
                          <button
                            type="button"
                            onClick={() => setShowSketchLightbox(true)}
                            className="text-[11px] text-indigo-400 hover:text-indigo-300 cursor-pointer"
                          >
                            Expand Fullscreen ↗
                          </button>
                        </div>
                        <div
                          onClick={() => setShowSketchLightbox(true)}
                          className="rounded-lg overflow-hidden border border-white/10 bg-black/50 max-h-56 flex items-center justify-center p-2 cursor-pointer"
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={activeSketchImageSrc}
                            alt="Uploaded Sketch"
                            className="max-h-52 object-contain rounded"
                          />
                        </div>
                      </div>
                    )}

                    {/* ADK COMPREHENSION RESULTS */}
                    {analyzingSketch ? (
                      <div className="rounded-xl bg-white/[0.02] border border-indigo-500/25 p-6 text-center space-y-3">
                        <RefreshCw size={22} className="animate-spin text-indigo-400 mx-auto" />
                        <div className="space-y-1">
                          <p className="text-xs font-semibold text-white">
                            Google ADK (`adk_sketch_interpreter_agent`) is analyzing your sketch…
                          </p>
                          <p className="text-[11px] text-slate-400">
                            Extracting wireframe containers, handwritten labels, spatial grid layout, and REST API conclusions.
                          </p>
                        </div>
                      </div>
                    ) : sketchAnalysis ? (
                      <div className="space-y-3">
                        {/* SUMMARY & STRUCTURE */}
                        <div className="rounded-xl bg-white/[0.02] border border-white/[0.08] p-3.5 space-y-2">
                          <div className="flex items-center justify-between gap-2">
                            <h3 className="text-sm font-bold text-white">
                              {sketchAnalysis.app_title}
                            </h3>
                            <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                              {sketchAnalysis.app_category}
                            </span>
                          </div>
                          <p className="text-xs text-slate-300 leading-relaxed">
                            {sketchAnalysis.comprehension_summary}
                          </p>
                          <div className="pt-1.5 border-t border-white/[0.06] text-[11px] text-indigo-200">
                            <strong className="text-slate-400 font-medium">Spatial Layout: </strong>
                            {sketchAnalysis.layout_structure}
                          </div>
                        </div>

                        {/* OCR / HANDWRITTEN LABELS DETECTED */}
                        {sketchAnalysis.detected_text_labels?.length > 0 && (
                          <div className="rounded-xl bg-white/[0.02] border border-white/[0.07] p-3 space-y-2">
                            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                              Detected Sketch Text & Labels ({sketchAnalysis.detected_text_labels.length})
                            </span>
                            <div className="flex flex-wrap gap-1.5">
                              {sketchAnalysis.detected_text_labels.map((lbl, idx) => (
                                <span
                                  key={idx}
                                  className="px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/25 text-amber-200 font-mono text-[11px]"
                                >
                                  &ldquo;{lbl}&rdquo;
                                </span>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* DETECTED WIREFRAME COMPONENTS */}
                        {sketchAnalysis.detected_components?.length > 0 && (
                          <div className="rounded-xl bg-white/[0.02] border border-white/[0.07] p-3 space-y-2">
                            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                              Wireframe Regions & Components ({sketchAnalysis.detected_components.length})
                            </span>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                              {sketchAnalysis.detected_components.map((comp, idx) => (
                                <div
                                  key={idx}
                                  className="p-2.5 rounded-lg bg-[#07090e] border border-white/[0.06] space-y-1"
                                >
                                  <div className="flex items-center justify-between gap-1.5">
                                    <span className="text-xs font-semibold text-white truncate">
                                      {comp.name}
                                    </span>
                                    <span className="px-1.5 py-0.5 rounded text-[9px] font-mono bg-indigo-500/15 text-indigo-300 border border-indigo-500/25 shrink-0">
                                      {comp.region}
                                    </span>
                                  </div>
                                  <p className="text-[11px] text-slate-400 leading-snug">
                                    {comp.details}
                                  </p>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* LAYOUT CONCLUSIONS FOR NEXT PHASE */}
                        {sketchAnalysis.layout_conclusions?.length > 0 && (
                          <div className="rounded-xl bg-emerald-500/[0.05] border border-emerald-500/20 p-3 space-y-2">
                            <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-300 flex items-center gap-1.5">
                              <CheckCircle2 size={12} />
                              ADK Layout Conclusions (Ready for Planner & UI Agent)
                            </span>
                            <ul className="space-y-1 text-xs text-slate-200">
                              {sketchAnalysis.layout_conclusions.map((item, idx) => (
                                <li key={idx} className="flex items-start gap-2 leading-relaxed">
                                  <span className="text-emerald-400 mt-0.5">•</span>
                                  <span>{item}</span>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        <button
                          type="button"
                          onClick={() => handleStartGeneration()}
                          className="w-full py-2.5 px-4 rounded-xl bg-gradient-to-r from-indigo-500 to-violet-600 hover:from-indigo-400 hover:to-violet-500 text-white font-semibold text-xs flex items-center justify-center gap-2 shadow-lg shadow-indigo-500/20 cursor-pointer transition-all"
                        >
                          <Play size={13} fill="currentColor" />
                          Send ADK Layout Conclusions to Next Phase (Launch Pipeline)
                        </button>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <div className="h-full flex flex-col items-center justify-center text-center p-6 text-slate-500 gap-3">
                    <div className="w-12 h-12 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400">
                      <Terminal size={22} />
                    </div>
                    <div className="space-y-1 max-w-xs">
                      <p className="text-xs font-semibold text-slate-300">
                        Ready to launch your multi-agent build
                      </p>
                      <p className="text-[11px] text-slate-500 font-sans leading-relaxed">
                        Upload a hand-drawn sketch on the left to see Google ADK&apos;s live layout comprehension, or click{" "}
                        <span className="text-indigo-400 font-medium">Launch Agentic Pipeline</span>.
                      </p>
                    </div>
                  </div>
                )
              ) : (
                logs.map((log, index) => renderFormattedLog(log, index))
              )}
              {partialText && (
                <div className="p-2.5 rounded-lg bg-indigo-500/[0.06] border border-indigo-500/20 whitespace-pre-wrap leading-relaxed text-indigo-200/90 text-xs">
                  {partialText}
                </div>
              )}
              <div ref={logEndRef} />
            </div>

            {/* ITERATIVE REFINEMENT BAR */}
            <div className="px-5 pb-4 shrink-0 space-y-2">
              {jobId && !pipelineRunning && (
                <div className="flex items-center gap-1.5 overflow-x-auto pb-0.5">
                  <span className="text-[10px] font-medium text-slate-500 shrink-0 mr-1">
                    Quick refine:
                  </span>
                  {QUICK_REFINEMENTS.map((suggestion, i) => (
                    <button
                      key={i}
                      onClick={() => setChatInput(suggestion)}
                      className="px-2.5 py-1 rounded-full bg-white/[0.03] hover:bg-indigo-500/15 border border-white/[0.06] hover:border-indigo-500/30 text-[11px] text-slate-400 hover:text-indigo-200 whitespace-nowrap transition-colors cursor-pointer"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              )}
              <form onSubmit={handleChatSubmit} className="flex gap-2">
                <input
                  type="text"
                  value={chatInput}
                  disabled={pipelineRunning}
                  onChange={e => setChatInput(e.target.value)}
                  placeholder={
                    jobId
                      ? "Refine the generated app in-place — e.g. “Add a dark/light toggle to the navbar”"
                      : "Run the pipeline once first, then refine the generated code here…"
                  }
                  className="flex-1 bg-[#0b0e16] border border-white/10 rounded-xl py-2.5 px-3.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500/60 disabled:opacity-50"
                />
                <button
                  type="submit"
                  disabled={!chatInput.trim() || pipelineRunning || !jobId}
                  className="px-4 bg-indigo-500/20 hover:bg-indigo-500/30 border border-indigo-500/30 disabled:opacity-40 text-indigo-200 rounded-xl flex items-center gap-1.5 text-xs font-medium cursor-pointer transition-colors"
                >
                  <Send size={13} />
                  Refine
                </button>
              </form>
            </div>
          </section>

          {/* RIGHT CONTAINER - PREVIEW, CODE EXPLORER & ARCHITECTURE SPEC */}
          <section className="w-[500px] xl:w-[560px] border-l border-white/[0.07] bg-[#0b0e16] flex flex-col overflow-hidden shrink-0">
            {/* TABS HEADER */}
            <header className="border-b border-white/[0.07] flex items-center justify-between px-3 shrink-0 bg-[#090c14]">
              <div className="flex text-xs overflow-x-auto">
                <button
                  onClick={() => setPreviewTab("sketch")}
                  className={`flex items-center gap-1.5 px-3 py-3 font-semibold border-b-2 -mb-px transition-colors cursor-pointer whitespace-nowrap ${
                    previewTab === "sketch"
                      ? "border-indigo-500 text-white"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Sparkles size={13} className="text-cyan-400" />
                  Sketch & AI Vision
                  {sketchAnalysis && (
                    <span className="w-2 h-2 rounded-full bg-emerald-400" />
                  )}
                </button>
                <button
                  onClick={() => setPreviewTab("app")}
                  className={`flex items-center gap-1.5 px-3 py-3 font-semibold border-b-2 -mb-px transition-colors cursor-pointer whitespace-nowrap ${
                    previewTab === "app"
                      ? "border-indigo-500 text-white"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Eye size={13} />
                  App Preview
                </button>
                <button
                  onClick={() => {
                    setPreviewTab("code");
                    fetchWorkspaceFiles();
                  }}
                  className={`flex items-center gap-1.5 px-3 py-3 font-semibold border-b-2 -mb-px transition-colors cursor-pointer whitespace-nowrap ${
                    previewTab === "code"
                      ? "border-indigo-500 text-white"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Code size={13} />
                  Code
                  {totalWorkspaceFiles > 0 && (
                    <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                      {totalWorkspaceFiles}
                    </span>
                  )}
                </button>
                <button
                  onClick={() => {
                    setPreviewTab("spec");
                    fetchWorkspaceFiles();
                  }}
                  className={`flex items-center gap-1.5 px-3 py-3 font-semibold border-b-2 -mb-px transition-colors cursor-pointer whitespace-nowrap ${
                    previewTab === "spec"
                      ? "border-indigo-500 text-white"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Layers size={13} />
                  SPEC.md
                </button>
              </div>
            </header>

            <div className="flex-1 overflow-hidden flex flex-col">
              {/* TAB 0: UPLOADED SKETCH & GOOGLE ADK COMPREHENSION */}
              {previewTab === "sketch" && (
                <div className="h-full overflow-y-auto p-4 space-y-4 bg-[#07090e]">
                  {/* UPLOADED IMAGE CARD */}
                  <div className="rounded-xl bg-[#0b0e16] border border-white/[0.08] p-3.5 space-y-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <div>
                        <span className="text-[10px] font-bold uppercase tracking-wider text-indigo-400">
                          Input Source
                        </span>
                        <h3 className="text-xs font-bold text-white">
                          {uploadImage
                            ? `Uploaded Sketch: ${uploadImage.name}`
                            : selectedTemplate
                            ? `Wireframe Blueprint: ${(TEMPLATE_SCHEMAS as any)[selectedTemplate]?.name}`
                            : "Sketch Preview"}
                        </h3>
                      </div>
                      {activeSketchImageSrc && (
                        <button
                          type="button"
                          onClick={() => setShowSketchLightbox(true)}
                          className="px-2.5 py-1 rounded-lg bg-white/[0.06] hover:bg-white/[0.12] text-[11px] font-medium text-slate-200 cursor-pointer"
                        >
                          Fullscreen
                        </button>
                      )}
                    </div>

                    {activeSketchImageSrc ? (
                      <div
                        onClick={() => setShowSketchLightbox(true)}
                        className="rounded-lg overflow-hidden border border-white/10 bg-black/60 max-h-60 flex items-center justify-center p-2 cursor-pointer"
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={activeSketchImageSrc}
                          alt="Sketch preview"
                          className="max-h-56 object-contain rounded"
                        />
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 py-4 text-center">
                        Upload a sketch image on the left to inspect it here.
                      </p>
                    )}
                  </div>

                  {/* GOOGLE ADK COMPREHENSION PANEL */}
                  <div className="rounded-xl bg-[#0b0e16] border border-white/[0.08] p-3.5 space-y-3">
                    <div className="flex items-center justify-between gap-2">
                      <div>
                        <span className="text-[10px] font-bold uppercase tracking-wider text-cyan-400">
                          Stage 1 Vision Engine
                        </span>
                        <h3 className="text-xs font-bold text-white">
                          Google ADK Sketch Comprehension (`adk_sketch_interpreter_agent`)
                        </h3>
                      </div>
                      <button
                        type="button"
                        onClick={() => runAdkSketchAnalysis({ hint: customPrompt })}
                        disabled={analyzingSketch}
                        className="px-2.5 py-1 rounded-lg bg-indigo-500/20 hover:bg-indigo-500/30 border border-indigo-500/30 text-[11px] font-semibold text-indigo-200 flex items-center gap-1 cursor-pointer disabled:opacity-50"
                      >
                        <RefreshCw size={11} className={analyzingSketch ? "animate-spin" : ""} />
                        {sketchAnalysis ? "Re-Analyze" : "Run ADK Vision"}
                      </button>
                    </div>

                    {analyzingSketch ? (
                      <div className="py-8 text-center space-y-2">
                        <RefreshCw size={20} className="animate-spin text-indigo-400 mx-auto" />
                        <p className="text-xs font-semibold text-white">
                          Interpreting sketch layout with Google ADK…
                        </p>
                        <p className="text-[11px] text-slate-400">
                          Detecting wireframe boxes, handwritten annotations, and layout hierarchy.
                        </p>
                      </div>
                    ) : sketchAnalysis ? (
                      <div className="space-y-3 text-xs">
                        <div className="p-3 rounded-lg bg-white/[0.03] border border-white/[0.06] space-y-1.5">
                          <div className="flex items-center justify-between">
                            <span className="font-bold text-white">{sketchAnalysis.app_title}</span>
                            <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                              {sketchAnalysis.app_category}
                            </span>
                          </div>
                          <p className="text-slate-300 leading-relaxed">
                            {sketchAnalysis.comprehension_summary}
                          </p>
                          <p className="text-[11px] text-indigo-300 pt-1">
                            <strong>Layout Structure:</strong> {sketchAnalysis.layout_structure}
                          </p>
                        </div>

                        {sketchAnalysis.detected_text_labels?.length > 0 && (
                          <div className="space-y-1.5">
                            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                              Labels Read from Sketch
                            </span>
                            <div className="flex flex-wrap gap-1.5">
                              {sketchAnalysis.detected_text_labels.map((lbl, i) => (
                                <span
                                  key={i}
                                  className="px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/25 text-amber-200 font-mono text-[11px]"
                                >
                                  {lbl}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}

                        {sketchAnalysis.detected_components?.length > 0 && (
                          <div className="space-y-1.5">
                            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                              Detected Layout Components ({sketchAnalysis.detected_components.length})
                            </span>
                            <div className="space-y-1.5">
                              {sketchAnalysis.detected_components.map((comp, i) => (
                                <div
                                  key={i}
                                  className="p-2.5 rounded-lg bg-[#07090e] border border-white/[0.06]"
                                >
                                  <div className="flex items-center justify-between">
                                    <span className="font-semibold text-white">{comp.name}</span>
                                    <span className="text-[10px] font-mono text-indigo-300">
                                      {comp.region} · {comp.component_type}
                                    </span>
                                  </div>
                                  <p className="text-[11px] text-slate-400 mt-1 leading-relaxed">
                                    {comp.details}
                                  </p>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {sketchAnalysis.layout_conclusions?.length > 0 && (
                          <div className="space-y-1.5">
                            <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-400">
                              Design & Layout Conclusions (Handoff to Next Phase)
                            </span>
                            <ul className="space-y-1 text-[11px] text-slate-300">
                              {sketchAnalysis.layout_conclusions.map((c, i) => (
                                <li key={i} className="flex items-start gap-1.5">
                                  <span className="text-emerald-400">✓</span>
                                  <span>{c}</span>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {sketchAnalysis.suggested_backend?.length > 0 && (
                          <div className="space-y-1.5">
                            <span className="text-[10px] font-bold uppercase tracking-wider text-blue-400">
                              Implied FastAPI Endpoints & Models
                            </span>
                            <ul className="space-y-1 text-[11px] text-slate-300 font-mono">
                              {sketchAnalysis.suggested_backend.map((ep, i) => (
                                <li key={i}>• {ep}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    ) : (
                      <p className="text-xs text-slate-400 leading-relaxed">
                        Click <strong>Run ADK Vision</strong> above (or upload a sketch image on the left) to let Google ADK interpret the wireframe and draw layout conclusions before the build pipeline starts.
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 1: LIVE CLOUD RUN PREVIEW & DEPLOYMENT */}
              {previewTab === "app" && (
                <div className="h-full flex flex-col p-4 gap-3 overflow-hidden">
                  {/* TOP LIVE LINK BAR WHEN DEPLOYED */}
                  {liveUrl && (
                    <div className="flex items-center justify-between gap-2.5 px-3.5 py-2.5 bg-white/[0.03] rounded-xl border border-white/[0.08] shrink-0">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span
                            className={`w-2 h-2 rounded-full shrink-0 ${
                              deployMode === "mock" ? "bg-amber-400" : "bg-emerald-400 animate-pulse"
                            }`}
                          />
                          <span
                            className={`text-[10px] font-bold uppercase tracking-wider ${
                              deployMode === "mock" ? "text-amber-400" : "text-emerald-400"
                            }`}
                          >
                            {deployMode === "mock"
                              ? "Simulated Sandbox URL"
                              : "Live Deployed Cloud Run URL"}
                          </span>
                        </div>
                        {deployMode !== "mock" ? (
                          <a
                            href={liveUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="block text-xs text-indigo-300 hover:text-indigo-200 underline font-mono truncate mt-0.5"
                          >
                            {liveUrl}
                          </a>
                        ) : (
                          <p className="text-xs text-slate-300 font-mono truncate mt-0.5">{liveUrl}</p>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {deployMode !== "mock" ? (
                          <>
                            <button
                              onClick={() => setIframeKey(prev => prev + 1)}
                              title="Reload live preview"
                              className="p-2 bg-white/5 hover:bg-white/10 rounded-lg text-slate-300 cursor-pointer"
                            >
                              <RefreshCw size={13} />
                            </button>
                            <a
                              href={liveUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="px-3 py-1.5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-semibold text-xs rounded-lg flex items-center gap-1.5 shrink-0"
                            >
                              Open App <ExternalLink size={12} />
                            </a>
                          </>
                        ) : (
                          <button
                            onClick={handleRedeployLive}
                            disabled={pipelineRunning}
                            className="px-3 py-1.5 bg-gradient-to-r from-indigo-500 to-violet-600 hover:from-indigo-400 hover:to-violet-500 disabled:opacity-50 text-white font-semibold text-xs rounded-lg flex items-center gap-1.5 shrink-0 cursor-pointer shadow-md shadow-indigo-500/20"
                          >
                            <Rocket size={12} /> Deploy Live Now
                          </button>
                        )}
                      </div>
                    </div>
                  )}

                  {/* MAIN PREVIEW FRAME */}
                  <div className="flex-1 bg-[#07090e] rounded-xl border border-white/[0.07] overflow-y-auto flex flex-col">
                    {liveUrl && deployMode !== "mock" ? (
                      <iframe
                        key={iframeKey}
                        src={liveUrl}
                        className="w-full h-full border-none bg-white"
                        title="Live App Preview"
                      />
                    ) : liveUrl && deployMode === "mock" ? (
                      <div className="flex-1 flex flex-col justify-between p-4 space-y-4">
                        <div className="space-y-3">
                          <div className="flex items-start justify-between gap-3">
                            <div className="flex items-center gap-2.5">
                              <div className="w-10 h-10 rounded-xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shrink-0">
                                <CheckCircle2 size={20} />
                              </div>
                              <div>
                                <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                                  BUILD & QA PASSED
                                </span>
                                <h3 className="text-sm font-bold text-white mt-1">
                                  Ready for Live Cloud Run Deployment
                                </h3>
                              </div>
                            </div>
                          </div>

                          <p className="text-xs text-slate-400 leading-relaxed">
                            This run completed in sandbox mode because no target GCP project was passed at deploy time. Click{" "}
                            <span className="text-indigo-300 font-semibold">Deploy Live to Cloud Run</span>{" "}
                            below to deploy this verified build directly to{" "}
                            <code className="text-emerald-300 font-mono">
                              {gcpProject || "agents-461209"}
                            </code>{" "}
                            and load the live interactive preview here.
                          </p>

                          <button
                            onClick={handleRedeployLive}
                            disabled={pipelineRunning}
                            className="w-full py-2.5 px-4 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-400 hover:to-teal-400 disabled:opacity-50 text-slate-950 font-bold text-xs flex items-center justify-center gap-2 shadow-lg shadow-emerald-500/20 cursor-pointer transition-all"
                          >
                            <Rocket size={14} />
                            Deploy Live to Cloud Run ({gcpProject || "agents-461209"})
                          </button>

                          {/* QUICK JUMP TO GENERATED ARTIFACTS */}
                          <div className="space-y-2 pt-2">
                            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                              Inspect Generated Artifacts ({totalWorkspaceFiles} files)
                            </span>
                            <div className="grid grid-cols-2 gap-2">
                              {(() => {
                                const allPaths = collectAllFilePaths(workspaceFiles);
                                const feEntry =
                                  allPaths.find(p =>
                                    [
                                      "src/app/page.tsx",
                                      "frontend/app/page.tsx",
                                      "frontend/src/app/page.tsx",
                                    ].includes(p)
                                  ) || "src/app/page.tsx";
                                const beEntry =
                                  allPaths.find(p => ["api/main.py", "backend/main.py"].includes(p)) ||
                                  "api/main.py";
                                return (
                                  <>
                                    <button
                                      onClick={() => handleViewFile(feEntry, true)}
                                      className="px-3 py-2 rounded-lg bg-indigo-500/15 hover:bg-indigo-500/25 border border-indigo-500/30 text-left transition-colors cursor-pointer"
                                    >
                                      <div className="text-xs font-semibold text-indigo-200 flex items-center gap-1.5 truncate">
                                        <Code size={12} className="shrink-0" />
                                        <span className="truncate">{feEntry}</span>
                                      </div>
                                      <div className="text-[10px] text-slate-400 mt-0.5">
                                        Generated Next.js UI
                                      </div>
                                    </button>
                                    <button
                                      onClick={() => handleViewFile(beEntry, true)}
                                      className="px-3 py-2 rounded-lg bg-indigo-500/15 hover:bg-indigo-500/25 border border-indigo-500/30 text-left transition-colors cursor-pointer"
                                    >
                                      <div className="text-xs font-semibold text-indigo-200 flex items-center gap-1.5 truncate">
                                        <Code size={12} className="shrink-0" />
                                        <span className="truncate">{beEntry}</span>
                                      </div>
                                      <div className="text-[10px] text-slate-400 mt-0.5">
                                        Generated FastAPI API
                                      </div>
                                    </button>
                                  </>
                                );
                              })()}
                            </div>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="h-full flex flex-col items-center justify-center p-6 text-center gap-3 text-slate-500">
                        <div className="w-12 h-12 rounded-2xl bg-white/[0.03] border border-white/[0.06] flex items-center justify-center text-slate-400">
                          <Cloud size={24} />
                        </div>
                        <div className="space-y-1 max-w-[260px]">
                          <p className="text-xs font-semibold text-slate-300">
                            Cloud Run Preview Canvas
                          </p>
                          <p className="text-[11px] text-slate-500 leading-relaxed">
                            Once the 6-stage Antigravity SDK pipeline finishes, your live deployed Cloud Run application and URL appear right here.
                          </p>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 2: CODE EXPLORER */}
              {previewTab === "code" && (
                <div className="h-full flex flex-col overflow-hidden">
                  {/* FILE TREE */}
                  <div className="h-[34%] min-h-[160px] overflow-y-auto p-3 border-b border-white/[0.07] bg-[#090c14]/60">
                    <div className="flex items-center justify-between mb-2">
                      <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                        <FolderOpen size={13} className="text-amber-400" />
                        Generated Workspace ({totalWorkspaceFiles} files)
                      </span>
                      <button
                        onClick={() => fetchWorkspaceFiles()}
                        className="text-[11px] text-indigo-400 hover:text-indigo-300 flex items-center gap-1 cursor-pointer"
                      >
                        <RefreshCw size={11} /> Refresh
                      </button>
                    </div>
                    {workspaceFiles.length === 0 ? (
                      <p className="text-xs text-slate-500 px-1 py-4 text-center">
                        No generated files yet — run the pipeline first.
                      </p>
                    ) : (
                      <div className="space-y-0.5">{renderTree(workspaceFiles)}</div>
                    )}
                  </div>

                  {/* CODE VIEWER WITH COPY BUTTON */}
                  <div className="flex-1 overflow-hidden flex flex-col p-3 bg-[#07090e]">
                    <div className="flex items-center justify-between gap-2 mb-2 shrink-0">
                      <div className="flex items-center gap-1.5 text-xs font-mono text-slate-300 truncate">
                        <FileText size={13} className="text-indigo-400 shrink-0" />
                        <span className="truncate">{selectedFilePath || "Select a file above"}</span>
                      </div>
                      {selectedFileContent && (
                        <button
                          onClick={handleCopyCode}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded bg-white/[0.05] hover:bg-white/[0.1] text-[11px] text-slate-300 cursor-pointer shrink-0"
                        >
                          {copiedCode ? (
                            <>
                              <Check size={11} className="text-emerald-400" /> Copied
                            </>
                          ) : (
                            <>
                              <Copy size={11} /> Copy
                            </>
                          )}
                        </button>
                      )}
                    </div>
                    <div className="flex-1 border border-white/[0.07] rounded-xl overflow-hidden bg-[#0b0e16]">
                      {selectedFilePath ? (
                        <pre className="h-full p-3.5 overflow-auto font-mono text-[11px] text-slate-200 whitespace-pre leading-relaxed select-text">
                          <code>{selectedFileContent}</code>
                        </pre>
                      ) : (
                        <div className="h-full flex flex-col items-center justify-center text-slate-500 gap-2">
                          <Code size={18} />
                          <span className="text-xs">Select a file from the workspace tree above.</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 3: SPEC.MD VIEWER */}
              {previewTab === "spec" && (
                <div className="h-full flex flex-col p-4 overflow-hidden bg-[#07090e]">
                  <div className="flex items-center justify-between mb-2.5 shrink-0">
                    <span className="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                      <Layers size={14} className="text-violet-400" />
                      Planner Agent Architecture Contract (`SPEC.md`)
                    </span>
                  </div>
                  <div className="flex-1 border border-white/[0.07] rounded-xl overflow-auto bg-[#0b0e16] p-4">
                    {specContent ? (
                      <pre className="font-mono text-xs text-slate-200 whitespace-pre-wrap leading-relaxed select-text">
                        {specContent}
                      </pre>
                    ) : (
                      <div className="h-full flex flex-col items-center justify-center text-slate-500 gap-2 text-center">
                        <FileText size={20} />
                        <p className="text-xs">
                          `SPEC.md` is generated in Stage 2 (`planner_agent`) and shared across the UI, Backend, and QA agents.
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </section>
        </div>
      </div>

      {/* GOVERNANCE & HUMAN-IN-THE-LOOP APPROVAL MODAL */}
      {showApprovalDialog && (
        <div className="fixed inset-0 bg-black/75 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#101420] border border-white/15 rounded-2xl max-w-md w-full p-6 space-y-5 shadow-2xl">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-xl bg-amber-500/15 border border-amber-500/30 flex items-center justify-center text-amber-400 shrink-0">
                <Lock size={18} />
              </div>
              <div>
                <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                  ANTIGRAVITY SDK GOVERNANCE GATE
                </span>
                <h3 className="text-base font-bold text-white mt-1">
                  Approve Cloud Run Deployment?
                </h3>
                <p className="text-xs text-slate-400 mt-1 leading-relaxed">
                  Policy rule <code className="text-amber-300 font-mono">policy.ask(&quot;deploy_to_cloud_run&quot;)</code>{" "}
                  requires explicit human sign-off before container deployment.
                </p>
              </div>
            </div>

            <div className="rounded-xl bg-[#090c14] border border-white/[0.07] p-3.5 space-y-2 text-xs font-mono">
              <div className="flex justify-between">
                <span className="text-slate-500">QA Verification</span>
                <span className="text-emerald-400 font-semibold">✓ npm build & py_compile passed</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Target Project</span>
                <span className="text-slate-200">{gcpProject || "agents-461209"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Target Region</span>
                <span className="text-slate-200">{gcpRegion}</span>
              </div>
            </div>

            <div className="flex gap-3">
              <button
                onClick={() => handleApproveDeployment(false)}
                className="flex-1 py-2.5 bg-white/[0.06] hover:bg-white/[0.1] rounded-xl text-xs font-semibold text-slate-300 cursor-pointer transition-colors"
              >
                Reject Deployment
              </button>
              <button
                onClick={() => handleApproveDeployment(true)}
                className="flex-1 py-2.5 bg-gradient-to-r from-indigo-500 to-violet-600 hover:from-indigo-400 hover:to-violet-500 text-white text-xs font-semibold rounded-xl shadow-lg shadow-indigo-500/25 cursor-pointer transition-all"
              >
                Approve & Deploy
              </button>
            </div>
          </div>
        </div>
      )}

      {/* FULLSCREEN SKETCH LIGHTBOX MODAL */}
      {showSketchLightbox && activeSketchImageSrc && (
        <div
          onClick={() => setShowSketchLightbox(false)}
          className="fixed inset-0 bg-black/85 backdrop-blur-sm flex items-center justify-center z-50 p-6 cursor-pointer"
        >
          <div
            onClick={e => e.stopPropagation()}
            className="bg-[#0b0e16] border border-white/15 rounded-2xl max-w-4xl w-full max-h-[90vh] flex flex-col overflow-hidden shadow-2xl cursor-default"
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.08]">
              <span className="text-xs font-bold text-white flex items-center gap-2">
                <Eye size={14} className="text-indigo-400" />
                {uploadImage
                  ? `Uploaded Sketch: ${uploadImage.name}`
                  : `Blueprint Wireframe: ${(TEMPLATE_SCHEMAS as any)[selectedTemplate || "saas"]?.name}`}
              </span>
              <button
                type="button"
                onClick={() => setShowSketchLightbox(false)}
                className="px-2.5 py-1 rounded-lg bg-white/10 hover:bg-white/20 text-xs font-medium text-slate-200 cursor-pointer"
              >
                Close ✕
              </button>
            </div>
            <div className="p-4 flex-1 overflow-auto flex items-center justify-center bg-black/60">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={activeSketchImageSrc}
                alt="Fullscreen sketch view"
                className="max-h-[75vh] max-w-full object-contain rounded-lg"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
