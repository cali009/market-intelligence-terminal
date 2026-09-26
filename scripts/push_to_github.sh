#!/usr/bin/env bash
# Push to GitHub & Trigger GitHub Pages Deployment
set -e

if [ -z "$1" ]; then
    if [ -f "deploy" ]; then
        # Load credentials from deploy file
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
        echo "Or with PAT: ./scripts/push_to_github.sh https://<TOKEN>@github.com/your-username/market-intel.git"
        exit 1
    fi
else
    REPO_URL="$1"
fi

echo ">>> Setting git remote origin to $REPO_URL..."
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO_URL"

echo ">>> Pushing main branch to GitHub..."
git branch -M main
git push -u origin main --force

echo ""
echo "======================================================================"
echo " SUCCESS: Code pushed to GitHub!"
echo " GitHub Pages will now automatically build and deploy your web app via"
echo " the workflow in .github/workflows/pages.yml."
echo "======================================================================"
