"""원본 데이터를 정리해 app.py 가 읽을 작은 표 3개를 만든다. (화면 코드 없음)

실행:  python data_prep.py            (출입구는 data/exits.csv 에 이미 있는 역은 카카오를 다시 부르지 않음)
       python data_prep.py --refresh  (모든 역 출입구 좌표를 카카오에서 새로 받기)

만드는 파일
- data/last_train.csv : 출발역 → 같은 노선 목적지역 막차 (요일별, 갈아타지 않는 열차)
- data/last_by_dir.csv: 역 · 노선 · 방향별 마지막 열차 (다음 역 = "○○ 방면", 행선지)
- data/exits.csv      : 역 출입구 좌표 (카카오)
- data/cells.csv      : 상권을 100m 칸으로 나눈 격자 × 역별 가장 가까운 출구까지 맨해튼 거리
"""
import math
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from pyproj import Transformer

API = {
    "키워드검색": "https://dapi.kakao.com/v2/local/search/keyword.json",  # 🔌 카카오 키워드로 장소 검색
}

폴더 = Path(__file__).parent / "data"
시각표_원본 = 폴더 / "timetable_raw.csv"
상권_원본 = 폴더 / "area_raw.csv"

# 지역 = 역 몇 개(역: [호선들]) + 상권 몇 개. 칸마다 지역 안의 역들까지 거리를 잰다.
# 자르기 (A, B): A역 → B역 방향으로 B역보다 더 간 칸은 뺀다 (신논현역 상권 중 논현역 쪽 절반 빼기)
# 상권_역: 그 상권 칸은 적힌 역만 후보로 (적지 않은 상권은 지역의 모든 역)
지역들 = {
    "강남·역삼·신논현": {
        "역": {"강남": [2], "역삼": [2], "신논현": [9]},
        "상권": ["강남역", "역삼역", "신논현역", "국기원"],
        "자르기": ("강남", "신논현"),
    },
    # 석촌역(송리단길용)·잠실역(방이동먹자골목용)은 역 후보로만 넣음 (그 역 주변 상권 칸은 만들지 않음)
    "송파나루·석촌·잠실": {
        "역": {"송파나루": [9], "석촌": [8, 9], "잠실": [2, 8]},
        "상권": ["송파나루역 1번(송리단길)", "방이동먹자골목"],
        "상권_역": {"송파나루역 1번(송리단길)": ["송파나루", "석촌"]},
    },
}
상권_별명 = {"송파나루역 1번(송리단길)": "송리단길"}  # 화면에 보일 짧은 이름

셀_m = 100              # 칸 한 변 (한 블록 정도)
최대_출구번호 = 15      # 역마다 1~15번 출구까지 찾아봄
최대_호출 = 30          # 카카오 호출 상한 (남의 서버 예절)
최대_소요_분 = 100      # 2호선은 순환선이라 같은 열차코드가 한 바퀴 뒤 다시 나옴 → 그보다 짧은 것만 같은 운행으로 봄

# 상권 좌표는 EPSG:5181(중부원점 TM, 미터). 지도는 EPSG:4326(위도·경도)
TM_to_위경도 = Transformer.from_crs("EPSG:5181", "EPSG:4326", always_xy=True)
위경도_to_TM = Transformer.from_crs("EPSG:4326", "EPSG:5181", always_xy=True)

load_dotenv(override=True)


def 모든_역():
    return {역: 호선들 for 지역 in 지역들.values() for 역, 호선들 in 지역["역"].items()}


def 읽기(경로):
    """utf-8-sig → cp949 차례로 읽고, 어떤 인코딩으로 읽었는지 알려 준다."""
    for 인코딩 in ("utf-8-sig", "cp949"):
        try:
            df = pd.read_csv(경로, encoding=인코딩, low_memory=False)
            print(f"  {경로.name}: {len(df):,}줄 × {df.shape[1]}칸 ({인코딩})")
            return df
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"{경로.name} 을(를) 어떤 인코딩으로도 읽지 못했어요")


