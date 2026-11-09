<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Harden-runner egress allow-lists

Each file under this directory is the egress allow-list for one job,
at `<workflow>/<job id>.txt`. The job runs
[step-security/harden-runner](https://github.com/step-security/harden-runner)
with `egress-policy: block`, so it can reach only the endpoints its
list names, plus the GitHub Actions service hosts harden-runner adds
itself (artifacts, caches, results, codeload).

## How a job loads its list

The job's first two steps are:

1. [lfreleng-actions/harden-runner-block-action](https://github.com/lfreleng-actions/harden-runner-block-action),
   with `config` naming this repository, the job's list and
   `github.sha`. It fetches the list in its `pre:` hook, checks every
   entry and the token grammar, drops any hostname without a DNS A
   record (which would otherwise make harden-runner fail open), and
   publishes the list as `CONNECTION_ALLOW_LIST`. Its main step then
   waits until harden-runner's agent reports that block mode is in
   force, so no later step races the agent's start-up.
2. `step-security/harden-runner` with `egress-policy: block` and
   `allowed-endpoints: ${{ env.CONNECTION_ALLOW_LIST }}`.

Reading the list at `github.sha` means a pull request that changes a
list is tested with its own list. This grants a pull request nothing
it could not already do: on `pull_request` the workflow itself comes
from the same merge commit. On `pull_request_target`, `github.sha` is
the base branch, so a fork cannot widen the list.

## Updating a list

Lists come from harden-runner telemetry, not guesses. The
`Post Run step-security/harden-runner` log of every job lists each
`endpoint called` with its port and calling process, and a blocked
call shows up there too. When a job fails on a blocked endpoint:

1. Find the endpoint in that log, or in the insights link
   harden-runner prints.
2. Add `host:port` to the job's list with a comment naming the caller.
3. Push; the pull request's own run proves the change.

Keep one `host:port` entry per line. Prefer exact hosts: a `*.host`
wildcard opens only the first port listed for it.

## Jobs still in audit mode

A job that never runs on a pull request cannot prove its list before
the list gates real work, so it stays in `audit` mode. A comment beside
its harden-runner step lists the endpoints seen in its telemetry, ready
to become a list here once a run confirms them:

- `release.yaml`: every job (tag pushes only).
- `release-drafter.yaml`: pushes to `main` only.
- `build-test.yaml` `clear-cache`: manual dispatch only.

## Reusable workflows

The reusable workflows called from `codeql.yml`,
`openssf-scorecard.yaml`, `semantic-pull-request.yaml` and
`linting.yaml` run their own harden-runner steps in block mode against
the `lfreleng-actions` shared allow-list. Linting defaults to audit, so
its caller sets `harden_runner_egress: 'block'`.
