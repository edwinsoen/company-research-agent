output "artifact_bucket_name" {
  description = "GCS bucket name for GcsArtifactService"
  value       = google_storage_bucket.artifacts.name
}

output "agent_identity_principal" {
  description = "SPIFFE Agent Identity principal configured for IAM bindings"
  value       = var.agent_identity_principal
}
