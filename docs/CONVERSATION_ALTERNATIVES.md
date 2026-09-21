# Conversation alternatives

Editing a prompt or regenerating an answer preserves the original conversation. Use the
previous/next controls below a message (for example, **2 of 3**) to select an alternative
and continue from it. No configuration option is needed; normal Chat remains the default.

## Edit, retry, and return

- **Regenerate** beneath the latest answer requests another answer to the same question.
  The original answer is retained. If the provider cannot start, the original stays selected;
  a saved partial or stopped answer may appear as another alternative.
- **Edit → Save & resend** on a user message creates a revised prompt and a new continuation.
  The original question and everything following it remain accessible through the prompt's
  alternative controls. Images, explicit documents, skills, Research selection, and context
  exclusions are carried forward and checked against current access rules.
- **Previous/next** selects a saved alternative and its continuation. Phlox remembers the
  previously selected continuation beneath each alternative, including nested choices.
  Selection survives reload and does not make a model call. Sending afterward does.

Only the selected message path is eligible for model context and Markdown export. Project
context compatibility, memory choices, guardrails, and model context limits still apply.
Other chats in a project are not automatically replayed. Editing and retrying use current
project instructions, current document access, and the currently selected model; the original
answer keeps its own model, usage, citations, and context record. Usage includes every model
attempt, regardless of the alternative currently displayed.

There is no tree diagram or branch merge operation. The compact message controls are the
navigation for this first version. Simultaneous model comparisons are future work.

## Runs and approvals

Resolve active work before editing, regenerating, or switching alternatives. This applies
to request-bound streaming, reconnectable runs, pending approvals, and interrupted runs
that still need acknowledgement. Another tab cannot change the selected path during a run.
A stale tab receives a reload error instead of silently changing a newer selection.

Approval snapshots and durable runs keep the message parent for their answer. Refreshing,
reconnecting, or resuming an approval continues that attempt; it does not create another
alternative by itself. Selecting an existing alternative never repeats its tool actions.

## Workspace files and saved output

**Workspace files are shared between alternatives.** Changing the selected conversation
path does not undo filesystem changes, restore a checkpoint, or reverse external actions.
Use the existing checkpoint controls for an explicit workspace restore.

Newly completed answer artifacts have a bounded saved copy when the file is available:

- A **Saved with this answer** label means downloads and canvas previews open the retained
  bytes, even if another attempt later overwrites that path in the workspace.
- Snapshots capture files as they exist when the answer is finalized, including partial
  answers finalized after Stop. They do not preserve every intermediate tool write.
- Repeated tool updates to the same full file path produce one answer card and one final
  snapshot/version. Individual writes remain in the tool history. Older answers with
  duplicate entries also display one card per path without rewriting their saved records.
- The limits are 32 MiB per file and 64 MiB per answer. Files over the limit
  and older artifacts without snapshot metadata show **Current workspace file · no saved copy**. Their links use the
  current workspace and can fail if the file was removed. Historical bytes cannot be
  reconstructed from an old link alone.
- Files missing or unreadable during snapshot capture show **Unavailable when this answer
  was saved · no saved copy**, with preview/download disabled. Temporary files deleted by
  the agent can appear this way; their existence earlier does not mean they remain in the workspace.
- Workspace Files continues to show current files. A saved answer copy is separate from
  an editable workspace file. [Editable artifacts](ARTIFACTS.md) adds version history,
  selected-passage revision, comparison, and explicit workspace publication for text files.

Snapshots remain private under the conversation's ownership checks. Source citations still
obey their existing access/deletion/expiry rules; preserving an answer does not bypass them.
Snapshots use the existing attachment storage, are included in normal backups, and are
removed with the message/conversation/account. Keeping alternatives consumes additional
transcript and file storage.

## Upgrade

Schema revision `0007_branches` adds message ancestry and saved conversation selection.
It converts each existing linear conversation into one path, preserving messages, usage,
projects, source records, and approvals. Normal startup applies the checked migration.
Preserve an offline backup before updating an existing deployment; see
[backup and restore](BACKUP_RESTORE.md). Do not stamp manually. Restart the backend and
rebuild the frontend for production; the development servers reload their changed code.

The existing explicit message-delete API still deletes the selected message and its
descendants. Edit and Regenerate no longer call it. Deleting a conversation removes all
of its alternatives. Rollback requires a backup and its matching application release.

## Manual verification

1. Ask a question, then Regenerate. Confirm **2 of 2** appears and both answers can be
   selected. Reload and confirm the selected alternative survives.
2. Continue one alternative, switch to the other, and continue differently. Return to each
   and check that it retains its own follow-up messages.
3. Edit an earlier question. Return to the original prompt and check the full old
   continuation. Try an image or document attachment and confirm it survives the edit.
4. Repeat in a project, then with Research explicitly selected. Inspect citations and
   Context records, and export each selected path separately.
5. Stop a regeneration, or temporarily select an unavailable model and retry. Confirm the
   original answer remains accessible.
6. Generate a small report file, then ask for another version at the same workspace path.
   Confirm each saved answer opens its own copy, while Workspace Files opens the latest.
7. With reconnectable runs enabled, refresh during a retry and pause for a tool approval.
   Confirm navigation is unavailable until the work is resolved and the resumed answer
   belongs to the correct question.
8. Use a second tab to change selection, then try sending from the stale tab. Reload after
   the conflict. Check the controls on a phone and with keyboard navigation.
