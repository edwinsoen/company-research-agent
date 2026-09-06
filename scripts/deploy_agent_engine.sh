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
EOF

# Deploy using ADK CLI with Cloud Trace / OpenTelemetry and SPIFFE Agent Identity (HLD §12A.1, §14.1)
adk deploy agent_engine \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --otel_to_cloud \
  --artifact_service_uri="gs://${ARTIFACT_BUCKET}" \
  --env_file="${ENV_FILE}" \
  meeting_prep

echo "✓ Deployment submitted to Vertex AI Agent Engine successfully."
