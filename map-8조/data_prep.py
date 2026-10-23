"""원본 데이터를 정리해 app.py 가 읽을 작은 표 3개를 만든다. (화면 코드 없음)

실행:  python data_prep.py            (출입구는 data/exits.csv 가 있으면 카카오를 다시 부르지 않음)
       python data_prep.py --refresh  (출입구 좌표를 카카오에서 새로 받기)

만드는 파일
- data/last_train.csv : 출발역 → 목적지역 2호선 막차 (요일별)
- data/exits.csv      : 강남·잠실역 2호선 출입구 좌표 (카카오)
- data/areas.csv      : 역 1km 안 상권 사각 박스 + 가장 먼 꼭짓점에서 출입구까지 맨해튼 거리
"""
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

역목록 = ["강남", "잠실"]
노선 = 2
상권_반경_m = 1000      # 역 중심에서 상권 중심까지 이 거리 안만
최대_출구번호 = 15      # 역마다 1~15번 출구까지 찾아봄
최대_호출 = 30          # 카카오 호출 상한 (남의 서버 예절)
최대_소요_분 = 100      # 순환선이라 같은 열차코드가 한 바퀴 뒤 다시 나옴 → 그보다 짧은 것만 같은 운행으로 봄

# 상권 좌표는 EPSG:5181(중부원점 TM, 미터). 지도는 EPSG:4326(위도·경도)
TM_to_위경도 = Transformer.from_crs("EPSG:5181", "EPSG:4326", always_xy=True)
위경도_to_TM = Transformer.from_crs("EPSG:4326", "EPSG:5181", always_xy=True)

load_dotenv(override=True)


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
    t = 읽기(시각표_원본)
    t = t[t["호선"] == 노선].copy()
    t["출발_분"] = t["열차출발시간"].map(시각_분)
    # 출발 칸이 빈 줄 = 그 역이 종착 → 도착 시각을 씀
    t["도착_분"] = t["열차도착시간"].map(시각_분).fillna(t["출발_분"])

    결과 = []
    for 출발역 in 역목록:
        출발 = t[(t["역사명"] == 출발역) & t["출발_분"].notna()]
        출발 = 출발[["주중주말", "방향", "열차코드", "출발_분", "도착역"]]
        도착 = t[t["역사명"] != 출발역][["주중주말", "열차코드", "역사명", "도착_분"]]

        짝 = 출발.reset_index().merge(도착, on=["주중주말", "열차코드"])
        짝["소요_분"] = 짝["도착_분"] - 짝["출발_분"]
        짝 = 짝[(짝["소요_분"] > 0) & (짝["소요_분"] <= 최대_소요_분)]
        # 같은 출발 줄에서 같은 목적지가 두 번 잡히면 가장 먼저 닿는 것만
        짝 = 짝.sort_values("소요_분").drop_duplicates(["index", "역사명"])

        막차 = 짝.sort_values("출발_분").groupby(["주중주말", "역사명"]).tail(1)
        막차 = 막차.assign(출발역=출발역).rename(columns={"역사명": "목적지역", "도착역": "행선지"})
        결과.append(막차)

    막차 = pd.concat(결과, ignore_index=True)
    막차["막차_시각"] = 막차["출발_분"].map(분_표시)
    막차["목적지_도착"] = 막차["도착_분"].map(분_표시)
    막차["소요_분"] = 막차["소요_분"].round().astype(int)
    막차 = 막차[["출발역", "목적지역", "주중주말", "방향", "행선지",
                 "출발_분", "막차_시각", "목적지_도착", "소요_분"]]
    return 막차.sort_values(["출발역", "주중주말", "목적지역"]).reset_index(drop=True)


