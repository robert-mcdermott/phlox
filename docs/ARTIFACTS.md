# Editable artifacts and version history

Phlox can edit generated Markdown, HTML, code, and other UTF-8 text files in the artifact
canvas. Each saved version is retained independently of the conversation's live workspace.
Normal Chat remains the default; AI revision happens only when you request it.

## Open and edit

1. Open a generated file in the canvas, either from an answer or **Workspace Files**.
2. Select **Edit & versions**. Supported text files are limited to **1 MiB** each.
3. Edit the text, use **Preview** to inspect your draft, then select **Save version**.
4. Use the version selector to reopen saved text. **Compare** shows the selected saved
   version against another version; it does not include unsaved draft changes.
5. **Download version** exports exactly that version's text, with its version number in
   the filename. **Preview original** returns to the original canvas file.

New finalized agent outputs automatically acquire versions when they meet the text limit.
Files saved by earlier releases are imported when you open their editor. A saved answer
opens its own version even when newer versions exist. Opening a workspace file captures its
current text if that content has not already been retained. Versions are shared by file
path within a conversation, including its conversation alternatives.

Versions show their origin: `agent`, `saved_answer` (an older answer snapshot), `workspace`,
`edit`, or `restore`. The selected working version is marked **current**; importing an
older answer does not replace that selection. Source message identifiers
and, for new agent output, turn/model metadata retain the relationship to the originating
answer. Names and file types come from the workspace path.

Unsaved editor drafts stay **in memory** when you close the canvas or switch chats. Reopen
the same file from the same answer or workspace entry to resume. Save before refreshing,
closing the browser, or logging out: these actions discard in-memory artifact drafts.
Phlox requests the browser's standard leave-page warning while artifact drafts exist;
logout clears them. This differs from the chat composer's session-persisted text drafts.
Changing versions asks before discarding unsaved edits. Typing in the plain-text editor
normalizes line endings to LF; selected-text AI replacement preserves the surrounding text.

## Revise a selected passage with AI

1. Save any current edits, then select a passage in **Edit**.
2. Enter an instruction, such as “Make this paragraph more concise while keeping the figures.”
3. Select **Revise selection**. Phlox sends only that passage and your instruction, plus
   a short editing instruction, to your currently selected model.
4. Review the **Proposed replacement**. **Apply to draft** replaces only the selected
   passage; **Discard proposal** leaves the document unchanged.
5. Review the resulting draft and **Save version** when satisfied.

This is one tool-free model call, with normal budget checks, input/output guardrails,
context limits, and usage accounting. It does not send the rest of the document, chat
history, project instructions, documents, or personal memory. The selected passage may
itself contain private information; the model selection shown by the editor determines
where it is sent. There is no automatic provider fallback for revision.

Selections are limited to 12,000 characters and instructions to 2,000. Output is limited
to the smaller of your configured output allowance and 4,096 tokens, with a 48,000-character
ceiling. Failed, blocked, interrupted, or truncated responses are never applied. Usage
may still be incurred by failed or discarded attempts; it appears in the usage ledger
as an `artifact_edit` call. Reviewed proposals become ordinary user-saved edits, not new
chat answers or evidence-verified reports.

**Stop revision** cancels the request. Closing the editor or switching chats also disconnects
it, even when reconnectable chat runs are enabled. Cancellation is cooperative: an in-flight
provider request may take time to stop and may still incur usage. This revision workflow
does not create a durable Run or resume automatically after refresh.

## Restore and use a version in the workspace

**Restore as new version** copies an older version into a new current version. Existing
versions remain available. Neither saving nor restoring changes workspace files.

Choose **Use in workspace** to replace the workspace file with the current saved version.
This makes the file available to subsequent agent tool actions. Phlox checks the workspace
hash you last observed and rejects the write if the file changed. It also rejects changes
while a run, revision, or unresolved approval is active. Archived projects allow history
viewing, but block new edits, revisions, imports, restores, and workspace updates.

After a conflict, your draft or saved version remains available. Open the current file in
**Workspace Files** to inspect it, then **Reload versions** in the editor to refresh its
version list and workspace status. Compare newer versions before saving or replacing the
workspace. Download either result if you want to retain a separate local copy.

Workspace publication is an atomic file replacement within the supported single-process
Phlox deployment. It is separate from the database save: if publication fails, the saved
version remains available. External programs writing directly to the workspace do not
participate in Phlox's process lock; hash checks narrow that race but are not a filesystem
transaction with external writers. Workspace checkpoints and conversation alternatives do
not automatically change artifact history, and artifact restore does not undo external
tool actions.

## Evidence, previews, and retention

Saved answer downloads continue to use the original answer snapshot, regardless of edits
or workspace publication. Edited content is not automatically verified against the answer's
sources. Citation labels in an edited document are plain text; the original answer's source
panel remains the place to inspect evidence subject to current retention and access rules.
Version downloads do not bundle source passages or a project manifest. Use the conversation's
cited export when you need the original answer and its authorized references.

HTML uses the existing opaque sandboxed iframe preview: scripts cannot access Phlox's
parent page or app credentials, but the preview is **not an outbound-network sandbox**.
Markdown may load remote images. React/JSX builds, PDF/table previews, and a stricter preview
network policy are separate roadmap work. Binary files and oversized text remain available
through their existing download paths. Inline comparisons support up to 2,000 lines per
version and 128,000 displayed characters; larger comparisons require downloading both files.
Line-ending differences use `␍` for carriage returns and a missing-final-newline marker.

Artifacts are private to the conversation owner; administrators have no read bypass.
Version content is stored in the database, so normal database backups include it. Original
answer snapshots remain in attachment storage, so a complete backup still needs both SQL
and files. Deleting a conversation or account removes its artifact versions. Deleting an
individual message or workspace file does not remove the conversation-level version history.
Retaining versions increases database size; there is no automatic version expiry in this wave.

## Upgrade and manual verification

Schema revision **`0008_artifacts`** adds the artifact and version tables. Existing chats,
alternative selections, source records, runs, and snapshots are preserved. Normal startup
applies the checked migration; `0007_branches` remains checkable and back-upable before
upgrade. Follow [backup and restore](BACKUP_RESTORE.md) before upgrading an existing deployment.
Restart the backend, and rebuild the frontend for production. Dev servers reload changed
code; restart if the running backend has not applied the new schema. No new configuration
or model credentials are required for manual editing.

To verify with your own configured model:

1. Ask Phlox to save a short Markdown report with three paragraphs. Open it and select
   **Edit & versions**; confirm version 1 is available.
2. Edit one paragraph and save. Compare versions 1 and 2. Confirm the original answer's
   preview/download and workspace file have not changed.
3. Select **Use in workspace**, then inspect the workspace file. Restore version 1 as a
   new version and confirm the workspace still contains version 2 until explicitly updated.
4. Select a passage in the saved text, request a concise revision, inspect and discard the
   proposal. Repeat and apply it; confirm text outside the selection remains unchanged,
   then save. Test **Stop revision** during a slower response.
5. Make an unsaved edit, close the canvas, switch chats and return. Reopen the same artifact
   and confirm the draft returns. Save it before refreshing the browser.
6. Open the same artifact in two browser tabs. Save in the first; the stale second save
   must fail without losing its draft. Similarly, modify the workspace after opening the
   editor and confirm **Use in workspace** rejects the stale hash.
7. Check the editor on a narrow screen and in your preferred themes. Try a second user
   account to confirm another user's artifact URLs return 404.
