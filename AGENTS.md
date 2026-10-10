# DRM Engineering Instructions

## Scope

This repository implements a reusable, deterministic framework for maintaining Word requirement documents. Core code must remain business-neutral. Business-specific recognition belongs in `src/drm/profiles` or a future adapter package.

## Safety boundaries

- Treat every input DOCX as read-only. Never save to, replace, rename, or delete an input path.
- Write to a temporary file in the destination directory, verify it is a valid DOCX ZIP, and only then publish it atomically.
- Refuse to overwrite an existing output unless the caller explicitly passes `--force`.
- `inspect`, `search`, `compare`, and `plan` must never modify a DOCX.
- `apply` may execute only changes whose `approval_status` is `approved`, whose target document hash matches, and whose target resolves uniquely.
- Do not turn inferred business logic into confirmed requirements. Preserve `confidence_status`, evidence, and unresolved questions.
- Never silently discard unsupported OOXML. Report limitations and skip unsafe edits.
- Real or sensitive business DOCX files remain outside Git by default.

## Editing rules

- DOCX is the source of truth. Markdown exports are read aids, not reconstruction sources.
- Make the smallest possible OOXML change and inherit the neighboring paragraph style.
- Paragraph replacement must be rejected when the target contains drawings, fields, hyperlinks, bookmarks, content controls, text boxes, or embedded objects.
- Any newly supported operation needs a plan schema update, deterministic resolver, audit entry, validation rule, and regression test.

## Tests

- Run `python -m unittest discover -s tests -v` with `PYTHONPATH=src`.
- Tests must assert original-file hash stability, exact target placement, absence of unrelated semantic changes, media preservation, ambiguous-target refusal, traceability, and consecutive-version behavior.
- Generated DOCX fixtures must use synthetic content only.
- For a release or meaningful DOCX authoring change, render generated DOCX files to PNG with the bundled document renderer and inspect every page.

## Change discipline

- Keep the CLI modes separate and maintain backward-compatible JSON where practical.
- Update README examples and known limitations when behavior changes.
- Do not add network services, paid APIs, vector databases, or autonomous long-running agents to the MVP.
