# NodeWatch

**Status: v1 complete and release-ready.** Every command described in this
README is real, implemented, tested, and verified on a real Ubuntu 24.04.4
LTS machine (Python 3.12.3) via a genuine fresh-clone install, including a
final security audit and release-validation pass.

**Supported platform:** Linux (validated on Ubuntu 24.04 LTS). Running
`nodewatch` with no command on macOS or Windows detects this and explains
that v1 is Linux-only, rather than attempting to continue.

NodeWatch is a Python command-line tool that answers natural-language questions
about the health and state of a Linux server, using **real system data** —
not guesses, and not hallucinated facts.

You ask questions like:

- "What service failed?"
- "What is consuming memory?"
- "What ports are open?"
- "What cron jobs exist?"
- "Why might disk space be running out?"

NodeWatch collects real evidence from the Linux system, and only then hands that
evidence to an LLM to interpret and summarize. The LLM never runs commands
itself and never invents facts that aren't in the evidence.

---

## Project Documentation

This project is developed across many sessions, so it leans heavily on
written documentation to stay consistent. Start here:

- [CONTEXT.md](CONTEXT.md) — permanent architectural context: objective,
  architecture, snapshot and safety philosophy, design principles
- [PROJECT_RULES.md](PROJECT_RULES.md) — permanent engineering standards:
  style, error handling, testing, git workflow, Definition of Done
- [DECISIONS.md](DECISIONS.md) — Architecture Decision Record (ADR log)
- [ROADMAP.md](ROADMAP.md) — high-level milestones, current and future
- [LEARNING_NOTES.md](LEARNING_NOTES.md) — beginner-friendly explanations of
  every concept introduced along the way

Two other working documents — a phase-by-phase progress checklist and an
append-only development diary — are kept locally for day-to-day development
but aren't part of the published repository.

---

## Why NodeWatch exists

Diagnosing a Linux server usually means running a dozen different commands
(`systemctl`, `journalctl`, `df`, `ss`, `crontab -l`, ...), mentally piecing
the output together, and figuring out what's actually wrong. NodeWatch automates
the "gather evidence" step and adds a natural-language layer on top, so you
can ask plain-English questions and get answers grounded in what the system
actually reports.

---

## Architecture

NodeWatch is built around a **snapshot-first** design. This is a deliberate,
non-negotiable architectural choice — see [CONTEXT.md](CONTEXT.md) for the
full reasoning. In short: the tool never lets an LLM run commands live on
your system. Instead, it works in two clearly separated layers:

### Layer 1 — Data Collection

Small, independent "collectors" gather facts from the Linux system using
standard tools and files:

- `systemctl` — services and their status
- `journalctl` — system and service logs
- `ss` / `ip` — network connections and interfaces
- `df` / `du` / `findmnt` — disk usage and mounted filesystems
- `/proc` — CPU, memory, and process information
- `crontab`, systemd timers — scheduled jobs
- `iptables` / `nftables` / `ufw` — firewall rules
- filesystem metadata — permissions and ownership