def 시각_분(값):
    """'24:21:30' → 1461.5 (자정 기준 분). 24시가 넘는 표기를 그대로 숫자로."""
    if pd.isna(값):
        return None
    시, 분, 초 = (int(x) for x in str(값).split(":"))
    return 시 * 60 + 분 + 초 / 60


def 분_표시(분):
    """1461.5 → '00:21' (초는 버림 = 실제보다 이르게 보여서 안전)."""
    분 = int(분)
    return f"{(분 // 60) % 24:02d}:{분 % 60:02d}"


# ── 1) 막차 ────────────────────────────────────────────────
def 막차_만들기():
    원본 = 읽기(시각표_원본)
    원본["출발_분"] = 원본["열차출발시간"].map(시각_분)
    # 출발 칸이 빈 줄 = 그 역이 종착 → 도착 시각을 씀
    원본["도착_분"] = 원본["열차도착시간"].map(시각_분).fillna(원본["출발_분"])

    결과, 방향별 = [], []
    for 출발역, 호선 in ((역, h) for 역, 호선들 in 모든_역().items() for h in 호선들):
        t = 원본[원본["호선"] == 호선]
        출발 = t[(t["역사명"] == 출발역) & t["출발_분"].notna()]
        출발 = 출발[["주중주말", "방향", "열차코드", "출발_분", "도착역"]]
        도착 = t[t["역사명"] != 출발역][["주중주말", "열차코드", "역사명", "도착_분"]]

        짝 = 출발.reset_index().merge(도착, on=["주중주말", "열차코드"])
        짝["소요_분"] = 짝["도착_분"] - 짝["출발_분"]
        짝 = 짝[(짝["소요_분"] > 0) & (짝["소요_분"] <= 최대_소요_분)]
        # 같은 출발 줄에서 같은 목적지가 두 번 잡히면 가장 먼저 닿는 것만
        짝 = 짝.sort_values("소요_분").drop_duplicates(["index", "역사명"])

        # 방향별 마지막 열차: 열차마다 바로 다음에 서는 역 = "○○ 방면"
        열차 = 짝.drop_duplicates("index").rename(columns={"역사명": "다음역"})  # 짝은 소요_분 순이라 첫 줄 = 다음 역
        마지막 = 열차.sort_values("출발_분").groupby(["주중주말", "방향"]).tail(1)
        방향별.append(마지막.assign(출발역=출발역, 호선=호선).rename(columns={"도착역": "행선지"}))

        막차 = 짝.sort_values("출발_분").groupby(["주중주말", "역사명"]).tail(1)
        막차 = 막차.assign(출발역=출발역, 호선=호선).rename(columns={"역사명": "목적지역", "도착역": "행선지"})
        결과.append(막차)

    막차 = pd.concat(결과, ignore_index=True)
    # 노선이 여러 개인 역(석촌 8·9호선)은 같은 목적지면 더 늦은 막차 하나만
    막차 = 막차.sort_values("출발_분").groupby(["출발역", "주중주말", "목적지역"]).tail(1)
    막차["막차_시각"] = 막차["출발_분"].map(분_표시)
    막차["목적지_도착"] = 막차["도착_분"].map(분_표시)
    막차["소요_분"] = 막차["소요_분"].round().astype(int)
    막차 = 막차[["출발역", "호선", "목적지역", "주중주말", "방향", "행선지",
                 "출발_분", "막차_시각", "목적지_도착", "소요_분"]]

    방향별 = pd.concat(방향별, ignore_index=True)
    방향별["막차_시각"] = 방향별["출발_분"].map(분_표시)
    방향별 = 방향별[["출발역", "호선", "주중주말", "방향", "다음역", "행선지", "출발_분", "막차_시각"]]
    return (막차.sort_values(["출발역", "주중주말", "목적지역"]).reset_index(drop=True),
            방향별.sort_values(["출발역", "주중주말", "출발_분"]).reset_index(drop=True))


