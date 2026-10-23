"""카카오 로컬 '키워드로 장소 검색'으로 장소 5개를 찾아 터미널에 보여 준다.

다른 파일에서:  from place import search_place
"""
import os

import requests
from dotenv import load_dotenv

API = {
    "키워드검색": "https://dapi.kakao.com/v2/local/search/keyword.json",  # 🔌 카카오 키워드로 장소 검색
}

load_dotenv(override=True)


def 한국_좌표인가(위도, 경도):
    """한국 밖 좌표(뒤바뀐 값·0 좌표)를 걸러낸다."""
    return 33 <= 위도 <= 39 and 124 <= 경도 <= 132


def search_place(검색어, 개수=5):
    """검색어로 장소를 찾아 [{이름, 주소, 위도, 경도}, ...] 를 돌려준다.

    실패하면 한국어 한 줄 메시지로 RuntimeError 를 낸다.
    키·설정·연결 문제는 메시지가 ⛔ 로 시작한다 (반복 호출을 멈추는 신호).
    """
    키 = os.getenv("KAKAO_REST_API_KEY")
    if not 키:
        raise RuntimeError("⛔ .env 에 KAKAO_REST_API_KEY 가 없어요")

    try:
        res = requests.get(  # 🔌 API 호출
            API["키워드검색"],
            headers={"Authorization": f"KakaoAK {키}"},
            params={"query": 검색어, "size": min(개수, 15)},
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

    결과 = []
    for 장소 in res.json().get("documents", []):
        try:
            위도 = float(장소["y"])  # y = 위도
            경도 = float(장소["x"])  # x = 경도
        except (KeyError, ValueError):
            continue
        if not 한국_좌표인가(위도, 경도):
            continue
        결과.append({
            "이름": 장소.get("place_name", ""),
            "주소": 장소.get("road_address_name") or 장소.get("address_name", ""),
            "위도": 위도,
            "경도": 경도,
        })

    if not 결과:
        raise RuntimeError(f"'{검색어}'을(를) 찾지 못했어요")
    return 결과[:개수]


if __name__ == "__main__":
    검색어 = input("검색어를 입력하세요 (예: 성수동 카페): ").strip()
    if not 검색어:
        print("검색어가 비어 있어요")
    else:
        try:
            for 번호, 장소 in enumerate(search_place(검색어), start=1):
                print(f"{번호}. {장소['이름']}, {장소['주소']}, ({장소['위도']}, {장소['경도']})")
        except RuntimeError as 에러:
            print(에러)
    print("\n출처: 카카오 로컬 API (키워드로 장소 검색)")
