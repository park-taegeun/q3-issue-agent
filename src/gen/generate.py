"""합성 매칭 퍼널 데이터 생성기 → data/funnel.db

설계 원칙:
- 원시 이벤트(공고/지원/매칭)만 저장한다. 지표는 전부 탐지기가 유도한다.
- 베이스 데이터는 기간(전반/후반)에 대해 '대칭'이다. 즉 함정 mods 없이 돌리면
  두 기간의 지표 차이는 순수 표본 노이즈뿐이다.
  → 기간 간의 모든 '변화'(표면 개선 + 곪은 세그먼트)는 traps.py의 mods가 만든다.
  이렇게 해야 "함정 없음 → 후보 없음"을 오탐 측정의 기준선으로 쓸 수 있다.
- 함정 주입은 Mods 훅(밑의 BaseMods 시그니처)을 통해서만 일어난다.
  generate.py 자체는 함정을 모른다.
"""
import math
import random
import sqlite3
from datetime import datetime, timedelta

from src import contract

# ── 베이스 파라미터 (현실적 편차: 번화가 볼륨 큼, 주말 물량 적음 등) ──
REGION_WEIGHT = {"서현동": 0.14, "정자동": 0.13, "야탑동": 0.12, "이매동": 0.09,
                 "수내동": 0.10, "금곡동": 0.08, "구미동": 0.09, "판교동": 0.13,
                 "백현동": 0.06, "운중동": 0.06}
JOB_WEIGHT = {"서빙": 0.22, "주방": 0.18, "물류": 0.17, "청소": 0.14,
              "매장관리": 0.12, "배달": 0.17}

DAILY_POSTINGS_MEAN = 140          # 하루 평균 공고 수 (8주 ≈ 7,800건)
WEEKEND_POSTING_FACTOR = 0.80      # 주말엔 공고가 덜 올라온다
APPS_MEAN_BY_JOB = {"서빙": 5.5, "주방": 4.5, "물류": 6.0, "청소": 4.0,
                    "매장관리": 3.5, "배달": 6.5}
WEEKEND_APPS_FACTOR = 0.85         # 주말 지원도 소폭 적다
FILL_PROB_BY_JOB = {"서빙": 0.65, "주방": 0.60, "물류": 0.62, "청소": 0.55,
                    "매장관리": 0.50, "배달": 0.70}
MATCH_DELAY_MEAN_MIN = 70.0        # 지원→매칭 지연(분), 지수분포 평균. 60분 내 비율 ≈ 57%
NOSHOW_BASE = 0.08                 # 노쇼 기본율
NOSHOW_ADD_BY_BAND = {"0-1km": 0.0, "1-3km": 0.01, "3-5km": 0.03, "5km+": 0.04}
BAND_WEIGHTS = {"0-1km": 0.45, "1-3km": 0.35, "3-5km": 0.13, "5km+": 0.07}
BAND_KM_RANGE = {"0-1km": (0.1, 1.0), "1-3km": (1.0, 3.0),
                 "3-5km": (3.0, 5.0), "5km+": (5.0, 9.0)}
WORKER_POOL_TOTAL = 2500           # 지역별 구직자 풀 (재이용률의 원천)


class BaseMods:
    """함정 주입 훅. 베이스는 전부 항등 — traps.py가 이를 상속해 기간/세그먼트별로 비튼다."""

    def band_weights(self, period, weights):
        return weights

    def apps_mean(self, period, region, job, day_type, mean):
        return mean

    def fill_prob(self, period, region, job, day_type, p):
        return p

    def delay_mean(self, period, region, job, day_type, mean):
        return mean

    def noshow_prob(self, period, region, job, day_type, band, p):
        return p


def _poisson(rng, lam):
    """표준 라이브러리엔 포아송이 없어서 Knuth 방식으로 직접 뽑는다."""
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _weighted_choice(rng, weight_map):
    return rng.choices(list(weight_map.keys()), weights=list(weight_map.values()))[0]


