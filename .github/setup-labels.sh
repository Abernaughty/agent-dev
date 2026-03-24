#!/bin/bash
# Run once to create all project labels.
# Requires: gh cli (https://cli.github.com) authenticated
#
# Usage: bash .github/setup-labels.sh

REPO="Abernaughty/agent-dev"

echo "Creating labels for $REPO..."

# Type labels
gh label create "type/feature"    --repo $REPO --color 0E8A16 --description "New capability" --force
gh label create "type/bug"        --repo $REPO --color D93F0B --description "Something broken" --force
gh label create "type/task"       --repo $REPO --color 1D76DB --description "Implementation work" --force
gh label create "type/docs"       --repo $REPO --color 0075CA --description "Documentation" --force
gh label create "type/infra"      --repo $REPO --color BFD4F2 --description "CI/CD, tooling, config" --force

# Priority labels
gh label create "priority/P0"     --repo $REPO --color B60205 --description "Blocking - do now" --force
gh label create "priority/P1"     --repo $REPO --color FBCA04 --description "Important - this sprint" --force
gh label create "priority/P2"     --repo $REPO --color C2E0C6 --description "Nice to have" --force

# Component labels
gh label create "component/orchestrator" --repo $REPO --color 5319E7 --description "LangGraph state machine" --force
gh label create "component/memory"       --repo $REPO --color 5319E7 --description "Chroma/pgvector memory layer" --force
gh label create "component/sandbox"      --repo $REPO --color 5319E7 --description "E2B execution sandbox" --force
gh label create "component/mcp"          --repo $REPO --color 5319E7 --description "MCP server integrations" --force
gh label create "component/dashboard"    --repo $REPO --color 5319E7 --description "Streamlit dashboard UI" --force

# Phase labels
gh label create "phase/1-foundation"  --repo $REPO --color C5DEF5 --description "Phase 1: Foundation" --force
gh label create "phase/2-integration" --repo $REPO --color C5DEF5 --description "Phase 2: Integration" --force
gh label create "phase/3-hardening"   --repo $REPO --color C5DEF5 --description "Phase 3: Hardening" --force

# Status labels
gh label create "status/done"         --repo $REPO --color 0E8A16 --description "Completed" --force
gh label create "status/in-progress"  --repo $REPO --color FBCA04 --description "Currently being worked on" --force
gh label create "status/blocked"      --repo $REPO --color D93F0B --description "Blocked by dependency" --force

echo "Done! Labels created."

# Create milestones
echo "Creating milestones..."
gh api repos/$REPO/milestones --method POST -f title="Phase 1: Foundation" -f description="Get a single agent loop working end-to-end. LangGraph orchestrator, Chroma memory, E2B sandbox, basic MCPs, Langfuse tracing." -f state="open" 2>/dev/null || echo "Phase 1 milestone may already exist"
gh api repos/$REPO/milestones --method POST -f title="Phase 2: Integration" -f description="Connect to real toolchain. CI/CD MCP, Project Context MCP, Vercel MCP, L0 approval UI, secrets provider, cost alerting." -f state="open" 2>/dev/null || echo "Phase 2 milestone may already exist"
gh api repos/$REPO/milestones --method POST -f title="Phase 3: Hardening" -f description="Production-grade reliability. pgvector migration, self-hosted sandboxes, Terraform State MCP, circuit-breaker, CrewAI evaluation." -f state="open" 2>/dev/null || echo "Phase 3 milestone may already exist"

echo "Milestones created. Setup complete!"
