"""Pydantic schemas and data contracts for Meeting Prep Copilot.

Source of truth: docs/hld.md §8 (Session state schema).
"""

from typing import Optional
from pydantic import BaseModel, Field


class Finding(BaseModel):
    """A structured finding from grounded research."""

    claim: str = Field(description="The concrete fact or assertion made.")
    source_url: str = Field(description="Source URL where this claim was grounded.")
    source_date: str = Field(
        description="Publication or discovery date of the source (YYYY-MM-DD or recent time string)."
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0."
    )


class ResearchFindings(BaseModel):
    """Bounded collection of structured research findings."""

    findings: list[Finding] = Field(
        default_factory=list,
        description="List of structured research findings (maximum 8 findings).",
    )


class ResolvedEntity(BaseModel):
    """Company entity resolved by entity_disambiguator."""

    name: str = Field(description="Canonical company name.")
    domain: str = Field(default="", description="Primary website domain.")
    description: str = Field(description="One-sentence description of the company.")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Resolution confidence score between 0.0 and 1.0."
    )


class DeltaSummary(BaseModel):
    """Summary of changes compared to prior briefs."""

    has_prior: bool = Field(
        default=False,
        description="Whether a prior brief was found for this company in memory.",
    )
    changes: list[str] = Field(
        default_factory=list,
        description="Key factual changes or updates compared to prior briefs.",
    )


class UserPreferences(BaseModel):
    """User preferences stored in session state."""

    focus_areas: list[str] = Field(
        default_factory=list,
        description="Custom focus areas requested for the research brief.",
    )
    recipients: list[str] = Field(
        default_factory=list,
        description="Email addresses to share the published brief with.",
    )


class ApprovalDecision(BaseModel):
    """Human decision captured at approval_gate."""

    status: str = Field(
        description="Decision status: 'approved' to publish, or 'revise' to trigger refinement."
    )
    comment: Optional[str] = Field(
        default=None,
        description="Feedback or revision directive if status is 'revise'.",
    )


class DocRef(BaseModel):
    """Published document reference."""

    doc_id: str = Field(description="Google Drive document ID.")
    doc_url: str = Field(description="Web URL of the published Google Doc.")
    title: str = Field(description="Title of the published document.")
    version: int = Field(default=1, description="Brief version published.")
