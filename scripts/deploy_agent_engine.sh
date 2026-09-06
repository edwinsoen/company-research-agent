#!/usr/bin/env bash
# Deploy Meeting Prep Copilot to Vertex AI Agent Engine with Agent Identity (HLD §12A, §14.1)
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo '')}"
if [ -z "${PROJECT_ID}" ]; then
  echo "Error: GOOGLE_CLOUD_PROJECT is not set and no active gcloud project found." >&2
  exit 1
fi
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
APP_NAME="meeting_prep"
ARTIFACT_BUCKET="${PROJECT_ID}-${APP_NAME}-artifacts"

echo "==========================================================================="
echo "🚀 Deploying Meeting Prep Copilot to Vertex AI Agent Engine"
echo "   Project:         ${PROJECT_ID}"
echo "   Region:          ${REGION}"
echo "   App:             ${APP_NAME}"
echo "   Artifact Bucket: ${ARTIFACT_BUCKET}"
echo "==========================================================================="

export DEPLOYMENT_ENV="cloud"
export GOOGLE_GENAI_USE_VERTEXAI="true"
export ARTIFACT_BUCKET="${ARTIFACT_BUCKET}"

# Write deployment environment variables to a temporary file passed via --env_file
# to avoid truncating or leaving behind artifacts in meeting_prep/.env
ENV_FILE="$(mktemp)"
trap 'rm -f "${ENV_FILE}"' EXIT

cat > "${ENV_FILE}" <<EOF
DEPLOYMENT_ENV=cloud
GOOGLE_GENAI_USE_VERTEXAI=true
ARTIFACT_BUCKET=${ARTIFACT_BUCKET}
GOOGLE_CLOUD_LOCATION=global
MODEL_NAME=${MODEL_NAME:-gemini-3.7-flash}
EOF

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Resolve ADK executable (check PATH, .venv/bin/adk, or python -m google.adk.cli)
if command -v adk >/dev/null 2>&1; then
  ADK_CMD="adk"
elif [ -x "${REPO_ROOT}/.venv/bin/adk" ]; then
  ADK_CMD="${REPO_ROOT}/.venv/bin/adk"
elif [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
  ADK_CMD="${REPO_ROOT}/.venv/bin/python -m google.adk.cli"
else
  echo "Error: 'adk' command not found on PATH or in ${REPO_ROOT}/.venv." >&2
  echo "Please activate your virtual environment: source .venv/bin/activate" >&2
  exit 1
fi

AGENT_ENGINE_ID="${AGENT_ENGINE_ID:-${REASONING_ENGINE_ID:-}}"
EXTRA_ARGS=()
if [ -n "${AGENT_ENGINE_ID}" ]; then
  echo "   Target Engine:   ${AGENT_ENGINE_ID} (updating existing instance)"
  EXTRA_ARGS+=(--agent_engine_id="${AGENT_ENGINE_ID}")
fi

# Deploy using ADK CLI with Cloud Trace / OpenTelemetry and SPIFFE Agent Identity (HLD §12A.1, §14.1)
${ADK_CMD} deploy agent_engine \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --otel_to_cloud \
  --artifact_service_uri="gs://${ARTIFACT_BUCKET}" \
  --env_file="${ENV_FILE}" \
  ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
  meeting_prep

echo "✓ Deployment submitted to Vertex AI Agent Engine successfully."
