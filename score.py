"""독립 채점기: 탐지기의 제안을 정답(ground_truth.json)과 대조한다.

이 파일만 answers/를 읽는다. 탐지기(src/agent/)와는 반대편 당사자이므로,
채점 전에 탐지기 소스가 정답 쪽을 참조하지 않는지 정적 검사한다 —
채점자가 정답을 알면 유리하게 맞추게 되므로, 격리를 약속이 아니라 코드로 강제한다.

표준 라이브러리만 사용하며 src/ 모듈을 일절 임포트하지 않는다(채점기 자신의 독립성).
"""
import argparse
import glob
import json
import os
import sys

# 탐지기 소스에 나타나면 안 되는 토큰: 정답 파일·생성기 참조의 흔적
FORBIDDEN_TOKENS = ("ground_truth", "answers", "traps", "src.gen", "src/gen")


def check_isolation(agent_dir):
    violations = []
    for path in sorted(glob.glob(os.path.join(agent_dir, "*.py"))):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for token in FORBIDDEN_TOKENS:
            if token in text:
                violations.append(f"{path}: '{token}'")
    return violations


def grade(proposal, truth):
    trap_by_key = {(t["axis"], t["value"], t["metric"]): t for t in truth["traps"]}

    result = {"seed": proposal.get("seed"),
              "verdict": None, "hit_trap": None, "primary_hit": False,
              "false_positive": None, "candidate_coverage": [], "missed_traps": []}

    p = proposal.get("proposed")
    if p is None:
        result["verdict"] = "미탐"  # 함정을 심었는데 아무것도 제안하지 못했다
    else:
        key = (p["axis"], p["value"], p["metric"])
        if key in trap_by_key:
            t = trap_by_key[key]
            result["verdict"] = "정탐"
            result["hit_trap"] = t["id"]
            result["primary_hit"] = (t["id"] == truth["primary"])
        else:
            result["verdict"] = "오탐"  # 심지 않은 헛문제를 올렸다
            result["false_positive"] = {"axis": p["axis"], "value": p["value"], "metric": p["metric"]}

    # 참고 지표: 폐기 후보까지 포함하면 심은 함정을 몇 개나 '인지'는 했는가
    seen_keys = set()
    if p is not None:
        seen_keys.add((p["axis"], p["value"], p["metric"]))
    for d in proposal.get("discarded", []):
        seen_keys.add((d["axis"], d["value"], d["metric"]))
    result["candidate_coverage"] = sorted(t["id"] for k, t in trap_by_key.items() if k in seen_keys)
    result["missed_traps"] = sorted(t["id"] for k, t in trap_by_key.items() if k not in seen_keys)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposal", default="out/proposal.json")
    ap.add_argument("--truth", default="answers/ground_truth.json")
    ap.add_argument("--agent-dir", default="src/agent")
    ap.add_argument("--out", default="out/score.json")
    args = ap.parse_args()

    violations = check_isolation(args.agent_dir)
    if violations:
        print("[격리 위반] 탐지기 소스가 정답 쪽을 참조한다 — 채점을 거부한다:")
        for v in violations:
            print(f"  - {v}")
        sys.exit(2)

    with open(args.proposal, encoding="utf-8") as f:
        proposal = json.load(f)
    with open(args.truth, encoding="utf-8") as f:
        truth = json.load(f)

    result = grade(proposal, truth)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    tag = {"정탐": "O", "오탐": "X", "미탐": "-"}[result["verdict"]]
    primary = " (primary 적중)" if result["primary_hit"] else ""
    print(f"[채점] {result['verdict']}{primary} [{tag}]"
          + (f" — 적중 함정: {result['hit_trap']}" if result["hit_trap"] else "")
          + (f" — 헛문제: {result['false_positive']}" if result["false_positive"] else ""))
    print(f"[채점] 후보 단계 커버리지: {result['candidate_coverage']}"
          + (f" / 인지 못한 함정: {result['missed_traps']}" if result["missed_traps"] else " (전 함정 인지)"))


if __name__ == "__main__":
    main()
