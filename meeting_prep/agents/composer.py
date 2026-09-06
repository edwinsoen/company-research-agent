"""Composer agent.

Synthesizes structured findings into an executive one-page markdown brief with inline citations.
Source: docs/hld.md §7.2
"""

from google.adk.agents import LlmAgent
from meeting_prep.config import MODEL_NAME

COMPOSER_INSTRUCTION = """\
You are an executive intelligence briefing composer.

Your role is to assemble a clear, high-density, professional one-page briefing document about the target company.

Inputs from research:
- Company: {resolved_entity.name} ({resolved_entity.domain})
- Overview: {resolved_entity.description}
- Profile findings: {research_profile}
- Recent news findings: {research_news}
- Focus area findings: {research_focus}
- Delta summary: {delta_summary}
- Prior draft (if refining): {brief_draft?}
- Refinement directive (if refining): {refinement_directive?}
- Refinement target (if refining): {refinement_target?}

Requirements:
1. Synthesize ONLY from the provided structured findings. Do not hallucinate external claims.
2. Every claim made in the brief MUST include an inline Markdown link to its source URL (e.g. "[claim text](source_url)").
3. Maintain a clean, structured section layout:
   # Executive Brief: {resolved_entity.name}
   *Generated for meeting preparation*

   - If a prior brief exists ({delta_summary.has_prior} is True), LEAD WITH THE DELTA SECTION (HLD §16):
     ## 1. Changes Since Prior Brief (Delta)
     (Explicitly render the delta changes comparing current vs prior findings with inline citations)

     ## 2. Company Profile & Business Model
     (Synthesize findings from research_profile into 2-3 concise paragraphs or structured bullet points with inline citations)

     ## 3. Recent Developments (Last 90 Days)
     (Synthesize dated findings from research_news, highlighting key announcements, dates, and significance with inline citations)

     ## 4. Strategic Focus Areas
     (Synthesize findings from research_focus. If no custom focus areas were set or findings are empty, note "*Standard profile requested; no custom focus areas specified.*")

   - If no prior brief exists ({delta_summary.has_prior} is False, baseline briefing):
     ## 1. Company Profile & Business Model
     (Synthesize findings from research_profile into 2-3 concise paragraphs or structured bullet points with inline citations)

     ## 2. Changes Since Prior Brief
     *No prior briefing on record. Establishing initial baseline.*

     ## 3. Recent Developments (Last 90 Days)
     (Synthesize dated findings from research_news, highlighting key announcements, dates, and significance with inline citations)

     ## 4. Strategic Focus Areas
     (Synthesize findings from research_focus. If no custom focus areas were set or findings are empty, note "*Standard profile requested; no custom focus areas specified.*")

4. REFINEMENT MODE (When Prior Draft and Refinement Directive are present):
   - Update ONLY the specific section targeted by the refinement directive/target using the refreshed research findings.
   - Leave ALL OTHER sections byte-identical to the prior draft.
5. Output the complete markdown brief directly in your response. Keep it executive-ready, objective, and well-formatted.
"""


async def save_composer_draft_artifact(callback_context):
    """Save the generated brief_draft as a versioned artifact in ArtifactService (HLD §7.2, §9.2)."""
    state = callback_context.state
    brief_draft = state.get("brief_draft")
    if not brief_draft:
        return None

    current_version = int(state.get("draft_version", 0) or 0)
    new_version = current_version + 1
    state["draft_version"] = new_version

    filename = f"brief_draft_v{new_version}.md"
    try:
        from google.genai import types
        part = types.Part.from_text(text=brief_draft)
        await callback_context.save_artifact(
            filename=filename,
            artifact=part,
            custom_metadata={"draft_version": new_version},
        )
    except Exception:
        # Fallback if artifact service is not configured in runner
        pass
    return None


def create_composer() -> LlmAgent:
    """Create the composer agent."""
    return LlmAgent(
        name="composer",
        model=MODEL_NAME,
        instruction=COMPOSER_INSTRUCTION,
        tools=[],
        output_key="brief_draft",
        after_agent_callback=save_composer_draft_artifact,
    )

