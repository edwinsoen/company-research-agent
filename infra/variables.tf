variable "project_id" {
  description = "Google Cloud Project ID (pass via -var='project_id=...' or TF_VAR_project_id)"
  type        = string
}

variable "region" {
  description = "Google Cloud Region for Agent Engine and Artifacts"
  type        = string
  default     = "us-central1"
}

variable "app_name" {
  description = "Application name"
  type        = string
  default     = "meeting_prep"
}
