---
name: check-updates
description: >
  Check an installed skills-for-fabric plugin bundle or git clone for updates,
  show the matching changelog, and provide host-appropriate update guidance.
  Use when the user wants to: (1) check for skill updates, (2) see what changed,
  (3) verify the installed version. Triggers: "check for updates", "am I up to
  date", "what version", "update skills", "show changelog".
---

# Check for Updates

This is a read-only update checker. It identifies the installation that supplied
this skill, compares its version with the repository's `main` branch, and shows
the update path supported by the current agent host. It never updates
automatically.

## Cadence

Keep these two controls separate:

- **Invocation guard:** Other Fabric skills invoke this skill once per session
  before continuing.
- **Network guard:** Automatic invocations perform a remote lookup at most once
  every 7 days for each detected installation identity.

If the user's current request directly asks for a version, changelog, or update
check, treat it as an **explicit invocation** and bypass the 7-day network guard.
If this skill was invoked only by another skill's session-start notice, treat it
as an **automatic invocation**.

Always continue with the caller's original task after an automatic invocation,
including when the check is skipped or fails.

## Procedure

### Step 1: Resolve the Installation Context

Determine one candidate installation root:

1. If the user explicitly supplied an installation path, use that exact path.
2. Otherwise, start from the active
   `<skills-root>/check-updates/SKILL.md` file.
3. When `<skills-root>` is named `skills`, consider its parent as a containing
   root. Use that containing root only if it has a supported plugin manifest or
   both `package.json` and a direct `.git` file or directory plus the positive
   skills-for-fabric Git identity defined below.
4. Otherwise, use `<skills-root>` as the candidate root. This includes personal
   copies under `~/.copilot/skills` and `~/.agents/skills`, plus project copies
   under `.github/skills`, `.agents/skills`, and `.claude/skills`.
5. If the runtime does not expose the active skill path, report the context as
   unknown. Do not guess.

Never infer the installation from the shell's current working directory. Never
walk upward beyond the candidate root. A project containing an unrelated parent
`.git` directory is not evidence that the skill came from that repository.

Resolve the runtime host independently from the installation channel:

```text
copilot-cli | claude-code | cursor | windsurf | codex | other | unknown
```

Use the host identity exposed by the current session or runtime. Do not infer it
from a plugin manifest, install path, or working directory. A deterministic test
may supply an explicit runtime-host override; normal use may not. If the host
cannot be established, use `unknown`.

For runtime detection of an already installed plugin, use the first existing
manifest in this cross-tool precedence:

1. `.plugin/plugin.json`
2. `plugin.json`
3. `.github/plugin/plugin.json`
4. `.claude-plugin/plugin.json`

In this repository, `.github/plugin/plugin.json` is the canonical source
manifest. `.plugin/plugin.json` and `plugin.json` are compatibility fallbacks
for other installed layouts or test fixtures. The final location supports
packages that expose only a Claude-compatible manifest. Do not treat this
runtime probe order as authoring guidance for this repository.

Then classify the candidate root in this order:

| Channel | Required files at the candidate root | Read |
|---|---|---|
| Plugin | one supported plugin manifest | top-level `name`, `version`, `repository` |
| Git clone | `package.json`, a direct `.git` file or directory, and a positive skills-for-fabric Git identity | top-level `version`, `repository.url` |
| Copied skills | no plugin or Git layout, plus either a root `SKILL.md` or direct child skill directories containing `SKILL.md` | tentative skill directory names only |
| Unknown | none of the exact layouts | no identity or update command |

Plugin detection takes precedence if both layouts are present. This lets a
development fixture retain plugin semantics while using a local Git remote.

A positive Git identity requires both of these checks:

- The final unscoped segment of `package.json` `name` is
  `skills-for-fabric`. Accepted examples include `skills-for-fabric`,
  `@microsoft/skills-for-fabric`, and contributor-fork scopes.
- After removing a trailing slash and `.git`, the final repository path segment
  is `skills-for-fabric`, compared case-insensitively.

If either check fails, do not promote that containing root to the Git channel
and do not run Git against it. Continue candidate-root resolution and classify
the resulting candidate independently; a nested `skills` root may therefore be
a loose copy. A generic Node repository that happens to contain a `skills/`
directory is not a skills-for-fabric installation.

A copied channel is terminal until an explicit request confirms its source:

- For an automatic invocation, do not ask a question. Optionally show one short
  note that the unverified loose copy was skipped and can be checked explicitly,
  then continue the caller's original task.
- For an explicit invocation without source confirmation, list the tentative
  copied skill names, state that source and version are unverified, ask the
  confirmation question in the copied-channel section, and stop.

