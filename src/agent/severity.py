"""탐지기 3단계: 후보 심각도 점수화 → dedup → 단 하나로 좁힌다. 전부 결정론적 코드.

점수 = 상대 악화 × 볼륨 비중 × 지표 가중치
- 상대 악화: 얼마나 곪았나
- 볼륨 비중: 얼마나 큰 부위인가 (같은 지표 분모 대비 비중이라 단위 일관)
- 지표 가중치: 어떤 종류의 훼손인가 — 이 가중치는 '판단'이므로 proposal에 그대로 노출해
  리뷰 가능하게 한다 (LLM/사람이 수치를 임의 조정하지 못하게 코드 상수로 고정)
"""
from src import contract

# 노쇼: 이미 성사된 약속의 파기 — 양측 신뢰를 직접 훼손, 회복이 가장 어렵다
# 재이용: 장기 리텐션 지표 — 훼손이 조용히 누적된다
# 전환: 퍼널 핵심이지만 원인이 다양(수급 불균형 포함)해 신뢰 훼손보다 한 단계 낮게
# 1시간 내 매칭: 속도 품질 — 중요하지만 위 셋보다 후행
METRIC_WEIGHT = {
    contract.METRIC_NOSHOW: 1.5,
    contract.METRIC_REHIRE: 1.3,
    contract.METRIC_CONVERSION: 1.2,
    contract.METRIC_MATCH_1H: 1.0,
}

# 퍼널 상류 → 하류. 같은 세그먼트에서 동시 악화하면 상류를 원인 후보로 대표시킨다.
# (예: 매칭 급감 세그먼트의 재이용률 하락은 매칭 총량 감소의 기계적 파생 — FAILURES.md 참고)
FUNNEL_ORDER = [contract.METRIC_CONVERSION, contract.METRIC_MATCH_1H,
                contract.METRIC_NOSHOW, contract.METRIC_REHIRE]


def _score(c):
    return c["rel_worsening"] * c["volume_share"] * METRIC_WEIGHT[c["metric"]]


def select_one(candidates):
    """후보들 → (선택 1개, 폐기 목록[사유 포함]). 정렬 키를 고정해 결정론을 보장한다."""
    for c in candidates:
        c["score"] = round(_score(c), 4)
        c["score_parts"] = {"rel_worsening": round(c["rel_worsening"], 3),
                            "volume_share": round(c["volume_share"], 3),
                            "metric_weight": METRIC_WEIGHT[c["metric"]]}
    discarded = []
    alive = sorted(candidates, key=lambda c: (-c["score"], c["axis"], c["value"], c["metric"]))

    # dedup 1: 같은 세그먼트에서 다중 지표 악화 → 퍼널 최상류 지표만 대표로 남긴다
    kept, seen_segment = [], {}
    for c in alive:
        key = (c["axis"], c["value"])
        if key not in seen_segment:
            seen_segment[key] = [c]
        else:
            seen_segment[key].append(c)
    for key, group in seen_segment.items():
        group.sort(key=lambda c: FUNNEL_ORDER.index(c["metric"]))
        kept.append(group[0])
        for c in group[1:]:
            c["discard_reason"] = (f"동일 세그먼트의 상류 지표({group[0]['metric']}) 악화에 따른 "
                                   f"기계적 파생 가능성 — 대표 후보에 흡수")
            discarded.append(c)

    # dedup 2: 2차원 축 후보와 그 성분 축 후보가 같은 지표면 점수 높은 쪽만 남긴다
    # (주방+주말이 곪으면 '주방' 단독, '주말' 단독 축에도 희석된 악화가 새어 보인다)
    final = []
    for c in sorted(kept, key=lambda c: (-c["score"], c["axis"], c["value"], c["metric"])):
        dup = None
        for f in final:
            pair = {c["axis"], f["axis"]}
            if c["metric"] == f["metric"] and contract.AXIS_JOB_DAYTYPE in pair:
                two_d, one_d = (c, f) if c["axis"] == contract.AXIS_JOB_DAYTYPE else (f, c)
                if one_d["axis"] in (contract.AXIS_JOB, contract.AXIS_WEEKDAY) \
                        and one_d["value"] in two_d["value"].split("+"):
                    dup = f
        if dup is None:
            final.append(c)
        else:
            c["discard_reason"] = (f"상위 점수 후보({dup['axis']}={dup['value']})와 동일 현상의 "
                                   f"중복 관측(축만 다름) — 희석/집중 관계")
            discarded.append(c)

    if not final:
        return None, discarded
    winner = final[0]
    for c in final[1:]:
        c["discard_reason"] = (f"심각도 점수 열세 ({c['score']} vs 1위 {winner['score']}) — "
                               f"상대악화 {c['score_parts']['rel_worsening']}, "
                               f"볼륨비중 {c['score_parts']['volume_share']}, "
                               f"가중치 {c['score_parts']['metric_weight']}")
        discarded.append(c)
    return winner, discarded
