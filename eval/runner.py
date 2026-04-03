#!/usr/bin/env python3
"""Evaluation runner for LexArdor benchmark.

Runs benchmark questions through the pipeline and measures:
- Citation precision (% of cited articles that are correct)
- Citation recall (% of expected articles that were cited)
- Hallucination rate (% of answers with unverified citations)
- Keyword coverage (% of expected keywords found in answers)
- Average latency

Usage:
    cd /home/kaizenlinux/Projects/Project_02_LEXARDOR/lexardor-v2
    python -m eval.runner [--limit N] [--mode balanced|strict|citizen] [--benchmark v1|v2]
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_benchmark(path: str = None, version: str = "v1") -> list[dict]:
    if not path:
        if version == "v2":
            path = str(Path(__file__).parent / "benchmark_v2.json")
        else:
            path = str(Path(__file__).parent / "benchmark_v1.json")

    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    # v2 format wraps questions in a dict with metadata
    if isinstance(raw, dict) and "questions" in raw:
        return raw["questions"]
    # v1 format is a plain list
    return raw


def extract_cited_articles(answer_text: str) -> list[str]:
    """Extract article numbers mentioned in an answer."""
    pattern = re.compile(r'(?:Član[auo]?m?|čl\.?)\s+(\d+[a-z]?)', re.IGNORECASE)
    return list(set(m.group(1) for m in pattern.finditer(answer_text)))


def run_eval(limit: int = None, answer_mode: str = "balanced", verbose: bool = False,
             benchmark_version: str = "v1", domain_filter: str = None):
    """Run the full benchmark evaluation."""
    from rag.pipeline import query as rag_query

    questions = load_benchmark(version=benchmark_version)

    # Optional domain filter
    if domain_filter:
        questions = [q for q in questions if q.get("domain") == domain_filter]

    if limit:
        questions = questions[:limit]

    results = []
    total_latency = 0

    print(f"Running {len(questions)} benchmark questions (mode={answer_mode}, benchmark={benchmark_version})...\n")

    for i, q in enumerate(questions):
        start = time.time()
        try:
            result = rag_query(q["question"], top_k=5, answer_mode=answer_mode)
            latency = time.time() - start
            total_latency += latency

            answer = result["answer"]
            sources = result["sources"]
            citations = result.get("citations", {})

            # Extract cited articles from answer
            cited = extract_cited_articles(answer)

            # Calculate metrics for this question
            expected = set(q.get("expected_articles", []))
            cited_set = set(cited)

            # Citation precision: of articles cited, how many are in expected?
            precision = len(cited_set & expected) / len(cited_set) if cited_set else 0
            # Citation recall: of expected articles, how many were cited?
            recall = len(cited_set & expected) / len(expected) if expected else 1.0
            # Hallucination: any flagged citations?
            flagged = citations.get("flagged_count", 0)
            has_hallucination = flagged > 0

            # Keyword coverage
            expected_kw = q.get("expected_keywords", [])
            answer_lower = answer.lower()
            kw_found = sum(1 for kw in expected_kw if kw.lower() in answer_lower)
            kw_coverage = kw_found / len(expected_kw) if expected_kw else 1.0

            r = {
                "id": q["id"],
                "domain": q.get("domain", ""),
                "difficulty": q.get("difficulty", ""),
                "answer_type": q.get("answer_type", ""),
                "latency_s": round(latency, 1),
                "cited_articles": cited,
                "expected_articles": list(expected),
                "citation_precision": round(precision, 2),
                "citation_recall": round(recall, 2),
                "has_hallucination": has_hallucination,
                "flagged_count": flagged,
                "verified_count": citations.get("verified_count", 0),
                "keyword_coverage": round(kw_coverage, 2),
                "confidence": result.get("confidence", ""),
            }
            results.append(r)

            status = "✅" if recall >= 0.5 and not has_hallucination else "⚠️" if recall > 0 else "❌"
            if verbose:
                print(f"  [{i+1}/{len(questions)}] {status} {q['id']}: P={precision:.0%} R={recall:.0%} KW={kw_coverage:.0%} ({latency:.1f}s)")
            else:
                print(f"  [{i+1}/{len(questions)}] {status} {q['id']} ({latency:.1f}s)")

        except Exception as e:
            latency = time.time() - start
            results.append({
                "id": q["id"],
                "error": str(e),
                "latency_s": round(latency, 1),
            })
            print(f"  [{i+1}/{len(questions)}] ❌ {q['id']}: ERROR: {e}")

    # Aggregate metrics
    valid = [r for r in results if "error" not in r]
    if not valid:
        print("\nNo valid results!")
        return {"error": "No valid results"}

    avg_precision = sum(r["citation_precision"] for r in valid) / len(valid)
    avg_recall = sum(r["citation_recall"] for r in valid) / len(valid)
    hallucination_rate = sum(1 for r in valid if r["has_hallucination"]) / len(valid)
    avg_kw_coverage = sum(r["keyword_coverage"] for r in valid) / len(valid)
    avg_latency = total_latency / len(valid) if valid else 0

    # By domain
    domains = set(r["domain"] for r in valid if r.get("domain"))
    by_domain = {}
    for d in sorted(domains):
        domain_results = [r for r in valid if r.get("domain") == d]
        by_domain[d] = {
            "count": len(domain_results),
            "avg_precision": round(sum(r["citation_precision"] for r in domain_results) / len(domain_results), 2),
            "avg_recall": round(sum(r["citation_recall"] for r in domain_results) / len(domain_results), 2),
            "avg_kw_coverage": round(sum(r["keyword_coverage"] for r in domain_results) / len(domain_results), 2),
            "hallucination_rate": round(sum(1 for r in domain_results if r["has_hallucination"]) / len(domain_results), 2),
        }

    # By difficulty
    difficulties = set(r["difficulty"] for r in valid if r.get("difficulty"))
    by_difficulty = {}
    for diff in sorted(difficulties):
        diff_results = [r for r in valid if r.get("difficulty") == diff]
        by_difficulty[diff] = {
            "count": len(diff_results),
            "avg_precision": round(sum(r["citation_precision"] for r in diff_results) / len(diff_results), 2),
            "avg_recall": round(sum(r["citation_recall"] for r in diff_results) / len(diff_results), 2),
            "avg_kw_coverage": round(sum(r["keyword_coverage"] for r in diff_results) / len(diff_results), 2),
        }

    # By answer type (v2 only)
    answer_types = set(r["answer_type"] for r in valid if r.get("answer_type"))
    by_answer_type = {}
    for at in sorted(answer_types):
        at_results = [r for r in valid if r.get("answer_type") == at]
        by_answer_type[at] = {
            "count": len(at_results),
            "avg_precision": round(sum(r["citation_precision"] for r in at_results) / len(at_results), 2),
            "avg_recall": round(sum(r["citation_recall"] for r in at_results) / len(at_results), 2),
            "avg_kw_coverage": round(sum(r["keyword_coverage"] for r in at_results) / len(at_results), 2),
        }

    summary = {
        "total_questions": len(questions),
        "valid_results": len(valid),
        "errors": len(results) - len(valid),
        "avg_citation_precision": round(avg_precision, 3),
        "avg_citation_recall": round(avg_recall, 3),
        "hallucination_rate": round(hallucination_rate, 3),
        "avg_keyword_coverage": round(avg_kw_coverage, 3),
        "avg_latency_s": round(avg_latency, 1),
        "by_domain": by_domain,
        "by_difficulty": by_difficulty,
        "by_answer_type": by_answer_type,
        "answer_mode": answer_mode,
        "benchmark_version": benchmark_version,
    }

    # Print summary
    print(f"\n{'='*60}")
    print(f"EVALUATION RESULTS (Benchmark {benchmark_version})")
    print(f"{'='*60}")
    print(f"Questions:          {summary['total_questions']} ({summary['valid_results']} valid, {summary['errors']} errors)")
    print(f"Citation Precision: {avg_precision:.1%}")
    print(f"Citation Recall:    {avg_recall:.1%}")
    print(f"Hallucination Rate: {hallucination_rate:.1%}")
    print(f"Keyword Coverage:   {avg_kw_coverage:.1%}")
    print(f"Avg Latency:        {avg_latency:.1f}s")

    print(f"\nBy Domain:")
    for d, stats in sorted(by_domain.items()):
        print(f"  {d}: {stats['count']} questions, P={stats['avg_precision']:.0%} R={stats['avg_recall']:.0%} KW={stats['avg_kw_coverage']:.0%} Hall={stats['hallucination_rate']:.0%}")

    print(f"\nBy Difficulty:")
    for diff, stats in sorted(by_difficulty.items()):
        print(f"  {diff}: {stats['count']} questions, P={stats['avg_precision']:.0%} R={stats['avg_recall']:.0%} KW={stats['avg_kw_coverage']:.0%}")

    if by_answer_type:
        print(f"\nBy Answer Type:")
        for at, stats in sorted(by_answer_type.items()):
            print(f"  {at}: {stats['count']} questions, P={stats['avg_precision']:.0%} R={stats['avg_recall']:.0%} KW={stats['avg_kw_coverage']:.0%}")

    # Save results
    output_dir = Path(__file__).parent / "reports"
    output_dir.mkdir(exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"eval_{benchmark_version}_{timestamp}.json"
    report_path.write_text(json.dumps({
        "summary": summary,
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport saved: {report_path}")

    return summary


if __name__ == "__main__":
    args = sys.argv[1:]
    limit = None
    mode = "balanced"
    benchmark = "v1"
    domain = None
    verbose = "--verbose" in args or "-v" in args

    if "--limit" in args:
        try:
            limit = int(args[args.index("--limit") + 1])
        except (IndexError, ValueError):
            limit = 5

    if "--mode" in args:
        try:
            mode = args[args.index("--mode") + 1]
        except (IndexError, ValueError):
            pass

    if "--benchmark" in args:
        try:
            benchmark = args[args.index("--benchmark") + 1]
        except (IndexError, ValueError):
            benchmark = "v2"

    if "--domain" in args:
        try:
            domain = args[args.index("--domain") + 1]
        except (IndexError, ValueError):
            pass

    run_eval(limit=limit, answer_mode=mode, verbose=verbose,
             benchmark_version=benchmark, domain_filter=domain)
