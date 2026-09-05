#!/usr/bin/env bash
# Deploy Meeting Prep Copilot to Vertex AI Agent Engine with Agent Identity (HLD §12A, §14.1)
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-edwinsoen-l200}"
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

# Deploy using ADK CLI with Cloud Trace / OpenTelemetry and SPIFFE Agent Identity (HLD §12A.1, §14.1)
adk deploy agent_engine \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --otel_to_cloud \
  --agent_engine_config_file="meeting_prep/.agent_engine_config.json" \
  meeting_prep

echo "✓ Deployment submitted to Vertex AI Agent Engine successfully."
