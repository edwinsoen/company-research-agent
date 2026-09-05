terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.20"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# 1. GCS Artifact Bucket for GcsArtifactService (HLD §9.2, §14.1)
resource "google_storage_bucket" "artifacts" {
  name                     = "${var.project_id}-${var.app_name}-artifacts"
  location                 = var.region
  uniform_bucket_level_access = true
  force_destroy            = false

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 90
    }
  }
}

# 2. Agent Identity Service Account & SPIFFE Configuration (HLD §12A.1)
resource "google_service_account" "agent_identity" {
  account_id   = "${var.app_name}-agent-sa"
  display_name = "Meeting Prep Copilot SPIFFE Agent Identity"
  description  = "Per-agent SPIFFE runtime identity with mTLS credentials"
}

# 3. IAM Grants for Agent Identity (HLD §12A.1)
resource "google_project_iam_member" "agent_context_editor" {
  project = var.project_id
  role    = "roles/aiplatform.agentContextEditor"
  member  = "serviceAccount:${google_service_account.agent_identity.email}"
}

resource "google_project_iam_member" "agent_express_user" {
  project = var.project_id
  role    = "roles/aiplatform.expressUser"
  member  = "serviceAccount:${google_service_account.agent_identity.email}"
}

# Modern object-level roles on the artifact bucket (legacy roles rejected in HLD §12A.1)
resource "google_storage_bucket_iam_member" "artifact_writer" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.agent_identity.email}"
}
