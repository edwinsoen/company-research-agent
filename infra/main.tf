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

# 2. IAM Grants for Provisioned Agent Identity (HLD §12A.1, §14.2)
# Agent Runtime provisions a per-agent SPIFFE identity principal when deployed with AGENT_IDENTITY.
# Under AGENT_IDENTITY, custom service accounts are rejected (HLD §12A.3).
# Once the Agent Engine instance is deployed, bind IAM roles to the provisioned agent_identity_principal.

resource "google_project_iam_member" "agent_context_editor" {
  count   = var.agent_identity_principal != "" ? 1 : 0
  project = var.project_id
  role    = "roles/aiplatform.agentContextEditor"
  member  = var.agent_identity_principal
}

resource "google_project_iam_member" "agent_default_access" {
  count   = var.agent_identity_principal != "" ? 1 : 0
  project = var.project_id
  role    = "roles/aiplatform.agentDefaultAccess"
  member  = var.agent_identity_principal
}

resource "google_project_iam_member" "agent_express_user" {
  count   = var.agent_identity_principal != "" ? 1 : 0
  project = var.project_id
  role    = "roles/aiplatform.expressUser"
  member  = var.agent_identity_principal
}

# Modern object-level roles on the artifact bucket (legacy roles rejected in HLD §12A.1)
resource "google_storage_bucket_iam_member" "artifact_writer" {
  count  = var.agent_identity_principal != "" ? 1 : 0
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectUser"
  member = var.agent_identity_principal
}