# ── 2) 출입구 (카카오) ─────────────────────────────────────
def 카카오_검색(검색어, 키):
    try:
        res = requests.get(  # 🔌 API 호출
            API["키워드검색"],
            headers={"Authorization": f"KakaoAK {키}"},
            params={"query": 검색어, "size": 15},
            timeout=10,
        )
    except requests.RequestException:
        raise RuntimeError("⛔ 카카오 서버에 연결하지 못했어요 (인터넷 연결을 확인해 주세요)") from None
    if res.status_code == 401:
        raise RuntimeError("⛔ 카카오 REST API 키가 틀렸어요 (JavaScript 키를 넣지 않았는지 확인)")
    if res.status_code == 403:
        raise RuntimeError("⛔ 카카오맵 사용 설정을 켜 주세요 (카카오 개발자 콘솔)")
    if res.status_code != 200:
        raise RuntimeError(f"카카오 장소 검색이 실패했어요 (상태 코드 {res.status_code})")
    return res.json().get("documents", [])


def 출구_찾기(문서들, 역, 호선들, 번호):
    """'강남역 2호선 3번출구' 또는 '강남역 신분당선 3번출구' 처럼 노선 이름이 달라도 같은 출구로 본다.
    (같은 출구가 다른 노선 이름으로만 등록된 경우가 있음) 그 역 노선 이름이 있으면 그걸 먼저."""
    후보 = []
    for 장소 in 문서들:
        조각 = 장소.get("place_name", "").split()
        if len(조각) == 3 and 조각[0] == f"{역}역" and 조각[2] == f"{번호}번출구":
            위도, 경도 = float(장소["y"]), float(장소["x"])  # y = 위도, x = 경도
            if 33 <= 위도 <= 39 and 124 <= 경도 <= 132:
                후보.append((조각[1] not in [f"{h}호선" for h in 호선들], 조각[1], 위도, 경도))
    return min(후보) if 후보 else None


def 출입구_만들기(이미_있는):
    """이미_있는 표에 없는 역만 카카오로 찾는다."""
    필요한_역 = {역: 호선들 for 역, 호선들 in 모든_역().items()
                 if 이미_있는.empty or 역 not in set(이미_있는["역"])}
    if not 필요한_역:
        print("  모든 역 출입구가 data/exits.csv 에 있어요 (카카오 호출 안 함, 새로 받으려면 --refresh)")
        return 이미_있는

    키 = os.getenv("KAKAO_REST_API_KEY")
    if not 키:
        raise RuntimeError("⛔ .env 에 KAKAO_REST_API_KEY 가 없어요")

    줄들, 호출 = [], 0
    for 역, 호선들 in 필요한_역.items():
        for 번호 in range(1, 최대_출구번호 + 1):
            if 호출 >= 최대_호출:
                print(f"  카카오 호출 {최대_호출}번에 닿아 멈췄어요")
                break
            # "송파나루역 2번출구"로는 상위 결과에 안 나오고 "송파나루역 9호선 2번출구"로만 나오는 경우가 있음
            # → 호선을 넣어 먼저 찾고, 없으면(예: 신분당선 이름으로만 등록된 강남역 3번) 호선 없이 한 번 더
            찾음 = None
            for 검색어 in [f"{역}역 {h}호선 {번호}번출구" for h in 호선들] + [f"{역}역 {번호}번출구"]:
                if 호출 >= 최대_호출:
                    break
                찾음 = 출구_찾기(카카오_검색(검색어, 키), 역, 호선들, 번호)  # ⛔ 에러는 그대로 위로 → 바로 멈춤
                호출 += 1
                time.sleep(0.1)
                if 찾음:
                    break
            if not 찾음:  # 출구 번호는 이어져 있어서, 없는 번호가 나오면 그 역은 끝
                break
            줄들.append({"역": 역, "출구": f"{번호}번", "등록_노선": 찾음[1], "위도": 찾음[2], "경도": 찾음[3]})

        # 카카오에 출구가 1개 이하로만 등록된 역(예: 송파나루)은 역 위치 자체를 보조 기준점으로 더한다
        if sum(줄["역"] == 역 for 줄 in 줄들) <= 1 and 호출 < 최대_호출:
            이름 = f"{역}역 {호선들[0]}호선"
            for 장소 in 카카오_검색(이름, 키):
                if 장소.get("place_name") == 이름:
                    줄들.append({"역": 역, "출구": "역 위치", "등록_노선": f"{호선들[0]}호선",
                                 "위도": float(장소["y"]), "경도": float(장소["x"])})
                    break
            호출 += 1
            time.sleep(0.1)
    print(f"  카카오 호출 {호출}번 ({', '.join(필요한_역)})")
    return pd.concat([이미_있는, pd.DataFrame(줄들)], ignore_index=True)


