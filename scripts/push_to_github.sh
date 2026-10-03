#!/usr/bin/env bash
# Push to GitHub & trigger the Firebase Hosting deployment.
#
# SAFETY: this script used to run `git push --force` unconditionally. In this workspace the
# local .git state can silently revert to an older commit (the snapshot mechanism excludes
# .git/config, and the object store has been observed rolling back). A blind force-push in
# that state overwrites the remote's newer commits and destroys work that exists nowhere
# else. This version fetches first and refuses to force-push over commits the local branch
# does not already contain.
#
# To force anyway, after confirming the remote commits are genuinely unwanted:
#     ALLOW_FORCE_PUSH=1 ./scripts/push_to_github.sh
set -e

if [ -z "$1" ]; then
    if [ -f "deploy" ]; then
        AUTH_URL=$(grep '^AUTH_REPO_URL=' deploy | cut -d'=' -f2-)
        if [ -n "$AUTH_URL" ]; then
            REPO_URL="$AUTH_URL"
        else
            TOKEN=$(grep '^GITHUB_TOKEN=' deploy | cut -d'=' -f2-)
            BASE_URL=$(grep '^REPO_URL=' deploy | cut -d'=' -f2- | sed 's#https://##')
            REPO_URL="https://${TOKEN}@${BASE_URL}.git"
        fi
    else
        echo "Usage: ./scripts/push_to_github.sh <GITHUB_REPO_URL>"
        echo "Example: ./scripts/push_to_github.sh https://github.com/your-username/market-intel.git"
        exit 1
    fi
else
    REPO_URL="$1"
fi

MASKED_URL=$(echo "$REPO_URL" | sed -E 's#(https?://)[^@]+@#\1***@#')
echo ">>> Setting git remote origin to $MASKED_URL..."
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO_URL"

git branch -M main

# ---------------------------------------------------------------------------
# Reconcile with the remote BEFORE pushing. This is the step that prevents a
# reverted local checkout from clobbering newer remote history.
# ---------------------------------------------------------------------------
echo ">>> Fetching remote state..."
git fetch origin main --quiet 2>/dev/null || true

LOCAL_SHA=$(git rev-parse HEAD)
REMOTE_SHA=$(git rev-parse origin/main 2>/dev/null || echo "")

if [ -z "$REMOTE_SHA" ]; then
    echo ">>> No remote main branch found; this will be the initial push."
    PUSH_ARGS="-u origin main"
elif [ "$LOCAL_SHA" = "$REMOTE_SHA" ]; then
    echo ">>> Local and remote are identical ($LOCAL_SHA). Nothing to push."
    exit 0
elif git merge-base --is-ancestor "$REMOTE_SHA" "$LOCAL_SHA"; then
    echo ">>> Remote ($(git rev-parse --short "$REMOTE_SHA")) is an ancestor of local; fast-forward push."
    PUSH_ARGS="-u origin main"
elif git merge-base --is-ancestor "$LOCAL_SHA" "$REMOTE_SHA"; then
    echo ""
    echo "!!! REFUSING TO PUSH: your local branch is BEHIND the remote."
    echo "    local  HEAD : $(git rev-parse --short "$LOCAL_SHA")"
    echo "    remote main : $(git rev-parse --short "$REMOTE_SHA")"
    echo ""
    echo "    The remote has commits you do not have locally -- this is the signature of a"
    echo "    reverted local checkout. Pushing would discard them."
    echo ""
    echo "    Recover with:"
    echo "        git fetch origin main && git reset --mixed origin/main"
    echo "    (use --hard only if you want to discard local working-tree changes too)"
    echo ""
    exit 1
else
    echo ""
    echo "!!! REFUSING TO PUSH: local and remote have DIVERGED."
    echo "    local  HEAD : $(git rev-parse --short "$LOCAL_SHA")"
    echo "    remote main : $(git rev-parse --short "$REMOTE_SHA")"
    echo ""
    echo "    Commits on the remote that are not in your local branch:"
    git log --oneline "$LOCAL_SHA".."$REMOTE_SHA" 2>/dev/null | sed 's/^/        /' || true
    echo ""
    if [ "${ALLOW_FORCE_PUSH:-0}" = "1" ]; then
        echo ">>> ALLOW_FORCE_PUSH=1 set; force-pushing anyway. The commits above will be"
        echo "    discarded from the remote."
        PUSH_ARGS="-u origin main --force"
    else
        echo "    Reconcile first (fetch + merge or rebase), or re-run with"
        echo "    ALLOW_FORCE_PUSH=1 if you are certain those remote commits are unwanted."
        exit 1
    fi
fi

echo ">>> Pushing main branch to GitHub..."
git push $PUSH_ARGS

echo ""
echo "======================================================================"
echo " SUCCESS: Code pushed to GitHub ($(git rev-parse --short HEAD))."
echo " The workflow .github/workflows/firebase_deploy.yml runs on push to main:"
echo " it re-runs the daily pipeline, rebuilds public/, and deploys to Firebase"
echo " Hosting if FIREBASE_SERVICE_ACCOUNT or FIREBASE_TOKEN is configured."
echo "======================================================================"
