#!/usr/bin/env bash
# Persist source state on its own Git branch, independently of expiring caches.
# Run from the main checkout. This script is used by the Pages workflow.
set -euo pipefail

operation="${1:?Expected restore or save}"
state_worktree="${2:?Expected an absolute worktree directory}"
state_file="${3:?Expected an absolute state JSON path}"
state_branch="bondscope-data"

if [[ "$state_worktree" != /* || "$state_file" != /* ]]; then
  echo "State worktree and state file paths must be absolute" >&2
  exit 2
fi

case "$operation" in
  restore)
    # A failed remote lookup aborts the job. It must not be confused with a
    # genuinely absent data branch and silently reset last-good observations.
    remote_heads="$(git ls-remote --heads origin "refs/heads/$state_branch")"
    if [[ -n "$remote_heads" ]]; then
      git fetch --no-tags origin "refs/heads/$state_branch"
      git worktree add --detach "$state_worktree" FETCH_HEAD
      if [[ ! -f "$state_worktree/state.json" || -L "$state_worktree/state.json" ]]; then
        echo "The data branch has no regular state.json; refusing to reset source data" >&2
        exit 1
      fi
      cp "$state_worktree/state.json" "$state_file"
      echo "Restored source state from $state_branch"
    else
      if [[ -e "$state_file" ]]; then
        echo "Unexpected pre-existing restore file; refusing to use stale local state" >&2
        exit 1
      fi
      git worktree add --detach "$state_worktree" HEAD
      git -C "$state_worktree" switch --orphan "$state_branch"
      echo "First publication: no persisted source state yet"
    fi
    ;;
  save)
    if [[ ! -f "$state_file" || -L "$state_file" || ! -f "$state_worktree/.git" ]]; then
      echo "Expected a built source-state JSON and an existing worktree" >&2
      exit 1
    fi
    cp "$state_file" "$state_worktree/state.json"
    git -C "$state_worktree" add -- state.json
    if git -C "$state_worktree" diff --cached --quiet; then
      echo "Persisted source state is unchanged"
    else
      git -C "$state_worktree" \
        -c user.name='github-actions[bot]' \
        -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
        commit -m 'Update bond market source state'
      state_commit="$(git -C "$state_worktree" rev-parse HEAD)"
      # Push from the main checkout so checkout's scoped credentials apply even
      # when the detached state worktree lives outside GITHUB_WORKSPACE.
      git push origin "$state_commit:refs/heads/$state_branch"
    fi
    ;;
  *)
    echo "Expected restore or save" >&2
    exit 2
    ;;
esac