# ── 2) 출입구 (카카오) ─────────────────────────────────────
def 카카오_검색(검색어, 키):
    try:
        res = requests.get(  # 🔌 API 호출
            API["키워드검색"],
            headers={"Authorization": f"KakaoAK {키}"},
            params={"query": 검색어, "size": 5},
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


def 출구_찾기(문서들, 역, 번호):
    """'강남역 2호선 3번출구' 또는 '강남역 신분당선 3번출구' 처럼 노선 이름이 달라도 같은 출구로 본다.
    (같은 출구가 다른 노선 이름으로만 등록된 경우가 있음) 2호선 이름이 있으면 그걸 먼저."""
    후보 = []
    for 장소 in 문서들:
        이름 = 장소.get("place_name", "")
        조각 = 이름.split()
        if len(조각) == 3 and 조각[0] == f"{역}역" and 조각[2] == f"{번호}번출구":
            위도, 경도 = float(장소["y"]), float(장소["x"])  # y = 위도, x = 경도
            if 33 <= 위도 <= 39 and 124 <= 경도 <= 132:
                후보.append((조각[1] != f"{노선}호선", 조각[1], 위도, 경도))
    return min(후보) if 후보 else None


def 출입구_만들기():
    키 = os.getenv("KAKAO_REST_API_KEY")
    if not 키:
        raise RuntimeError("⛔ .env 에 KAKAO_REST_API_KEY 가 없어요")

    줄들, 호출 = [], 0
    for 역 in 역목록:
        for 번호 in range(1, 최대_출구번호 + 1):
            if 호출 >= 최대_호출:
                print(f"  카카오 호출 {최대_호출}번에 닿아 멈췄어요")
                break
            찾음 = 출구_찾기(카카오_검색(f"{역}역 {번호}번출구", 키), 역, 번호)  # ⛔ 에러는 그대로 위로 → 바로 멈춤
            호출 += 1
            time.sleep(0.1)
            if not 찾음:  # 출구 번호는 이어져 있어서, 없는 번호가 나오면 그 역은 끝
                break
            줄들.append({"역": 역, "출구": f"{번호}번", "등록_노선": 찾음[1], "위도": 찾음[2], "경도": 찾음[3]})
    print(f"  카카오 호출 {호출}번")
    return pd.DataFrame(줄들)


# ── 3) 상권 박스 ───────────────────────────────────────────
def 상권_만들기(출입구):
    a = 읽기(상권_원본)
    a = a[a["상권_구분_코드_명"] != "관광특구"]  # 수 km² 짜리라 다른 상권을 덮어 버림

    출입구 = 출입구.copy()
    출입구["x"], 출입구["y"] = 위경도_to_TM.transform(출입구["경도"].values, 출입구["위도"].values)

    결과 = []
    for 역 in 역목록:
        문들 = 출입구[출입구["역"] == 역]
        중심 = 문들[["x", "y"]].mean()  # 역 중심 = 출입구들의 가운데

        근처 = a.copy()
        근처["역까지_m"] = ((근처["엑스좌표_값"] - 중심["x"]) ** 2 + (근처["와이좌표_값"] - 중심["y"]) ** 2) ** 0.5
        근처 = 근처[근처["역까지_m"] <= 상권_반경_m]

        for _, 상권 in 근처.iterrows():
            반 = 상권["영역_면적"] ** 0.5 / 2  # 정사각형 근사: 한 변 = √면적
            cx, cy = 상권["엑스좌표_값"], 상권["와이좌표_값"]
            꼭짓점들 = [(cx - 반, cy - 반), (cx + 반, cy - 반), (cx - 반, cy + 반), (cx + 반, cy + 반)]

            # 꼭짓점마다 가장 가까운 출구까지 맨해튼 거리 → 그중 가장 먼 꼭짓점 = 최악의 경우
            최악 = None
            for px, py in 꼭짓점들:
                거리들 = (문들["x"] - px).abs() + (문들["y"] - py).abs()
                후보 = (거리들.min(), 문들.loc[거리들.idxmin(), "출구"])
                if 최악 is None or 후보[0] > 최악[0]:
                    최악 = 후보

            경도들, 위도들 = TM_to_위경도.transform([cx - 반, cx + 반, cx], [cy - 반, cy + 반, cy])
            결과.append({
                "역": 역,
                "상권명": 상권["상권_코드_명"],
                "상권구분": 상권["상권_구분_코드_명"],
                "중심_위도": 위도들[2], "중심_경도": 경도들[2],
                "남서_위도": 위도들[0], "남서_경도": 경도들[0],
                "북동_위도": 위도들[1], "북동_경도": 경도들[1],
                "한변_m": round(반 * 2),
                "역까지_m": round(상권["역까지_m"]),
                "기준_출구": 최악[1],
                "도보거리_m": round(최악[0]),
            })
    return pd.DataFrame(결과).sort_values(["역", "도보거리_m"]).reset_index(drop=True)


def 저장(df, 이름):
    df.to_csv(폴더 / 이름, index=False, encoding="utf-8-sig")
    print(f"  → data/{이름} ({len(df)}줄)")


if __name__ == "__main__":
    try:
        print("1) 막차")
        막차 = 막차_만들기()
        저장(막차, "last_train.csv")

        print("2) 출입구")
        출입구_파일 = 폴더 / "exits.csv"
        if 출입구_파일.exists() and "--refresh" not in sys.argv:
            출입구 = pd.read_csv(출입구_파일, encoding="utf-8-sig")
            print(f"  data/exits.csv 를 다시 씀 (카카오 호출 안 함, 새로 받으려면 --refresh)")
        else:
            출입구 = 출입구_만들기()
            저장(출입구, "exits.csv")
        print("  " + " · ".join(f"{역} {(출입구['역'] == 역).sum()}개" for 역 in 역목록))

        print("3) 상권 박스")
        상권 = 상권_만들기(출입구)
        저장(상권, "areas.csv")
        print("  " + " · ".join(f"{역} {(상권['역'] == 역).sum()}곳" for 역 in 역목록))

        print("\n검산: 강남 → 홍대입구 막차")
        print(막차[(막차["출발역"] == "강남") & (막차["목적지역"] == "홍대입구")]
              [["주중주말", "방향", "행선지", "막차_시각", "목적지_도착"]].to_string(index=False))
    except RuntimeError as 에러:
        print(에러)
        sys.exit(1)