Neither path continues to the network guard or remote-version steps.

Build one context object:

```text
runtimeHost: copilot-cli | claude-code | cursor | windsurf | codex | other | unknown
channel: plugin | git | copy | unknown
root: exact candidate root
installKind: marketplace | direct | none
installScope: user | project | local | managed | none
manifestName: plugin manifest name | none
installedName: marketplace entry name | none
marketplace: marketplace name | none
sourceId: direct-install source ID | none
identity: plugin:<marketplace>/<installed-name> | plugin:direct/<source-id> | git:<repository-url-as-stored>|<root>
localVersion: manifest version
repository: URL exactly as stored in the selected manifest
updateTarget: <installed-name>@<marketplace> | <manifest-name> | <root> | none
copiedSkills: direct skill directory names | none
```

For a plugin, keep the manifest name separate from the installed marketplace
entry name. They can differ. Resolve the installed entry from native runtime
metadata or the native plugin listing when it identifies the candidate root.

For GitHub Copilot CLI marketplace installs, the documented path is
`~/.copilot/installed-plugins/<marketplace>/<installed-name>`, so those two path
segments are authoritative.

For a direct Copilot CLI install at
`~/.copilot/installed-plugins/_direct/<source-id>`, set `installKind` to
`direct`, use the source ID only for the cache identity, and use the manifest
name as the bare update target. `_direct` is not a marketplace name.

For Claude Code marketplace installs, resolve the installed entry and scope from
Claude's native plugin metadata. `claude plugin list --json` can identify the
entry; the declaring settings file identifies its scope:

- `~/.claude/settings.json` -> `user`
- `.claude/settings.json` -> `project`
- `.claude/settings.local.json` -> `local`
- managed settings -> `managed`

If the same entry exists at more than one scope, list the choices and ask which
one to update. Do not guess a scope. A plugin loaded only from a skills
directory, `--plugin-dir`, or `--plugin-url` has no marketplace install record;
do not fabricate an update command for it.

If native metadata and the installed path are unavailable, use the manifest
name with marketplace `fabric-collection` only when the mapping is
unambiguous. The `fabric-skills` manifest is not unambiguous because the legacy
`skills-for-fabric` marketplace entry uses the same payload. In that case,
report that the installed entry could not be resolved, ask the user to inspect
the native plugin list, and do not guess an identity or update command.

For Git `package.json`, accept either a repository object with a `url` property
or a repository URL string. Keep the repository value exactly as stored and
append the exact resolved root when building the cache identity. This prevents
one clone from suppressing checks for another clone of the same repository.
When parsing a GitHub owner and repository for remote tools, strip only a
trailing `.git` and trailing slash. Do not alter owner spelling, case,
underscores, or punctuation.

Validate that required fields are present and that `localVersion` is semantic
version text. If validation fails, report the malformed field and classify the
context as unknown rather than using partial metadata.

### Step 2: Apply the Network Guard

Use an exact cache path supplied by the caller only for a deterministic test.
Otherwise, use this persistent cache file:

```text
~/.config/fabric-collection/last-update-check.json
```

On Windows, this is:

```text
$env:USERPROFILE\.config\fabric-collection\last-update-check.json
```

The file is a flat JSON object keyed by installation identity:

```json
{
  "plugin:fabric-collection/fabric-consumption": "2026-07-15",
  "plugin:direct/github-com-example-skills": "2026-07-15",
  "git:https://github.com/example/skills-for-fabric.git|C:\\repos\\skills-for-fabric": "2026-07-14"
}
```

Use UTC dates in `YYYY-MM-DD` format.

- For an automatic invocation, skip the remote lookup when this identity has a
  valid date within the last 7 days.
- For an explicit invocation, perform the remote lookup even when a fresh cache
  entry exists.
- Entries for different installed plugin entries or Git clone roots do not
  suppress each other.
- Preserve every unrelated entry when writing the file.
- If the file contains malformed JSON, warn and do not overwrite it.
- Do not read or write a cache entry for a copied or unknown context.

After a remote attempt completes, record today's UTC date for the detected
identity whether the attempt found an update, found no update, or failed, unless
the cache file was malformed. This prevents an automatic network failure from
repeating in every session. Do not rewrite the marker when the network guard
skipped the attempt.

### Step 3: Read the Remote Version

Read `package.json` and `CHANGELOG.md` from the same repository and the same
`main` ref. Try these methods in order and stop after the first successful one:

