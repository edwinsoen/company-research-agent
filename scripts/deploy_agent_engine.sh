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

# Deploy using ADK CLI with Cloud Trace export and SPIFFE identity flag
adk deploy agent_engine \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --app="meeting_prep.app:app" \
  --trace_to_cloud \
  --service_account="${APP_NAME}-agent-sa@${PROJECT_ID}.iam.gserviceaccount.com"

echo "✓ Deployment submitted to Vertex AI Agent Engine successfully."
