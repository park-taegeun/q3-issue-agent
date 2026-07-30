"""탐지기 4단계: 최종 산출물 작성.

- out/proposal.json: 채점용 구조화 출력
- out/report.md: 사람용 리포트 — 서술 문장은 템플릿이고, 등장하는 모든 수치는
  metrics/rules/severity가 계산한 값의 인용이다. (LLM이 수치를 만들지 않는다는 원칙)
"""
import json
import os

from src import contract

# 지표별 권장 '확인' 액션 — 해결책 제안이 아니라, 제안이 맞는지 검증하는 다음 한 걸음
ACTION_TEMPLATE = {
    contract.METRIC_NOSHOW: ("current 기간 {value} 세그먼트의 노쇼 매칭 {harm}건에서 표본 50건을 뽑아 "
                             "CS 기록·채팅 로그로 노쇼 사유(이동 부담/일정 착오/조건 불일치)를 분류한다"),
    contract.METRIC_CONVERSION: ("current 기간 {value} 세그먼트의 미성사 공고에서 표본 50건을 뽑아 "
                                 "미성사 사유(구인자 무응답/조건 불일치/공고 품질)를 분류한다"),
    contract.METRIC_MATCH_1H: ("current 기간 {value} 세그먼트의 매칭 지연 상위 50건에서 "
                               "지연 구간(지원 도착~구인자 확인~수락)별 소요를 분해한다"),
    contract.METRIC_REHIRE: ("current 기간 {value} 세그먼트에서 1회 매칭 후 이탈한 worker 표본 50명의 "
                             "직후 경험(노쇼 여부/정산 문제)을 추적한다"),
}

METRIC_KO = {
    contract.METRIC_CONVERSION: "지원→매칭 전환율",
    contract.METRIC_MATCH_1H: "1시간 내 매칭률",
    contract.METRIC_NOSHOW: "노쇼율",
    contract.METRIC_REHIRE: "재이용률",
}


def _pct(x):
    return f"{x * 100:.1f}%"


def _harm_count(c):
    """악화로 인한 초과 피해 건수 ≈ 악화폭 × 현재 분모. 근거 수치용 추정치(코드 산출)."""
    return round(c["seg_worsening"] * c["n_current"])


def build_proposal(winner, discarded, seed):
    return {
        "seed": seed,
        "proposed": None if winner is None else {
            "axis": winner["axis"], "value": winner["value"],
            "metric": winner["metric"], "direction": winner["direction"],
            "evidence": {k: winner[k] for k in
                         ("baseline", "current", "n_baseline", "n_current",
                          "overall_baseline", "overall_current",
                          "seg_worsening", "excess", "rel_worsening", "volume_share")},
            "score": winner["score"], "score_parts": winner["score_parts"],
            "recommended_action": ACTION_TEMPLATE[winner["metric"]].format(
                value=winner["value"], harm=_harm_count(winner)),
        },
        "discarded": [
            {"axis": d["axis"], "value": d["value"], "metric": d["metric"],
             "baseline": d["baseline"], "current": d["current"],
             "score": d["score"], "reason": d["discard_reason"]}
            for d in discarded
        ],
    }


def write_report(proposal, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "proposal.json"), "w", encoding="utf-8") as f:
        json.dump(proposal, f, ensure_ascii=False, indent=2)

    lines = ["# 탐지 리포트", ""]
    p = proposal["proposed"]
    if p is None:
        lines += ["**제안할 문제가 없다.** 모든 세그먼트가 임계 조건을 넘지 않았다.", ""]
    else:
        e = p["evidence"]
        lines += [
            f"## 제안 문제 (1개)",
            "",
            # 지표명이 모두 'ㄹ' 받침(율/률)이라 조사는 '이'로 고정해도 된다
            f"**{p['value']} 세그먼트({p['axis']} 축)의 {METRIC_KO[p['metric']]}이 "
            f"{_pct(e['baseline'])} → {_pct(e['current'])}로 악화했다.**",
            "",
            "### 근거 수치 (전부 결정론적 코드 산출값)",
            "",
            "| | baseline | current |",
            "|---|---|---|",
            f"| 세그먼트 {METRIC_KO[p['metric']]} | {_pct(e['baseline'])} (n={e['n_baseline']}) "
            f"| {_pct(e['current'])} (n={e['n_current']}) |",
            f"| 전체 {METRIC_KO[p['metric']]} | {_pct(e['overall_baseline'])} "
            f"| {_pct(e['overall_current'])} |",
            "",
            f"- 전체 지표 변화로는 설명되지 않는 초과 악화(excess): **{_pct(e['excess'])}p**",
            f"- 상대 악화: {e['rel_worsening'] * 100:.0f}% / 영향 규모: 해당 지표 모수의 "
            f"{_pct(e['volume_share'])}",
            f"- 심각도 점수: {p['score']} = 상대악화 {p['score_parts']['rel_worsening']} × "
            f"볼륨비중 {p['score_parts']['volume_share']} × 지표가중치 {p['score_parts']['metric_weight']}",
            "",
            "### 권장 확인 액션 (1개)",
            "",
            f"- {p['recommended_action']}",
            "",
        ]
    lines += ["## 폐기한 후보와 사유", ""]
    if not proposal["discarded"]:
        lines.append("- 없음")
    for d in proposal["discarded"]:
        lines.append(f"- **{d['value']}({d['axis']}) {METRIC_KO[d['metric']]} "
                     f"{_pct(d['baseline'])}→{_pct(d['current'])}** (점수 {d['score']}): {d['reason']}")
    lines.append("")
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
