# PROJECT CONTEXT

## Project Name
AI Created Oracle Troubleshooting Analysis Tools

---

# Project Goal

This project provides AI-assisted troubleshooting, diagnostics, log analysis,
automation, and operational tooling for Oracle-related environments.

Primary focus areas:
- Exadata troubleshooting
- Exadata storage server diagnostics
- Exadata performance and wait-event analysis
- Exadata cell, network, and RAC-related issue investigation
- Database diagnostics
- Log parsing and correlation
- AI-assisted operational analysis
- Automation tooling
- Incident investigation workflows

---

# High-Level Architecture

Main components:

- frontend/
  UI and visualization layer

- backend/
  Core APIs and orchestration

- agents/
  AI agents and autonomous workflows

- parsers/
  Log parsers and data extraction

- analyzers/
  Root cause analysis modules

- prompts/
  Reusable AI prompts

- scripts/
  Operational and utility scripts

- tests/
  Automated tests

---

# Current Development Priorities

Current active goals:

1. Improve log correlation engine
2. Reduce AI hallucinations in troubleshooting output
3. Add OCI-aware diagnostics
4. Improve parser modularity
5. Add automated remediation suggestions

---

# Coding Standards

## General
- Prefer simple and maintainable solutions
- Avoid unnecessary abstractions
- Keep functions small and focused
- Write readable code over clever code

## Python
- Use type hints
- Prefer pathlib over os.path
- Use dataclasses where appropriate
- Follow PEP8

## Logging
- Use structured logging
- Never print directly in production code

## Error Handling
- Fail clearly and explicitly
- Avoid silent exceptions

---

# AI Agent Rules

When generating code:

- Preserve existing architecture
- Avoid large refactors unless requested
- Do not rewrite unrelated files
- Keep diffs minimal
- Prefer incremental improvements
- Ask before introducing new dependencies

When analyzing logs:
- Focus on actionable findings
- Avoid speculative conclusions
- Clearly separate facts vs assumptions

---

# Git Workflow

## Branch Strategy

- main = stable
- dev-mac = local macOS development
- dev-linux = remote Linux development

Feature branches:
- feature/<name>
- fix/<name>

## Commit Style

Examples:
- add Exadata log parser
- improve memory handling
- fix parser timeout issue

Keep commits small and focused.

## Base Git Flow

Fluxo Git que funciona MUITO bem.

### No Mac

Use the local macOS development branch:

```bash
cd /Users/pporacle/Documents/GitHub/AI_Created_Oracle_Troubleshooting_Analysis_Tools

git checkout dev-mac
```

Trabalha normalmente.

Commit pequeno e frequente:

```bash
git add .
git commit -m "Refactor parser module"
git push
```

### No Linux

Use the remote Linux development branch:

```bash
ssh paportug@phoenix93718.dev3sub2phx.databasede3phx.oraclevcn.com

cd /home/paportug/AI_Created_Oracle_Troubleshooting_Analysis_Tools

git checkout dev-linux
git pull
```

Depois:

```bash
git cherry-pick
```

---

# Environment Information

## macOS Local

Path:
/Users/pporacle/Documents/GitHub/AI_Created_Oracle_Troubleshooting_Analysis_Tools

Main usage:
- Architecture
- Refactoring
- UI
- Prompt engineering
- Codex App

---

## Linux Remote

Host:
phoenix93718.dev3sub2phx.databasede3phx.oraclevcn.com

Path:
/home/paportug/AI_Created_Oracle_Troubleshooting_Analysis_Tools

Main usage:
- Codex CLI
- Long-running tasks
- Automation
- Testing
- Log analysis
- Batch execution

---

# Important Commands

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
