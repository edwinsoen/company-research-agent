"""Configuration for Meeting Prep Copilot."""

import os
from typing import Any, Optional
from dotenv import load_dotenv
from google.adk.memory import InMemoryMemoryService

load_dotenv()

def _detect_gcp_project() -> str:
    proj = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
    if proj:
        return proj
    try:
        import subprocess

        res = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return ""


PROJECT_ID = _detect_gcp_project()
LOCATION = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")
GENAI_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.7-flash")

# Configure backend: Use Vertex AI if explicitly configured or ADC available;
# otherwise transparently use GEMINI_API_KEY.
if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true":
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    if PROJECT_ID:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
    os.environ["GOOGLE_CLOUD_LOCATION"] = GENAI_LOCATION
elif os.getenv("GEMINI_API_KEY"):
    os.environ.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
else:
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    if PROJECT_ID:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
    os.environ["GOOGLE_CLOUD_LOCATION"] = GENAI_LOCATION


def enable_server_side_tools_callback(callback_context=None, llm_request=None, **kwargs):
    """Ensure Gemini allows built-in tools (like Google Search) to coexist with function calling,
    and explicitly disables SDK-level automatic function calling (AFC) since ADK orchestrates tool execution.
    """
    if llm_request:
        from google.genai import types
        if not llm_request.config:
            llm_request.config = types.GenerateContentConfig()
        if not llm_request.config.tool_config:
            llm_request.config.tool_config = types.ToolConfig()
        # include_server_side_tool_invocations is only valid in Gemini Developer API (mldev) mode.
        # In Gemini Enterprise / Vertex AI Agent Platform mode, it is unsupported and rejected by google.genai.
        is_vertex = (
            os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true"
            or os.getenv("DEPLOYMENT_ENV", "").lower() == "cloud"
        )
        if not is_vertex:
            llm_request.config.tool_config.include_server_side_tool_invocations = True
        if not llm_request.config.automatic_function_calling:
            llm_request.config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)


DEPLOYMENT_ENV = os.getenv("DEPLOYMENT_ENV", "local").lower()
ARTIFACT_BUCKET = os.getenv("ARTIFACT_BUCKET", f"{PROJECT_ID}-meeting_prep-artifacts")


def get_session_service():
    """Return VertexAiSessionService if DEPLOYMENT_ENV=='cloud', else InMemorySessionService."""
    if os.getenv("DEPLOYMENT_ENV", "local").lower() == "cloud":
        from google.adk.sessions import VertexAiSessionService
        agent_engine_id = os.getenv("AGENT_ENGINE_ID")
        return VertexAiSessionService(project=PROJECT_ID, location=LOCATION, agent_engine_id=agent_engine_id)
    from google.adk.sessions import InMemorySessionService
    return InMemorySessionService()


def get_artifact_service():
    """Return GcsArtifactService if DEPLOYMENT_ENV=='cloud', else InMemoryArtifactService."""
    if os.getenv("DEPLOYMENT_ENV", "local").lower() == "cloud":
        from google.adk.artifacts import GcsArtifactService
        bucket = os.getenv("ARTIFACT_BUCKET", f"{PROJECT_ID}-meeting_prep-artifacts")
        return GcsArtifactService(bucket_name=bucket)
    from google.adk.artifacts import InMemoryArtifactService
    return InMemoryArtifactService()


class LocalMemoryService(InMemoryMemoryService):
    """In-memory memory service supporting direct add_memory with custom metadata.

    Used for local development and testing when VertexAiMemoryBankService is not configured.
    """

    def __init__(self) -> None:
        super().__init__()
        self._direct_memories: dict[tuple[str, str], list[Any]] = {}

    async def add_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        memories: Any,
        custom_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        user_key = (app_name, user_id)
        with self._lock:
            if user_key not in self._direct_memories:
                self._direct_memories[user_key] = []
            for mem in memories:
                if custom_metadata:
                    existing = getattr(mem, "custom_metadata", {}) or {}
                    merged = dict(existing)
                    merged.update(custom_metadata)
                    mem.custom_metadata = merged
                self._direct_memories[user_key].append(mem)

    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> Any:
        from google.adk.memory.base_memory_service import SearchMemoryResponse

        base_resp = await super().search_memory(app_name=app_name, user_id=user_id, query=query)
        base_memories = list(base_resp.memories or [])

        user_key = (app_name, user_id)
        query_words = set(query.lower().split())

        scored: list[tuple[int, Any]] = []
        with self._lock:
            direct_list = list(self._direct_memories.get(user_key, []))

        for mem in direct_list:
            text = ""
            if mem.content and mem.content.parts:
                for p in mem.content.parts:
                    t = getattr(p, "text", "")
                    if t:
                        text += t
            words_in_text = set(text.lower().split())
            matched = len(query_words.intersection(words_in_text))
            meta = getattr(mem, "custom_metadata", {}) or {}
            for v in meta.values():
                if isinstance(v, str) and any(w in v.lower() for w in query_words):
                    matched += 2
            if matched > 0 or not query_words:
                scored.append((matched, mem))

        scored.sort(key=lambda x: -x[0])
        combined = [mem for _, mem in scored] + base_memories
        return SearchMemoryResponse(memories=combined[:10])


def get_memory_service():
    """Return VertexAiMemoryBankService if DEPLOYMENT_ENV=='cloud', else LocalMemoryService."""
    if os.getenv("DEPLOYMENT_ENV", "local").lower() == "cloud":
        from google.adk.memory import VertexAiMemoryBankService
        return VertexAiMemoryBankService(project=PROJECT_ID, location=LOCATION)
    return LocalMemoryService()