def generate(db_path, seed, mods=None):
    """합성 데이터를 생성해 db_path에 저장한다. mods가 함정을 주입한다."""
    rng = random.Random(seed)
    mods = mods or BaseMods()

    start = datetime.fromisoformat(contract.PERIOD_START)
    baseline_end = datetime.fromisoformat(contract.BASELINE_END)
    end = datetime.fromisoformat(contract.PERIOD_END)
    n_days = (end - start).days + 1

    # 지역별 구직자 풀. 기간 간 동일 풀이어야 재이용률이 자연스럽게 안정된다.
    workers = {r: [f"{r}-w{i}" for i in range(max(60, int(WORKER_POOL_TOTAL * w)))]
               for r, w in REGION_WEIGHT.items()}

    conn = sqlite3.connect(db_path)
    conn.executescript("""
        DROP TABLE IF EXISTS postings;
        DROP TABLE IF EXISTS applications;
        DROP TABLE IF EXISTS matches;
        CREATE TABLE postings(id INTEGER PRIMARY KEY, region TEXT, job_category TEXT, posted_at TEXT);
        CREATE TABLE applications(id INTEGER PRIMARY KEY, posting_id INTEGER, worker_id TEXT,
                                  applied_at TEXT, distance_km REAL);
        CREATE TABLE matches(id INTEGER PRIMARY KEY, application_id INTEGER, matched_at TEXT, no_show INTEGER);
    """)

    posting_id = app_id = match_id = 0
    for d in range(n_days):
        day = start + timedelta(days=d)
        period = "baseline" if day.date() <= baseline_end.date() else "current"
        is_weekend = day.weekday() >= 5
        day_type = "주말" if is_weekend else "평일"
        day_factor = WEEKEND_POSTING_FACTOR if is_weekend else 1.0

        for region in contract.REGIONS:
            for job in contract.JOBS:
                lam = DAILY_POSTINGS_MEAN * REGION_WEIGHT[region] * JOB_WEIGHT[job] * day_factor
                for _ in range(_poisson(rng, lam)):
                    posting_id += 1
                    # 공고는 아침~저녁 사이, 오전에 몰린다
                    hour = rng.choices(range(7, 22), weights=[2, 4, 6, 6, 5, 4, 3, 3, 3, 3, 3, 2, 2, 1, 1])[0]
                    posted_at = day.replace(hour=hour, minute=rng.randrange(60))
                    conn.execute("INSERT INTO postings VALUES (?,?,?,?)",
                                 (posting_id, region, job, posted_at.isoformat(sep=" ")))

                    # ── 지원 ──
                    apps_lam = mods.apps_mean(period, region, job, day_type,
                                              APPS_MEAN_BY_JOB[job] * (WEEKEND_APPS_FACTOR if is_weekend else 1.0))
                    band_w = mods.band_weights(period, dict(BAND_WEIGHTS))
                    posting_apps = []
                    for _ in range(_poisson(rng, apps_lam)):
                        app_id += 1
                        band = _weighted_choice(rng, band_w)
                        km = round(rng.uniform(*BAND_KM_RANGE[band]), 2)
                        # 지원은 공고 게시 후 수 시간 안에 몰린다 (지수분포, 평균 3시간)
                        applied_at = posted_at + timedelta(minutes=rng.expovariate(1 / 180.0))
                        worker = rng.choice(workers[region])
                        conn.execute("INSERT INTO applications VALUES (?,?,?,?,?)",
                                     (app_id, posting_id, worker, applied_at.isoformat(sep=" "), km))
                        posting_apps.append((app_id, applied_at, band))

                    # ── 매칭: 공고당 최대 1건 채용 ──
                    fill_p = mods.fill_prob(period, region, job, day_type, FILL_PROB_BY_JOB[job])
                    if posting_apps and rng.random() < fill_p:
                        match_id += 1
                        chosen_id, chosen_at, chosen_band = rng.choice(posting_apps)
                        delay_mean = mods.delay_mean(period, region, job, day_type, MATCH_DELAY_MEAN_MIN)
                        matched_at = chosen_at + timedelta(minutes=rng.expovariate(1 / delay_mean))
                        noshow_p = mods.noshow_prob(period, region, job, day_type, chosen_band,
                                                    NOSHOW_BASE + NOSHOW_ADD_BY_BAND[chosen_band])
                        no_show = 1 if rng.random() < noshow_p else 0
                        conn.execute("INSERT INTO matches VALUES (?,?,?,?)",
                                     (match_id, chosen_id, matched_at.isoformat(sep=" "), no_show))

    conn.commit()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("postings", "applications", "matches")}
    conn.close()
    return counts


if __name__ == "__main__":
    # 단독 실행은 베이스 데이터(함정 없음) 생성용 — 오탐 기준선 확인에 쓴다
    import argparse, os
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/funnel.db")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.db), exist_ok=True)
    print(generate(args.db, args.seed))
