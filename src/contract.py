"""이름 계약(contract): 생성기·탐지기·채점기가 공유하는 '어휘'.

여기에는 축/지표의 이름과 거리 구간 경계만 둔다.
함정의 위치·강도 같은 '정보'는 절대 넣지 않는다 — 그건 src/gen/traps.py에만 있다.
(왜 공유하나: 채점이 (축, 값, 지표) 문자열 대조라서, 어휘가 어긋나면
 정탐도 오탐으로 채점된다. DECISIONS.md 2026-07-30 항목 참고.)
"""

# ── 기간 ──────────────────────────────────────────────
# 8주를 절반으로 나눠 "이전(baseline) vs 최근(current)"을 비교한다.
PERIOD_START = "2026-06-01"          # 월요일
BASELINE_END = "2026-06-28"          # baseline: 06-01 ~ 06-28 (4주)
PERIOD_END = "2026-07-26"            # current : 06-29 ~ 07-26 (4주)

# ── 세그먼트 축 ────────────────────────────────────────
AXIS_REGION = "region"               # 동네
AXIS_JOB = "job_category"            # 직종
AXIS_WEEKDAY = "day_type"            # 평일/주말
AXIS_DISTANCE = "distance_band"      # 지원자-공고 거리 구간
AXIS_JOB_DAYTYPE = "job_category+day_type"  # 2차원 조합축

REGIONS = ["서현동", "정자동", "야탑동", "이매동", "수내동",
           "금곡동", "구미동", "판교동", "백현동", "운중동"]
JOBS = ["서빙", "주방", "물류", "청소", "매장관리", "배달"]
DAY_TYPES = ["평일", "주말"]

# 거리 구간: [하한, 상한) km. 상한 None = 무제한.
DISTANCE_BANDS = [
    ("0-1km", 0.0, 1.0),
    ("1-3km", 1.0, 3.0),
    ("3-5km", 3.0, 5.0),
    ("5km+", 5.0, None),
]


def distance_band(km):
    """거리(km) → 구간 라벨. 생성기는 원시 km만 저장하고 비닝은 여기서 통일한다."""
    for label, lo, hi in DISTANCE_BANDS:
        if km >= lo and (hi is None or km < hi):
            return label
    raise ValueError(f"음수 거리: {km}")


# ── 지표 ──────────────────────────────────────────────
# 스캔 대상은 '비율' 지표만. 건수(공고/지원/매칭)는 근거 수치로만 쓴다.
# (건수는 세그먼트 규모에 따라 자연 변동이 커서 비율로 정규화해야 비교가 성립한다.)
METRIC_CONVERSION = "conversion_rate"        # 지원 → 매칭 전환율
METRIC_MATCH_1H = "match_within_1h_rate"     # 매칭 중 지원→매칭 60분 이내 비율
METRIC_NOSHOW = "noshow_rate"                # 매칭 중 노쇼 비율
METRIC_REHIRE = "rehire_rate"                # 매칭된 worker 중 2회 이상 매칭 비율

RATE_METRICS = [METRIC_CONVERSION, METRIC_MATCH_1H, METRIC_NOSHOW, METRIC_REHIRE]

# 지표별 '악화 방향': +1 = 오르면 나쁨, -1 = 내리면 나쁨
BAD_DIRECTION = {
    METRIC_CONVERSION: -1,
    METRIC_MATCH_1H: -1,
    METRIC_NOSHOW: +1,
    METRIC_REHIRE: -1,
}
