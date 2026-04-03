# LexArdor v2 — Functional Validation Report

**Date:** 2026-03-31
**Server:** localhost:8080 | LLM: Qwen 3.5 9B Q8 on localhost:8081
**Corpus:** 79,111 articles

## Summary: 58/58 PASSED (100.0%)

| Status | Test | Detail |
|--------|------|--------|
| ✅ | Health check | 200 (0.02s) |
| ✅ | App info | 200 (0.00s) |
| ✅ | List models | 200 (0.01s) |
| ✅ | Engine config | 200 (0.00s) |
| ✅ | Current model | 200 (0.01s) |
| ✅ | BM25 status | 200 (0.00s) |
| ✅ | Diagnostics | 200 (0.00s) |
| ✅ | Set model (valid) | 200 (0.00s) |
| ✅ | Set model (invalid role) | 400 (0.00s) |
| ✅ | Corpus stats | 200 (0.02s) |
| ✅ | Corpus summary | 200 (0.00s) |
| ✅ | Corpus freshness | 200 (0.00s) |
| ✅ | List laws | 200 (0.26s) |
| ✅ | Law articles (advokatska_tarifa_tabelarni_pr) | 200 (0.00s) |
| ✅ | Article detail (Čl. 1) | 200 (0.00s) |
| ✅ | Law versions (advokatska_tarifa_tabelarni_pr) | 200 (0.00s) |
| ✅ | Citation graph (advokatska_tarifa_tabelarni_pr) | 200 (0.00s) |
| ✅ | Corpus search (BM25) | 200 (0.08s) |
| ✅ | Corpus search short query | 400 (0.00s) |
| ✅ | Compare (missing params) | 400 (0.00s) |
| ✅ | Explain article (AI) | 200 (4.40s) |
| ✅ | List chats | 200 (0.00s) |
| ✅ | Create chat | 200 (0.01s) |
| ✅ | RAG query (balanced) | 200 (25.45s) |
| ✅ | Verify citations (LLM) | 200 (21.60s) |
| ✅ | Export chat (HTML) | 200 (0.00s) |
| ✅ | Research agent | 200 (52.54s) |
| ✅ | Research too short | 400 (0.00s) |
| ✅ | Get chat messages | 200 (0.00s) |
| ✅ | Delete chat | 200 (0.01s) |
| ✅ | List documents | 200 (0.00s) |
| ✅ | Upload doc (JSON) | 200 (0.02s) |
| ✅ | Upload doc (empty) | 400 (0.00s) |
| ✅ | List templates | 200 (0.00s) |
| ✅ | List drafts | 200 (0.00s) |
| ✅ | Get nonexistent template | 404 (0.00s) |
| ✅ | Analyze risk (AI) | 200 (6.90s) |
| ✅ | Check completeness (AI) | 200 (16.34s) |
| ✅ | Legal basis | 200 (12.19s) |
| ✅ | Explain clause (AI) | 200 (10.59s) |
| ✅ | List providers | 200 (0.00s) |
| ✅ | Anonymize text | 200 (0.00s) |
| ✅ | Submit report | 200 (0.01s) |
| ✅ | List reports | 200 (0.00s) |
| ✅ | Create matter | 200 (0.01s) |
| ✅ | List matters | 200 (0.00s) |
| ✅ | Get matter | 200 (0.00s) |
| ✅ | Add note | 200 (0.01s) |
| ✅ | Delete note | 200 (0.01s) |
| ✅ | Update matter | 200 (0.01s) |
| ✅ | Delete matter | 200 (0.01s) |
| ✅ | Get nonexistent matter | 404 (0.00s) |
| ✅ | Homepage loads | 200 (0.00s) |
| ✅ | Has 8 pages | 200 (0.00s) |
| ✅ | Has vis.js CDN | 200 (0.00s) |
| ✅ | Has i18n system | 200 (0.00s) |
| ✅ | Has dark mode | 200 (0.00s) |
| ✅ | Favicon | 200 (0.00s) |