1. **Git CLI:** Only when the candidate root itself has a direct `.git` file or
   directory. Do not use `git rev-parse` as the availability check because it
   can discover an unrelated parent repository.

   ```bash
   git -C "<root>" fetch origin main --quiet
   git -C "<root>" show origin/main:package.json
   git -C "<root>" show origin/main:CHANGELOG.md
   ```

2. **Authenticated GitHub tools:** Use GitHub MCP file tools or `gh api` with
   the owner and repository parsed exactly from the manifest URL. Read
   `package.json` and `CHANGELOG.md` at ref `main`.
3. **Public raw content:** For a public repository, read both files from
   `https://raw.githubusercontent.com/<owner>/<repo>/main/`.

Do not use the latest GitHub Release tag as the remote version. Plugin
marketplace updates follow repository content, and a release tag can lag behind
the version available to plugin users.

If version retrieval fails, show the detected channel, identity, and local
version with a concise warning. Do not fabricate a remote version. If only the
changelog retrieval fails, still report the version comparison and note that
the changelog was unavailable.

### Step 4: Compare and Report

Compare semantic versions:

- `remoteVersion > localVersion`: update available.
- `remoteVersion <= localVersion`: up to date.

For an update, show the relevant `CHANGELOG.md` entries between the local and
remote versions, then provide only guidance verified for both the detected
channel and `runtimeHost`.

**Plugin channel**

Never emit one host's plugin command for another host.

| Runtime host | Verified guidance |
|---|---|
| GitHub Copilot CLI | For a marketplace install, show `/plugin update <installed-name>@<marketplace>`. For a direct install, show `/plugin update <manifest-name>`. |
| Claude Code | For a marketplace install with a resolved scope, show `claude plugin update <installed-name>@<marketplace> --scope <scope>`, then tell the user to run `/reload-plugins` or restart Claude Code. |
| Cursor | Direct the user to **Customize > Plugins**. For a team marketplace, an administrator can use **Refresh** or **Enable Auto Refresh**. Do not emit a plugin CLI command. |
| Windsurf, Codex, other, or unknown | Report the available version and repository, but provide no executable plugin command because none is verified for this layout. |

GitHub Copilot CLI marketplace install:

```text
/plugin update <installed-name>@<marketplace>
```

The installed marketplace entry is authoritative. For example, an installed
`fabric-consumption@fabric-collection` entry produces:

```text
/plugin update fabric-consumption@fabric-collection
```

If the installed entry is the legacy `skills-for-fabric` alias, update that
alias first so the installed entry can receive the current payload, even though
its copied manifest is named `fabric-skills`:

```text
/plugin update skills-for-fabric@fabric-collection
```

Afterward, optional migration to the canonical entry is:

```text
/plugin uninstall skills-for-fabric@fabric-collection
/plugin install fabric-skills@fabric-collection
```

GitHub Copilot CLI direct install:

```text
/plugin update <manifest-name>
```

The native CLI update target for a direct install is the bare manifest name.
Do not append `@_direct` or use the opaque source ID as the update target.

Claude Code marketplace install:

```text
claude plugin update <installed-name>@<marketplace> --scope <scope>
```

The entry name and scope must come from Claude's installed-plugin metadata. Do
not reuse Copilot's `_direct` behavior for Claude Code.

**Git channel**

```text
git -C "<detected-root>" pull --ff-only
```

**Unknown channel**

State which expected files were absent and provide no update command. Never
default to `fabric-skills`.

**Copied-skill channel**

A loose copy has no trustworthy repository, installed version, or native update
target. This includes a skill materialized from a local file or URL by
`copilot skill add` when no source metadata accompanies it.

For an automatic invocation, do not interrupt the caller with a source question.
Skip the copied-skill update flow, optionally state that an explicit update
check can identify a supported replacement, and continue the caller's task.
Do not list an executable command.

For an explicit invocation, list the tentative skill names found at the
candidate root, state that their provenance cannot be verified from the files
alone, and ask:

```text
Were these skills copied from https://github.com/microsoft/skills-for-fabric?
If yes, I can inspect the official public repository and show the supported
plugin bundle that will refresh them. I will not install or remove anything
without your confirmation.
```

Before the user confirms the source, do not contact the network, compare
versions, write cache state, or offer an executable install/update command.

After explicit confirmation:

1. Read `.github/plugin/marketplace.json` from the `main` ref of
   `microsoft/skills-for-fabric`, using authenticated GitHub tools first and
   public raw content second.
2. Match the tentative skill directory names against the skill paths inside
   each marketplace plugin source. Treat only exact path matches as evidence.
