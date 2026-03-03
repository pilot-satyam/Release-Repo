# Release Tool

## Setup
1. Create config file at `~/.config/release-tool/config.json`:
   ```json
   {
     "github_token": "<PAT>",
     "github_org": "mmf-cps",
     "workspace_dir": "/tmp/release-workspace"
   }
   ```
2. Install Python dependencies:
   ```bash
   pip install lxml
   ```

### Configure your GitHub token (PAT)
- Create a Personal Access Token (classic):
  - GitHub.com: Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token.
  - GitHub Enterprise Server (e.g., github.cerner.com): User menu → Settings → Developer settings → Personal access tokens → Generate new token.
- Select scopes:
  - Required: `repo` (to search and clone private repositories in the org)
  - Recommended: `read:org` (helps with org-scoped queries on some setups)
- If your organization uses SSO, be sure to “Authorize” the token for your org after creating it.
- Put the token in `~/.config/release-tool/config.json` under `github_token` and set `github_org` appropriately.

To verify the token works, try a code search via curl (replace ORG and ARTIFACT):
```
curl -H "Authorization: token <YOUR_TOKEN>" \
  "https://api.github.com/search/code?q=org:ORG+filename:pom.xml+ARTIFACT"
```
You should get HTTP 200 with JSON results. If you see 401, re-check scopes and SSO authorization.

### SSH-only environments (no HTTP Git)
- This tool performs Git operations (clone/fetch/push) over SSH only. We derive SSH remotes from repository metadata (e.g., `git@github.cerner.com:org/repo.git`).
- PRs are created using the GitHub CLI (`gh pr create`). While the Git API itself is HTTPS, your Git remotes remain SSH. Ensure `gh` is authenticated to your Enterprise host and set to use SSH for Git operations:
  ```bash
  gh auth login --hostname github.cerner.com --web
  gh config set -h github.cerner.com git_protocol ssh
  ```
- If your local repo remotes are HTTP(S), update them to SSH to avoid HTTP Git usage:
  ```bash
  git remote set-url origin git@github.cerner.com:org/repo.git
  ```
  The tool will still function, but SSH remotes are recommended and expected in restricted environments.

## Usage
```
python -m release_tool.cli \
  --artifact <artifactId> \
  [--war-version <version>] \
  --release <release-number> \
  --war-path /path/to/war/repo \
  [-v|--verbose]
```

The tool will:
1. Create `release-<release>` branch in WAR repo and run Maven release prepare.
2. Discover RPM repos consuming the artifact.
3. For each RPM: create release branch, update dependency version (dropping `-SNAPSHOT`), and run Maven release prepare.

Use `-v/--verbose` for detailed logging (git commands, repo names, etc.).

Before each RPM’s Maven release step, the CLI prompts:
```
Proceed with Maven release for <repo>? (yes/no):
```
Answer `yes` to continue or `no` to skip that repo’s release.

### Flag reference
- `--artifact`: The WAR artifactId to update in RPM POMs (e.g., `mmf-cps-storage-content-app-spot-tomcat`).
- `--war-version`: Optional. If omitted, the tool derives the current `<version>` from the WAR pom.xml and drops `-SNAPSHOT` for the release. The WAR release is prepared with this exact version.
- `--release`: A human-friendly release identifier used to create release branches, e.g., `release-3.15` in both the WAR and the RPM repos. This is not the WAR version; it’s just the branch naming token for this coordinated release (you can use formats like `3.15` or `2026.02.25`).
- `--war-path`: Local filesystem path to the WAR repository checkout.
- `--verbose`: Print detailed progress logs.
 - `--timeout`: Per-command timeout in seconds (default: 900). Applies to git and maven steps.
 - `--skip-pull`: Skip `git pull` and remote resets; operate only on the current local state.
 - `--sync-strategy`: How to sync release branches with origin (`rebase` default, or `merge`).
 - `--rpm-war-version`: Force a specific WAR version in RPM POMs (overrides the released tag detected from `release.properties`).
 - `--stop-after-war`: Run only the WAR release stage and exit (useful if you want to wait for artifact publication before running RPM updates later).
 - `--list-consumers`: List RPM repositories that consume `--artifact` and exit (no changes made).
 - `--list-jar-consumers`: List both WAR and RPM repositories that consume the given JAR `--artifact` and exit (no changes made).

### Troubleshooting 401 Unauthorized from GitHub search
- Ensure the PAT has the `repo` scope (this is required to search private code).
- If your org enforces SSO, click “Enable SSO” for the token and authorize it for the organization.
- Confirm `github_org` in your config matches the org that hosts the RPM repos (e.g., `mmf-cps`).
- Validate the token with curl (see above). Expect HTTP 200; HTTP 401 indicates bad/unauthorized token.
- If still failing, regenerate a new PAT with `repo` scope and update your config file.

### List RPM consumers (discovery only)
To see which RPM repos (packaging=rpm) currently reference your WAR artifact in their pom.xml files, run:
```
python -m release_tool.cli \
  --artifact <artifactId> \
  --release <release-number> \
  --list-consumers \
  --verbose
```
This prints a count and the repo names with their SSH URLs. No branches are created and no changes are made in this mode. `--war-path` is not required for discovery.

### List JAR consumers across WAR and RPM (discovery only)
To see which WAR and RPM projects consume a given JAR artifactId in their pom.xml files, run:
```
python -m release_tool.cli \
  --artifact <jar-artifactId> \
  --release <release-number> \
  --list-jar-consumers \
  --verbose
```
This prints two sections:
- WAR projects consuming <jar-artifactId>: N
- RPM projects consuming <jar-artifactId>: M

No branches are created and no changes are made in this mode. `--war-path` is not required for discovery.

### Releasing just the WAR first (then RPMs later)
If your artifact repository publishes the WAR asynchronously, run the WAR stage only:
```
python -m release_tool.cli \
  --artifact <artifactId> \
  --release <release-number> \
  --war-path /path/to/war/repo \
  --stop-after-war \
  --verbose
```
Later, once the WAR version is available in your repos, rerun without `--stop-after-war` to update and release the RPMs. If needed, you can override the RPM dependency version with `--rpm-war-version <released-war-version>`.

### Dynamic WAR version handling
- If `--war-version` is omitted, the tool reads the current `pom.xml` version and strips `-SNAPSHOT` to compute the planned release version. It then calls Maven Release Plugin with `-DreleaseVersion=<planned>` and auto-bumps the next development `-SNAPSHOT` version.
- The RPM step uses the released tag from `release.properties` (scm.tag) if present; otherwise it falls back to `--war-version` or the derived version. You can always force a specific version via `--rpm-war-version`.

### lxml import error
If you see `ModuleNotFoundError: No module named 'lxml'`, install it in your active virtualenv:
```
pip install lxml
```

### What you’ll see during a run
- WAR step: creates/uses `release-<release>` and runs the Maven release with streaming logs.
- RPM discovery: lists the RPM repos to be processed.
- Per RPM: shows which repo is being updated, ensures `release-<release>` branch, prompts for confirmation, then streams the Maven release logs.
- Final Summary: lists Updated, Skipped (by user), and Failed (with reasons).