# ── 3) 상권 격자 ───────────────────────────────────────────
def 가로세로_m(위도, 경도, 출구_위도, 출구_경도):
    """가로+세로(맨해튼) 거리 m. 위도에 따른 1도의 실제 길이(WGS84 타원체 공식)를 쓴다.
    make_map.py 의 브라우저 커서 계산과 **같은 식**이라 표 숫자와 커서 숫자가 똑같이 나온다."""
    φ = math.radians(위도)
    위도_1도 = 111132.92 - 559.82 * math.cos(2 * φ) + 1.175 * math.cos(4 * φ)
    경도_1도 = 111412.84 * math.cos(φ) - 93.5 * math.cos(3 * φ)
    return (경도 - 출구_경도).abs() * 경도_1도 + (위도 - 출구_위도).abs() * 위도_1도


def 칸_만들기(출입구):
    a = 읽기(상권_원본)
    출입구 = 출입구.copy()
    출입구["x"], 출입구["y"] = 위경도_to_TM.transform(출입구["경도"].values, 출입구["위도"].values)

    결과 = []
    for 지역, 설정 in 지역들.items():
        고른 = a[a["상권_코드_명"].isin(설정["상권"])]
        없는 = set(설정["상권"]) - set(고른["상권_코드_명"])
        if 없는:
            raise RuntimeError(f"area_raw.csv 에 없는 상권 이름이에요: {', '.join(없는)}")

        # 1) 상권마다 정사각형(중심 ± √면적/2)을 덮는 100m 칸 번호 모으기 (모든 상권이 같은 격자를 써서 겹치면 한 칸)
        칸_주인 = {}  # (ix, iy) → (상권명, 상권 중심까지 거리)
        for _, 상권 in 고른.iterrows():
            cx, cy, 반 = 상권["엑스좌표_값"], 상권["와이좌표_값"], 상권["영역_면적"] ** 0.5 / 2
            for ix in range(math.floor((cx - 반) / 셀_m), math.floor((cx + 반) / 셀_m) + 1):
                for iy in range(math.floor((cy - 반) / 셀_m), math.floor((cy + 반) / 셀_m) + 1):
                    거리 = math.hypot((ix + 0.5) * 셀_m - cx, (iy + 0.5) * 셀_m - cy)
                    if (ix, iy) not in 칸_주인 or 거리 < 칸_주인[(ix, iy)][1]:
                        칸_주인[(ix, iy)] = (상권["상권_코드_명"], 거리)

        if "자르기" in 설정:
            가, 나 = (출입구.loc[출입구["역"] == 역, ["x", "y"]].mean() for 역 in 설정["자르기"])
            dx, dy = 나["x"] - 가["x"], 나["y"] - 가["y"]
            길이2 = dx * dx + dy * dy
            칸_주인 = {
                (ix, iy): 주인 for (ix, iy), 주인 in 칸_주인.items()
                # 칸 가운데를 가→나 선에 비춘 위치가 1 이하(= 나역까지)인 칸만
                if (((ix + 0.5) * 셀_m - 가["x"]) * dx + ((iy + 0.5) * 셀_m - 가["y"]) * dy) / 길이2 <= 1
            }

        # 2) 칸 이름: 상권 안에서 서→동 A,B,C… / 북→남 1,2,3…
        칸표 = pd.DataFrame([{"ix": ix, "iy": iy, "상권원명": 주인[0]} for (ix, iy), 주인 in 칸_주인.items()])
        칸표["상권명"] = 칸표["상권원명"].map(lambda n: 상권_별명.get(n, n))
        칸표["열"] = 칸표.groupby("상권명")["ix"].transform(lambda s: s - s.min())
        칸표["행"] = 칸표.groupby("상권명")["iy"].transform(lambda s: s.max() - s)
        칸표["구역"] = 칸표["열"].map(lambda i: chr(ord("A") + i)) + (칸표["행"] + 1).astype(str)

        # 3) 칸 중심 → 역마다 가장 가까운 출구까지 맨해튼 거리
        for _, 칸 in 칸표.iterrows():
            x0, y0 = 칸["ix"] * 셀_m, 칸["iy"] * 셀_m
            경도들, 위도들 = TM_to_위경도.transform([x0, x0 + 셀_m, x0 + 셀_m / 2], [y0, y0 + 셀_m, y0 + 셀_m / 2])
            for 역 in 설정.get("상권_역", {}).get(칸["상권원명"], 설정["역"]):
                문들 = 출입구[출입구["역"] == 역]
                거리들 = 가로세로_m(위도들[2], 경도들[2], 문들["위도"], 문들["경도"])
                결과.append({
                    "지역": 지역, "상권명": 칸["상권명"], "구역": 칸["구역"],
                    "남서_위도": 위도들[0], "남서_경도": 경도들[0],
                    "북동_위도": 위도들[1], "북동_경도": 경도들[1],
                    "중심_위도": 위도들[2], "중심_경도": 경도들[2],
                    "역": 역, "출구": 문들.loc[거리들.idxmin(), "출구"], "도보거리_m": 거리들.min(),
                })
    return pd.DataFrame(결과).sort_values(["지역", "상권명", "구역", "역"]).reset_index(drop=True)


