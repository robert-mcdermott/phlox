# Projects and context

Projects group private chats around selected library documents and shared instructions.
They are optional: choose **All chats / no project** in the sidebar for an ordinary chat.
Projects belong to their creator; administrators cannot read other users' projects.

## Create and use a project

1. Select **Manage projects** in the sidebar, or **Settings → Projects**. On a phone, use
   the Settings section selector.
2. Enter a name, optional description, and project instructions. For example: “Prefer
   repairable equipment; explain maintenance and operating costs.” Instructions are limited
   to 8,000 characters and do not override deployment guardrails or assistant permissions.
3. Choose up to 32 documents from your personal library and **Save project**. Upload new
   library files through **Settings → Documents**. Conversation-specific attachments and
   another user's documents cannot be linked as project knowledge.
4. Close Settings and select the project in the sidebar. **New chat** now starts a chat in
   that project. The welcome screen shows its description and document count.
5. Use the project editor to review its instructions, documents, and recent chats. Select
   an existing chat, then choose **Move current chat here** to associate it with a project.
   **Remove current chat from its project** returns it to ordinary chat organization.

Only ready documents contribute excerpts. Linked documents still processing are shown with
their status; retry failed ingestion in Documents. Linking a file does not duplicate it,
and deleting the library document removes it from future project context.

Choose **Archived** in the editor and save to preserve a finished project while removing it
from normal new-chat choices. Its overview and transcripts remain readable. Uncheck Archived
and save to continue. Resolve active runs and pending approvals before moving chats or
changing their project's settings. Concurrent edits are rejected with a reload message.

An assistant remains a reusable persona with its own capabilities and knowledge base; a
skill remains reusable workflow guidance. You can use them within a project.

## Review the next turn's context

Open **Context** above the message composer. It shows:

- The selected provider/model and endpoint host or Bedrock region, plus base instructions.
- Project instructions, with a switch to exclude them for this turn.
- Whether compatible previous turns will be included.
- The personal-memory setting and memory candidates you can exclude individually.
- Selected project documents, explicit attachments, and assistant knowledge documents,
  with per-document exclusions.

**Personal memory defaults off in projects.** Turn it on explicitly to use your personal
cross-conversation memories. Up to five relevant memories are chosen when you send; the
preview shows up to 100 candidates without embedding or generating a response. Research
never uses personal memory. Project turns cannot use `save_memory` to write project facts
into the global personal memory store; dedicated project memory is future work.

Ready project documents contribute bounded source excerpts automatically in normal chat.
The document search tool is restricted to the project's selected files, explicit attachments,
and any currently authorized assistant knowledge. It cannot widen this selection by asking
for unrelated document IDs. Explicit attachments can add another owned file for one turn.
Outside projects, ordinary document-search behavior remains available; exclusions narrow it.

Changing context selections starts a new **context segment**: earlier turns with different
project context or exclusions are not replayed to the model. Moving a chat into or out of a
project also separates its prior context. The transcript stays visible. This prevents an
old tool result or answer from silently reintroducing excluded information. Turn off
**Include previous context-compatible turns** to send only the current question and selected
context. Context compaction may summarize older compatible turns to fit the model budget.

Exclusions apply to future inputs. They do not delete transcripts, rewrite old answers,
undo earlier provider requests, or isolate the execution workspace. Changing projects does
not move or erase workspace files. Existing tool permissions and sandbox controls still
govern filesystem, shell and MCP access.

The controls reset after sending or switching chats. Regenerate and edit/resend retain the
original turn's exclusions, subject to current project and document access. New project
instructions apply to subsequent turns; changes detected during execution block further
model calls until a new turn is started with updated context.
Removing a linked project document also removes its automatic attachment on regeneration;
a file you explicitly attached remains an explicit selection until excluded or removed.

## Inspect a response's context record

Select **Context record** below a response. The record survives reload and reconnectable
run replay. It shows the prepared instructions, eligible history count, actual attempted
model calls, selected memories, and complete retained passages found in fitted outbound
inputs. Calls can include compaction, fallback models, and delegated work. The record
does not prove that a remote provider processed a request.

Prepared documents are not the same as passages supplied to a model. A tool may gather a
source before cancellation or before context fitting omits/shortens it. Only complete
retained excerpts detected in outbound input are listed as supplied. Guardrail redaction
is considered when matching; shortened excerpts are not claimed complete. Citations and
the existing source panel continue to expose collected evidence separately.

Records include at most 128 attempted calls and store references to existing source
snapshots. Source reads recheck access and retention; deleted or expired passages show as
unavailable. Deleted personal memories are redacted from record reads. Prepared instruction
and memory snapshots remain part of private conversation storage until that conversation or
account is deleted. Older responses may have no record.

The destination preview describes the primary chat provider. Existing configured fallback
providers and embedding services may also receive inputs; it is not a network-egress or
data-residency policy. The record describes model calls, not embedding requests or every
tool's external traffic. Project access rules do not replace [guardrails](GUARDRAILS.md),
[sandboxing](SANDBOX.md), or the [existing provider configuration](USER_GUIDE.md#configure-model-providers).

## Research within a project

Chat remains the default. Select Research explicitly. Web-only Research excludes project
documents; Documents or Documents + web can use selected project knowledge and attached
documents. The same document scope and assistant capability restrictions apply. Ready
project documents are included without reattaching them. Research uses the current question and permitted
sources, not prior conversation history. See [Research mode](RESEARCH.md).

## Upgrade and verify

This wave adds Alembic revision `0006_projects`: private project and context-record tables,
plus a nullable project association on conversations. Existing chats remain unassigned.
Normal startup applies the migration automatically. Preserve an offline backup before
upgrading an existing deployment; see [backup and restore](BACKUP_RESTORE.md). Do not stamp
the revision manually. Rollback uses a backup with its matching release.

Suggested manual checks:

1. Create two projects with different documents and instructions. Start two chats in the
   first project and confirm both can cite its knowledge.
2. Ask about the other project's document without attaching it. It should not be retrieved.
3. Open Context, exclude a document and the project instructions, and send another turn.
   Inspect the context record; prior incompatible turns and the excluded passage should
   not be supplied. The visible history should remain intact.
4. Enable personal memory, review/exclude a candidate, and verify the resulting record.
5. Move an existing chat into a project, then remove it. Confirm its transcript remains but
   earlier context is not automatically reused across the move.
6. Reload a finished chat and inspect its record. With reconnectable runs enabled, repeat
   across a browser refresh and an approval pause/resume.
7. Archive and restore a project. Try another user account and confirm the first user's
   projects, chats, and context records are not accessible.

Collaborative sharing, automatic decisions/tasks, dedicated project memory, project-wide
artifact editing, versioned output and conversation branching remain later roadmap work.
