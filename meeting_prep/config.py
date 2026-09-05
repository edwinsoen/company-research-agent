"""Configuration for Meeting Prep Copilot."""

import os
from dotenv import load_dotenv

load_dotenv()

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "edwinsoen-l200")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.7-flash")

# Configure backend: Use Vertex AI if explicitly configured or ADC available;
# otherwise transparently use GEMINI_API_KEY.
if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true":
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LOCATION)
elif os.getenv("GEMINI_API_KEY"):
    os.environ.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
else:
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LOCATION)


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
        llm_request.config.tool_config.include_server_side_tool_invocations = True
        if not llm_request.config.automatic_function_calling:
            llm_request.config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)


DEPLOYMENT_ENV = os.getenv("DEPLOYMENT_ENV", "local").lower()
ARTIFACT_BUCKET = os.getenv("ARTIFACT_BUCKET", f"{PROJECT_ID}-artifacts")


def get_session_service():
    """Return VertexAiSessionService if DEPLOYMENT_ENV=='cloud', else InMemorySessionService."""
    if os.getenv("DEPLOYMENT_ENV", "local").lower() == "cloud":
        from google.adk.sessions import VertexAiSessionService
        return VertexAiSessionService(project=PROJECT_ID, location=LOCATION)
    from google.adk.sessions import InMemorySessionService
    return InMemorySessionService()


def get_artifact_service():
    """Return GcsArtifactService if DEPLOYMENT_ENV=='cloud', else InMemoryArtifactService."""
    if os.getenv("DEPLOYMENT_ENV", "local").lower() == "cloud":
        from google.adk.artifacts import GcsArtifactService
        return GcsArtifactService(bucket_name=os.getenv("ARTIFACT_BUCKET", f"{PROJECT_ID}-artifacts"))
    from google.adk.artifacts import InMemoryArtifactService
    return InMemoryArtifactService()