def 저장(df, 이름):
    df.to_csv(폴더 / 이름, index=False, encoding="utf-8-sig")
    print(f"  → data/{이름} ({len(df)}줄)")


if __name__ == "__main__":
    try:
        print("1) 막차")
        막차, 방향별 = 막차_만들기()
        저장(막차, "last_train.csv")
        저장(방향별, "last_by_dir.csv")

        print("2) 출입구")
        출입구_파일 = 폴더 / "exits.csv"
        이미_있는 = pd.DataFrame()
        if 출입구_파일.exists() and "--refresh" not in sys.argv:
            이미_있는 = pd.read_csv(출입구_파일, encoding="utf-8-sig")
            이미_있는 = 이미_있는[이미_있는["역"].isin(모든_역())]  # 지금 안 쓰는 역은 뺌
        출입구 = 출입구_만들기(이미_있는)
        저장(출입구, "exits.csv")
        print("  " + " · ".join(f"{역} {(출입구['역'] == 역).sum()}개" for 역 in 모든_역()))

        print("3) 상권 격자")
        칸 = 칸_만들기(출입구)
        저장(칸, "cells.csv")
        for 지역 in 지역들:
            칸들 = 칸[칸["지역"] == 지역].drop_duplicates(["상권명", "구역"])
            print(f"  {지역}: " + " · ".join(f"{이름} {n}칸" for 이름, n in 칸들["상권명"].value_counts().items()))

        print("\n검산: 강남 → 홍대입구 막차")
        print(막차[(막차["출발역"] == "강남") & (막차["목적지역"] == "홍대입구")]
              [["주중주말", "방향", "행선지", "막차_시각", "목적지_도착"]].to_string(index=False))
    except RuntimeError as 에러:
        print(에러)
        sys.exit(1)
