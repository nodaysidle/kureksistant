# Kurek

Kurek is a voice assistant that lives on one person's desktop. These terms name what Kurek knows about that person and how much weight each kind of knowledge carries.

## What Kurek knows

**Memory**:
What Kurek has learned about the user over time. It only grows: nothing is erased, and a correction replaces an older entry while the history stays.
_Avoid_: Long-term memory, learned facts, knowledge (for this store); "memory" as a word for every store

**Lasting fact**:
Something about the user that stays true until they change it, such as their terminal, name, city or where notes go. Kurek checks with the user before going against one.
_Avoid_: Setting, profile

**For-now instruction**:
Something the user says that holds only for the moment, such as "I'm in Koper today". Kurek follows it and doesn't save it.
_Avoid_: Temporary memory

**Correction**:
A new memory entry that replaces an older one. The older one stays in the history but no longer counts.
_Avoid_: Edit, update, overwrite

**Retraction**:
A memory entry withdrawn without being deleted. A retraction supersedes it, so neither reaches Kurek, and recall still shows both as retracted.
_Avoid_: Delete, erase, wipe

**Rule**:
A standing instruction the user wrote, kept word for word. Only the user writes rules.
_Avoid_: Preference, setting, guideline

**Pinned rule**:
A rule repeated to Kurek right after every user message, for rules he tends to drift from.

**Proposal**:
A rule Kurek suggests but cannot apply. It becomes a rule only when the user copies it in.
_Avoid_: Draft rule, pending rule

**Reference facts**:
A hand-written sheet of who the user is, their setup and their projects. Kurek reads it and never writes to it.
_Avoid_: Core memory, Muse core

**Hermes memory**:
Notes kept by Hermes, another of the user's agents. Kurek reads them and never adds to them.

**Project**:
One of the user's git repositories under `~/dev/nodaysidle`, called by its portfolio name. Nothing else counts as a project.
_Avoid_: Repo, app, workspace

**Connected file**:
A file whose name or folder clearly names a project. If Kurek isn't sure, the file is not connected and he leaves it alone.

## What was said

**Chat history**:
The last few turns of the current conversation. It is context, never fact: memory and rules outrank it.
_Avoid_: Memory, context window

**Daily log**:
A day-by-day record of everything the user and Kurek said. It is what was said, not what is true.
_Avoid_: Journal, diary

**Recall**:
Kurek looking back through the daily logs when the user refers to a past conversation, and answering with the date and what was said.
_Avoid_: Remembering (that is memory)

**Fresh start**:
Clearing the chat history on the user's request. Memory, rules and daily logs stay.
_Avoid_: Reset, wipe memory

## Retired

**Personal context** and **memory bank** are earlier stores that nothing writes to any more. Don't use either name for a current store.
