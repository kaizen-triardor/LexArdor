# LexArdor — Deployment & Protection Architecture

## How It Ships

Each client gets a **Mac laptop** (M4, 32GB RAM) with LexArdor pre-installed.

```
┌──────────────────────────────────────────────────────┐
│  macOS User Account: "lexardor" (hidden service)     │
│                                                       │
│  /opt/lexardor/                                       │
│    ├── bin/          ← Compiled binary (Nuitka)       │
│    ├── models/       ← GGUF model files (read-only)   │
│    ├── core-data/    ← Law corpus ChromaDB (read-only) │
│    ├── client-data/  ← Client's documents (read-write) │
│    ├── db/           ← SQLite (users, chats)          │
│    └── config/       ← .env, license key              │
│                                                       │
│  Auto-starts on boot via LaunchDaemon                 │
│  Runs on localhost:8080 (no internet needed)          │
│  Tailscale for remote management                      │
└──────────────────────────────────────────────────────┘
```

## 3 Layers of Protection

### Layer 1: Compiled Binary (code protection)
- Python → compiled binary via **Nuitka** (C compilation, not just bytecode)
- Clients see a single executable, not Python source files
- System prompts, RAG logic, embedding config all baked into binary
- Can't be easily decompiled or modified

### Layer 2: Separate Data Volumes (tamper protection)
- **Core corpus** (laws): Read-only, owned by service account
  - ChromaDB at `/opt/lexardor/core-data/`
  - Client cannot modify, delete, or corrupt the law database
  - Only Stefan can update via remote access
- **Client documents**: Read-write, client can manage via UI
  - Separate ChromaDB collection at `/opt/lexardor/client-data/`
  - Upload contracts, cases, memos through the dashboard
  - Client can add/delete their own docs freely
- **Models**: Read-only GGUF files, no modification possible

### Layer 3: License & Remote Access
- **License key** in config — app checks on startup
- **Tailscale** installed — always-on VPN for remote management
  - Stefan can SSH in to push updates, fix issues, add laws
  - Zero-config, works through NAT/firewalls
  - Client doesn't need to do anything
- **Auto-update endpoint** — app checks for updates on Tailscale network

## Remote Update Flow

```
Stefan's PC                          Client's Mac
    │                                      │
    ├─ Tailscale VPN ─────────────────────┤
    │                                      │
    ├─ SSH into client Mac                 │
    ├─ Stop LexArdor service               │
    ├─ rsync new binary / law updates      │
    ├─ Run migration script if needed      │
    ├─ Restart service                     │
    │                                      │
    └─ Verify via /api/health              │
```

## Client Document Management (via UI)

Dashboard gets a "Moji dokumenti" (My Documents) tab:
- Upload PDF/DOCX/TXT
- View uploaded documents
- Delete own documents
- Search across own documents + core laws
- RAG searches BOTH collections (core laws + client docs)