3. Prefer a focused plugin that covers all matched non-utility skills. If more
   than one plugin qualifies, list the valid choices and ask the user to choose.
   Never choose `fabric-skills` merely because `check-updates` appears in it.
4. Offer only the replacement path supported by `runtimeHost`:

   GitHub Copilot CLI:

   ```text
   /plugin marketplace add microsoft/skills-for-fabric
   /plugin install <matched-plugin>@fabric-collection
   ```

   Claude Code, using `user` for a personal copied-skills root or `project` for
   a project copied-skills root:

   ```text
   /plugin marketplace add microsoft/skills-for-fabric
   claude plugin install <matched-plugin>@fabric-collection --scope <scope>
   ```

   Tell the user to run `/reload-plugins` or restart Claude Code after the
   install. If the Claude scope cannot be resolved, ask instead of guessing.

   For Cursor, Windsurf, Codex, other, or unknown hosts, link to the official
   repository's installation instructions and provide no executable replacement
   command. The current public repository does not expose a verified native
   plugin marketplace for those hosts.

This installs a complete current bundle instead of overwriting loose files and
leaving mixed-version dependencies. Keep the loose copy until the native plugin
is installed and verified. Ask separately before removing it. If no exact
official mapping exists or the public repository is unavailable, report that
and do not fabricate a bundle or copy individual files.

## Examples

### Focused plugin bundle

```text
Host: copilot-cli
Detected: plugin:fabric-collection/fabric-consumption
Current: 0.3.7
Latest: 0.3.8
Update: /plugin update fabric-consumption@fabric-collection
```

### Claude Code plugin bundle

```text
Host: claude-code
Detected: plugin:fabric-collection/fabric-consumption
Current: 0.3.7
Latest: 0.3.8
Update: claude plugin update fabric-consumption@fabric-collection --scope user
Reload: /reload-plugins
```

### Git clone

```text
Detected: git:https://github.com/example/skills-for-fabric.git|C:\repos\skills-for-fabric
Current: 0.3.7
Latest: 0.3.8
Update: git -C "C:\repos\skills-for-fabric" pull --ff-only
```

### Unknown layout

```text
Could not determine the skills-for-fabric installation at <candidate-root>.
Expected a supported plugin manifest, or a positively identified
skills-for-fabric package.json plus a direct .git entry.
No update command was guessed.
```

### Confirmed official loose copy

```text
Host: copilot-cli
Detected loose skills: check-updates, powerbi-report-planning
Confirmed source: https://github.com/microsoft/skills-for-fabric
Supported refresh:
  /plugin marketplace add microsoft/skills-for-fabric
  /plugin install powerbi-authoring@fabric-collection
The loose files were not changed or removed.
```

## Must

- Resolve identity from the active skill root or an explicit user path.
- Resolve the runtime host independently from the installation layout.
- Keep once-per-session invocation separate from the 7-day network guard.
- Use the installed marketplace entry, not merely the manifest name, in plugin
  identities and update commands.
- Keep direct installs distinct from marketplace installs.
- Require positive package and repository identity before using the Git channel.
- Isolate Git cache entries by repository and exact clone root.
- Preserve unrelated cache entries and use UTC dates.
- Require source confirmation before looking up a loose copy in the official
  public repository.
- Match copied skill names to exact public marketplace skill paths.
- Emit only update or replacement commands verified for the runtime host.
- Continue non-blockingly after automatic checks.

## Prefer

- Use a root-local Git remote before authenticated GitHub tools.
- Fetch version and changelog from the same repository ref.
- Replace confirmed official loose copies through a complete native plugin
  bundle rather than overwriting individual files.
- Show concise, copy-pasteable output.

## Avoid

- Inferring installation context from the current working directory.
- Letting `git` discover a parent repository.
- Treating an unrelated package plus `.git` as a skills-for-fabric clone.
- Inferring the runtime host from a manifest or install path.
- Showing Copilot or Claude plugin commands to another host.
- Defaulting focused bundles to `fabric-skills`.
- Using GitHub Release tags as the plugin marketplace version.
- Repeating failed automatic network checks every session.
- Treating a same-named loose folder as proof that it came from the official
  repository.
- Replacing individual copied files without their complete bundle dependencies.
- Updating without explicit user consent.

## Error Handling

On failure, report the operation that failed and continue:

```text
Could not check plugin:fabric-collection/fabric-consumption for updates: remote package.json was unavailable.
Current installed version: 0.3.7
The automatic check is non-blocking; continuing with the requested task.
```

If the user says only "update my skills", clarify whether they want to check for
an update or execute the detected native update command. Do not execute an
update based on ambiguous intent.
