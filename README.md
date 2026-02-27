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

## Usage
```
python -m release_tool.cli \
  --artifact <artifactId> \
  --war-version <version> \
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
- `--war-version`: The released WAR version to propagate to RPM dependencies. Any `-SNAPSHOT` suffix will be dropped automatically when updating RPMs.
- `--release`: A human-friendly release identifier used to create release branches, e.g., `release-3.15` in both the WAR and the RPM repos. This is not the WAR version; it’s just the branch naming token for this coordinated release (you can use formats like `3.15` or `2026.02.25`).
- `--war-path`: Local filesystem path to the WAR repository checkout.
- `--verbose`: Print detailed progress logs.
 - `--timeout`: Per-command timeout in seconds (default: 900). Applies to git and maven steps.
 - `--skip-pull`: Skip `git pull` and remote resets; operate only on the current local state.

### Troubleshooting 401 Unauthorized from GitHub search
- Ensure the PAT has the `repo` scope (this is required to search private code).
- If your org enforces SSO, click “Enable SSO” for the token and authorize it for the organization.
- Confirm `github_org` in your config matches the org that hosts the RPM repos (e.g., `mmf-cps`).
- Validate the token with curl (see above). Expect HTTP 200; HTTP 401 indicates bad/unauthorized token.
- If still failing, regenerate a new PAT with `repo` scope and update your config file.

### What you’ll see during a run
- WAR step: creates/uses `release-<release>` and runs the Maven release with streaming logs.
- RPM discovery: lists the RPM repos to be processed.
- Per RPM: shows which repo is being updated, ensures `release-<release>` branch, prompts for confirmation, then streams the Maven release logs.
- Final Summary: lists Updated, Skipped (by user), and Failed (with reasons).
