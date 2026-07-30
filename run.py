"""원커맨드 실행기: 데이터 생성 → 탐지 → 채점.

  python3 run.py                  # 시드 42로 한 번 실행
  python3 run.py --seed 7         # 다른 시드로 실행
  python3 run.py --eval-seeds 5   # 시드 42~46 반복 채점 → 정탐률/오탐률 집계

채점(score.py)은 별도 프로세스로 호출한다 — 탐지와 채점의 독립성을 실행 구조로도 유지.
"""
import argparse
import json
import os
import subprocess
import sys

from src.gen.generate import generate
from src.gen.traps import TrapMods, write_ground_truth
from src.agent.metrics import compute_metrics, load_data
from src.agent.rules import scan
from src.agent.severity import select_one
from src.agent.report import build_proposal, write_report

ROOT = os.path.dirname(os.path.abspath(__file__))


def run_once(seed, db_path, truth_path, out_dir, quiet=False):
    generate(db_path, seed, TrapMods())
    write_ground_truth(truth_path, seed)

    postings, events = load_data(db_path)
    metrics = compute_metrics(postings, events)
    winner, discarded = select_one(scan(metrics), events)
    proposal = build_proposal(winner, discarded, seed)
    write_report(proposal, out_dir)

    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "score.py"),
         "--proposal", os.path.join(out_dir, "proposal.json"),
         "--truth", truth_path,
         "--agent-dir", os.path.join(ROOT, "src", "agent"),
         "--out", os.path.join(out_dir, "score.json")],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout + r.stderr)
        sys.exit(r.returncode)
    if not quiet:
        print(r.stdout, end="")
    with open(os.path.join(out_dir, "score.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--eval-seeds", type=int, default=0,
                    help="N개 시드(--seed부터 연속)로 반복 채점해 정탐률/오탐률을 집계")
    args = ap.parse_args()

    if not args.eval_seeds:
        os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
        score = run_once(args.seed,
                         os.path.join(ROOT, "data", "funnel.db"),
                         os.path.join(ROOT, "answers", "ground_truth.json"),
                         os.path.join(ROOT, "out"))
        print(f"\n리포트: out/report.md / 제안: out/proposal.json / 채점: out/score.json")
        return

    # ── multi-seed 평가: "유의미하게 잡고, 오탐이 통제되는가"를 수치로 확인 ──
    results = []
    for seed in range(args.seed, args.seed + args.eval_seeds):
        eval_dir = os.path.join(ROOT, "out", "eval", f"seed{seed}")
        os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
        score = run_once(seed,
                         os.path.join(ROOT, "data", f"eval_seed{seed}.db"),
                         os.path.join(ROOT, "answers", f"ground_truth_seed{seed}.json"),
                         eval_dir, quiet=True)
        results.append(score)
        cov = len(score["candidate_coverage"])
        print(f"seed {seed}: {score['verdict']}"
              f"{' (primary)' if score['primary_hit'] else ''}"
              f" / 후보 커버리지 {cov}/3"
              + (f" / 헛문제 {score['false_positive']}" if score["false_positive"] else ""))

    n = len(results)
    tp = sum(1 for r in results if r["verdict"] == "정탐")
    fp = sum(1 for r in results if r["verdict"] == "오탐")
    miss = sum(1 for r in results if r["verdict"] == "미탐")
    primary = sum(1 for r in results if r["primary_hit"])
    full_cov = sum(1 for r in results if len(r["candidate_coverage"]) == 3)
    print(f"\n[집계] {n}개 시드 — 정탐 {tp} (primary 적중 {primary}) / 오탐 {fp} / 미탐 {miss}"
          f" / 후보 단계 전 함정 인지 {full_cov}/{n}")


if __name__ == "__main__":
    main()
