# Release Automation

The Release Branch Helper workflow assembles every Jira ticket for a fixVersion, lists all merged PRs per ticket, and prints a ready-to-run `git cherry-pick` command so you can build the release branch locally without mutating the repo from CI.

## TL;DR

1. Open **Actions → Release Branch Helper → Run workflow**
2. Enter `fix_version`, `base_release`, `target_release` (format: `release/YYYY-MM-DD`)
3. Wait for the run, copy the cherry-pick command from the summary, run it locally, push the new branch

## Run the Release

1. **Prerequisites**
   - Repo secrets: `JIRA_EMAIL`, `JIRA_API_TOKEN`.
   - Ticket naming: every merged PR must mention its Jira key (e.g., `KNCK-123`) in the title or body so GitHub search can find it.
   - Branch naming: `base_release` and `target_release` must follow `release/YYYY-MM-DD`; tags are auto-derived as `Release_YYYY-MM-DD`.
   - Project: the workflow always queries the Knock CRM Jira project (`KNCK`). Any Jira board URL that includes `/projects/KNCK/` confirms the key.
2. **Trigger the workflow**
   - Open **Actions → Release Branch Helper → Run workflow**.
   - Provide the inputs:
     - `fix_version`: exact Jira fixVersion name.
     - `base_release`: branch to start from (e.g., `release/2025-01-10`).
     - `target_release`: branch you plan to create (e.g., `release/2025-02-07`).
3. **Consume the results**
   - Job summary shows the release matrix plus the cherry-pick command (copy/paste locally). If tickets reference commits in other repositories, the summary now appends extra `git cherry-pick` lines labeled per repo so you can apply them where they belong.
   - Artifacts include `ticket-matrix.md`, `cherry-pick-command.txt`, and the raw `release_payload.json`.
4. **Build the branch locally**
   - Run the command from the summary/artifact, resolve conflicts if necessary, push the new branch, and continue the usual release steps (see `docs/TEAM_WORKFLOW.md`).

## How It Works

1. **Fetch Jira Tickets**
   - Validates inputs and secrets, enforces the `release/YYYY-MM-DD` naming, derives `Release_YYYY-MM-DD` tags.
   - Queries Jira project `KNCK` for all issues with the specified fixVersion.
   - Uses each ticket key to find every merged PR in this repo and captures all associated commit SHAs (deduped, ordered).
   - Stores the combined data in `release_payload.json`.
2. **Build Release Matrix**
   - Reads the payload and renders the Markdown matrix plus an ordered cherry-pick command.
   - Writes those into the GitHub Actions job summary for instant visibility.
   - Uploads the matrix, command, and payload as workflow artifacts for documentation or further automation.
