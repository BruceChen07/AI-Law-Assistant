# AI Skill Template

This template follows the current `AI-Law-Assistant` skill structure so project admins can add new skills with consistent metadata, documentation, files, and version history.

## Required Structure

Every skill should contain four layers of information:

1. Base metadata
2. `SKILL.md`
3. `Skill Card`
4. `Files` and `Versions`

## Field Guide

### Base Metadata

- `id`
  - Stable lowercase identifier.
  - Recommended format: `domain_capability_target`
  - Example: `withholding_tax_checker`

- `display_name`
  - Human-friendly name shown in the admin panel.
  - Example: `Withholding Tax Checker`

- `category`
  - Used for grouping and filtering.
  - Common values: `knowledge`, `document_processing`, `localization`, `audit_pipeline`

- `scene`
  - Business context where the skill should be used.
  - Example: `tax_contract_audit`

- `description`
  - Short explanation of the user value and primary purpose.

- `source_url`
  - Link to the source skill, handbook, internal wiki, or specification.

- `reference_summary`
  - Summary of references, scope boundaries, validation method, and maintenance owner.

- `tags`
  - Search tags such as `tax`, `invoice`, `compliance`, `translation`.

### Detail Metadata

- `publisher_name`
  - Publisher or team name.

- `publisher_handle`
  - Handle or internal owner alias.

- `current_version`
  - Current public or internal version tag.

- `license_name`
  - License or internal usage note.

- `security_audit_status`
  - Suggested values: `draft`, `internal`, `pass`, `pending-verification`

- `install_command`
  - Optional installation command if the skill is distributed externally.

## SKILL.md Template

```md
# Example AI Skill

## Overview
Describe the skill goal, user value, and expected output in concise language.

## Trigger Conditions
- Explain which tasks should route to this skill.
- Describe the minimum required inputs.

## Workflow
1. Validate the input context.
2. Read the required references or knowledge files.
3. Execute the skill logic.
4. Return structured output for downstream processing.

## Review Checklist
- Confirm the source material is up to date.
- Mark uncertain items for manual review.
- Keep the output aligned with project policy and scope.
```

## Skill Card Template

```json
{
  "overview": "Describe the core problem this skill solves in one or two concise sentences.",
  "publisher_name": "AI-Law-Assistant",
  "publisher_handle": "@your-team",
  "version": "v0.1.0",
  "license_name": "Internal Reference",
  "geography": ["China"],
  "use_type": "Internal / project use",
  "use_case": "Explain who should use this skill and the business scenario it targets.",
  "review_before_use": [
    {
      "risk": "Summarize the main business or compliance risk.",
      "mitigation": "Explain how operators should review or mitigate the risk."
    }
  ],
  "ethical_considerations": "Note any human review, compliance, or policy constraints before production use.",
  "output_behavior": {
    "types": ["Structured JSON"],
    "format": "Summarize the expected response format.",
    "parameters": "2D",
    "side_effects": ["List any file writes, notifications, or downstream actions."]
  },
  "references": [
    {
      "label": "Source or handbook",
      "url": "https://example.com/skill-reference",
      "type": "external"
    }
  ]
}
```

## Files Template

Recommended file structure:

```text
SKILL.md
skill-card.md
references/
references/domain-notes.md
references/examples.md
agents/
agents/openai.yaml
```

Guidance:

- `SKILL.md`
  - Main behavior contract for the skill.

- `skill-card.md`
  - Rendered JSON or markdown summary for governance and review.

- `references/*.md`
  - Domain notes, policy references, terminology, examples, or checklists.

- `agents/*.yaml`
  - Runtime or model adapter configuration when needed.

## Versions Template

Each version should include:

- `version_tag`
- `release_label`
- `published_at`
- `is_latest`
- `download_url`
- `changelog`

Example:

```text
version_tag: v0.1.0
release_label: Draft
published_at: 2026-07-21
is_latest: true
download_url: https://example.com/download
changelog:
- Initial project-aligned template
- Add references and workflow before production use
```

## Suggested Workflow For New Skills

1. Create the base metadata first.
2. Write a concise but strict `SKILL.md`.
3. Fill the `Skill Card` with risk and review guidance.
4. Add `references/*.md` and example files.
5. Record the initial version and changelog.
6. Test the skill in the admin panel before linking it to templates or agents.
