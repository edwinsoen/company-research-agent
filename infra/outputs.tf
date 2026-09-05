output "artifact_bucket_name" {
  description = "GCS bucket name for GcsArtifactService"
  value       = google_storage_bucket.artifacts.name
}

output "agent_identity_email" {
  description = "SPIFFE Agent Identity service account email"
  value       = google_service_account.agent_identity.email
}
