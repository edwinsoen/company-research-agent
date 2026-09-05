variable "project_id" {
  description = "Google Cloud Project ID"
  type        = string
  default     = "edwinsoen-l200"
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