Each collector writes its findings into a single structured JSON document
called a **snapshot**. Collectors are independent: if one fails (for example,
a firewall tool isn't installed), the rest of the scan still completes.

### Layer 2 — AI Interpretation

The LLM is given **only** the JSON snapshot plus the user's question. It:

- interprets the evidence,
- never executes arbitrary shell commands,
- never invents facts that aren't present in the snapshot.

### The pipeline

```
scan    → collect Linux system information → store structured JSON snapshot
report  → summarize an existing snapshot into a human-readable report
ask     → load a snapshot, send it + a question to the LLM, get an answer
```

---

## Features

- `nodewatch scan` — collect a full snapshot of system state into JSON
- `nodewatch report` — print a human-readable summary of the latest snapshot
  (`--section NAME` to see just one section, `--snapshot PATH` for a
  specific file, `--fresh` to scan first)
- `nodewatch ask <question>` — ask a natural-language question, no quotes
  needed (`nodewatch ask what service failed` works just as well as
  `nodewatch ask "what service failed"`), answered strictly from evidence
  in the snapshot. If no snapshot exists yet, `ask` scans automatically
  first — no need to run `nodewatch scan` manually before asking. An
  already-existing snapshot is reused as-is (never re-scanned
  automatically), so a run of follow-up questions stays fast.
- `nodewatch` (no command) — enter an interactive shell: type questions
  directly at the `NodeWatch>` prompt instead of repeating `nodewatch ask` each
  time (`help`, `clear`, `exit`/`quit`). On startup, detects the platform;
  on anything other than Linux, explains that NodeWatch v1 is Linux-only and
  exits cleanly rather than continuing
- `nodewatch --version` — print the installed version
- Warns when answering from a stale snapshot (older than 24 hours),
  suggesting a fresh `nodewatch scan`
- Collectors for: system metadata, CPU, memory, processes, disk, inodes,
  services, logs, network, scheduled jobs, and permissions — each one
  degrades gracefully (never crashes the scan) if a tool is missing
- Bounded, safe LLM requests: prompt size is capped, model responses are
  capped, and every OpenAI failure mode (missing key, timeout, rate limit,
  connection/server error, malformed response) is translated into a clean,
  user-facing message — never a raw exception or SDK detail

---

## Secrets & Deployment Safety

- Your OpenAI API key lives **only** in a local `.env` file (see
  `.env.example` for the exact variable name) — `.env` is listed in
  `.gitignore` and is never committed, logged, printed, or included in any
  snapshot, summary, report, or prompt. `src/nodewatch/llm/client.py` is the
  only file in the entire codebase that reads `OPENAI_API_KEY`.
- `.env.example` **is** committed — it contains only a placeholder value
  (`OPENAI_API_KEY=your_openai_api_key_here`), so a fresh clone always
  documents which variable to set without ever containing a real secret.
- API keys are **never transferred** anywhere — not to a Multipass VM
  during development/testing, not into a packaged release. If you need to
  copy this project to another machine for testing,
  [`scripts/sync_to_vm.sh`](scripts/sync_to_vm.sh) does it safely: it
  excludes `.env`, `.venv/`, `__pycache__/`, `.pytest_cache/`,
  `snapshots/*.json`, and `.git/` by construction, and refuses to proceed
  if a `.env` file somehow ends up in its staging directory anyway.
- Log messages NodeWatch collects are redacted before they ever reach a
  snapshot, a Summary, a report, or a prompt: `src/nodewatch/core/redaction.py`
  replaces API keys, tokens, passwords, and credential-shaped values
  (e.g. `OPENAI_API_KEY=sk-...` → `OPENAI_API_KEY=[REDACTED]`) and PEM
  private-key blocks — deterministically, with no false-positive
  redaction of ordinary text. The real system journal on disk is never
  modified; only NodeWatch's own collected copy is sanitized.
- A final, repository-wide secret audit (`git grep`/`git ls-files`) found
  no real secret anywhere in tracked files or git history.

---

## Folder Structure

```
NodeWatch/
├── README.md              # This file — public project documentation
├── CONTEXT.md              # Permanent project context and philosophy
├── PROJECT_RULES.md        # Engineering standards and conventions
├── DECISIONS.md            # Architecture Decision Record
├── ROADMAP.md              # High-level milestone view
├── requirements.txt        # Python dependencies
├── pyproject.toml          # Package metadata, console script, pytest config
├── .env.example            # Placeholder-only — copy to .env and fill in
├── .gitignore
├── scripts/
│   └── sync_to_vm.sh       # Safe, secret-excluding transfer to a test VM
├── docs/                   # Design notes, schemas, and architecture docs
├── snapshots/               # JSON snapshots produced by `nodewatch scan` (gitignored)
├── src/
│   └── nodewatch/              # Application source code (Python package)
│       ├── core/            # Shared execution infrastructure (runner,
│       │                    # coordinator, snapshot persistence, etc.)
│       ├── collectors/       # One module per snapshot section (all 9 built)
│       ├── cli/              # argparse CLI, interactive shell, platform
│       │                    # detection, presentation, freshness checks
│       ├── llm/               # Prompt Builder, OpenAI client, ask pipeline
│       ├── summary.py         # Summary Engine
│       └── report.py          # Report Formatter
└── tests/                    # Automated tests (pytest), mirroring src/nodewatch/
```

---

## Development Phases (all complete for v1)

NodeWatch was built in strict, incremental phases:

1. **Project architecture** — repository structure and documentation
2. **Data model** — the snapshot JSON schema
3. **Collectors** — all 9 collectors (system, CPU/memory, processes, disk,
   services, logs, network, scheduled jobs, permissions)
4. **Report generation** — the Summary Engine and Report Formatter
5. **CLI** — `scan`, `report`, `ask`
6. **LLM integration** — the Prompt Builder, the OpenAI client, and `ask`
   wired end to end
7. **UX & Robustness** — the interactive shell, platform detection, snapshot
   freshness checks, prompt/response size limits, and deployment safety
8. **Testing & Validation** — the full automated test suite plus real,
   scenario-based validation on a Multipass Ubuntu VM

See [CONTEXT.md](CONTEXT.md) for the full architectural reasoning behind
these phases, and [PROJECT_RULES.md](PROJECT_RULES.md) for coding standards.

---

## Known Limitations

- Firewall-implementation variance (`ufw`/`nft`/`iptables`) and
  non-root permission handling are already handled gracefully in code
  and were re-verified during release validation, but haven't been
  exhaustively stress-tested across every distribution.
- No demo script or slide deck exists — this README and a live
  walkthrough are the current "demo."
- See `ROADMAP.md`'s "Known Gaps, Recorded Honestly" for the complete,
  up-to-date list.

---

## Future Improvements

Ideas that are explicitly **out of scope** for v1, recorded for later:

- Support for remote hosts (currently single-server, local-only)
- Historical snapshot comparison ("what changed since yesterday?")
- Alerting / scheduled scans via cron or systemd timers
- Multiple LLM provider support
- Web-based report viewer

---

## Setup Instructions

```bash
# Clone the repository
git clone https://github.com/shrutiii-835/NodeWatch.git
cd NodeWatch

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install NodeWatch itself (editable) and its dependencies
pip install -e .
pip install -r requirements.txt

# Configure your OpenAI API key (only needed for `ask` / the interactive shell)
cp .env.example .env
# then edit .env and set OPENAI_API_KEY=<your real key>

# Run the test suite
python -m pytest

# Check the version
nodewatch --version

# Collect a snapshot and see a report
nodewatch scan
nodewatch report

# Ask a question (no quotes needed, auto-scans if no snapshot exists yet),
# or just run `nodewatch` with no command for the shell
nodewatch ask what service failed
nodewatch
```
