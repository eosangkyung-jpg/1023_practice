"""막차 지도 — 상권의 작은 구역(100m 칸)에서 가까운 역까지 걸어서 막차를 타려면 늦어도 몇 시에 나와야 하나.
화면은 휴대폰 기준(폭 약 400px) 한 줄 배치. PC에서 열어도 가운데에 휴대폰 폭으로 보인다.
표지(아무 데나 누르면) → 지도 화면. 사진은 레포 맨 위 image/ 에서 읽기만 한다.
흰 배경(밝은 테마)은 .streamlit/config.toml 에서.

실행:  streamlit run app.py   → http://localhost:8501
(카카오 콘솔 JavaScript SDK 도메인에 http://localhost:8501 등록 필요. 8502·IP·127.0.0.1 로 열면 지도가 안 떠요)
.env 를 고쳤다면 Ctrl+C 후 다시 streamlit run.
"""
import base64
import io
import math
import os
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFilter

from make_map import kakao_map_html

# ── 디자인 값은 여기 한 곳에서만 ────────────────────────────
STYLE = {
    "제목": ["내가", "막차에게로", "간다"],  # 줄마다 오려 붙인 종이 글자로
    # 제목 종이 조각: (종이색, 글자색, 글꼴, 크기px, 기울기deg, 위아래px, 자른 모양 번호, 흰 여백 테두리)
    # 색은 참고 이미지(오려 붙인 알파벳)에서: 먹자주·분홍·버건디·크림·하늘·살구·국방·노랑·연보라
    "제목_조각": [
        ("#2B2530", "#F38BB0", "Black Han Sans", 46, -6, 2, 0, False),
        ("#EADFC8", "#8E2F3A", "Song Myung", 50, 4, -3, 3, True),
        ("#8E2F3A", "#F2D45C", "Do Hyeon", 48, 3, 0, 1, False),
        ("#A9C8E8", "#2B2530", "Hahmlet", 44, -4, 4, 4, False),
        ("#F4C4A8", "#8E2F3A", "Jua", 40, 6, -2, 2, True),
        ("#3B3A24", "#EADFC8", "Nanum Myeongjo", 46, -3, 3, 5, False),
        ("#E94B87", "#FFF4E0", "Gugi", 42, 5, -1, 0, False),
        ("#F2D45C", "#E94B87", "Black Han Sans", 52, -5, 1, 3, False),
        ("#D9C2E0", "#2B2530", "Gowun Batang", 48, 4, -2, 1, True),
    ],
    "화면_폭_px": 430,   # 휴대폰 화면 폭 (보통 360~430)
    # 표지: 누워서 위를 가리키는 사람 사진 + 큰 콜라주 제목
    "표지_사진": "mapview.jpeg",
    "표지_제목_배율": 1.25,
    "지도_높이": 440,
    # 지도 화면 왼쪽 아래에 고정되는 사람 (흰 배경을 뺀 누끼)
    "기도_사진": "pls.jpeg",
    "기도_너비_px": 180,
    # 지도 화면 배경 사진과 진하기 (0~1, 흰색 위에 겹침)
    "지도_배경_사진": "bg.jpeg",
    "지도_배경_진하기": 0.5,
    # 도보 시간(분) → 색. 사이 값은 섞어서 부드럽게 (초록 → 노랑 → 빨강)
    "색_단계": [(0, "#2E7D32"), (8, "#F9A825"), (16, "#C62828")],
    "범례": "🟩 가까움 → 🟨 8분 → 🟥 16분+",
    # 칸 하나를 원 여러 겹으로: [반경 비율(칸 크기 대비), 투명도] → 가운데 진하고 바깥은 투명
    "번짐": [[0.95, 0.10], [0.70, 0.10], [0.45, 0.12]],
    # 탭했을 때 가는 출구 점 색 = 역 색
    "역_색": {"강남": "#1565C0", "역삼": "#8E24AA", "신논현": "#8D6E63", "송파나루": "#1565C0", "석촌": "#8E24AA", "잠실": "#2E7D32"},
}
셀_m = 100  # data_prep.py 의 칸 크기와 같게
최대_거리_m = 1500  # 출구에서 이보다 먼 곳은 안내를 안 띄움

# ── 계산 가정 (화면 아래 '계산 방법'에도 그대로 보여 줌) ─────
보통_속도, 천천히_속도 = 67, 50  # m/분
역_안_이동_분 = {"강남": 3, "역삼": 3, "신논현": 4, "송파나루": 4, "석촌": 4, "잠실": 5}  # 출구 → 승강장 (가정값, 잠실은 환승역이라 넓음)
여유_분 = 3
비슷함_분 = 2  # 다른 역과 도보 시간 차이가 이 안이면 비교로 보여 줌
요일_코드 = {"평일": "DAY", "토요일": "SAT", "일·공휴일": "END"}
출처 = (
    "출처: 서울교통공사 서울 도시철도 열차운행시각표 (공공데이터포털, 2026-09-01 기준) · "
    "서울시 상권분석서비스 영역-상권 (서울 열린데이터광장, 2026-09-11 갱신) · 출입구 좌표: 카카오 로컬 API"
)

폴더 = Path(__file__).parent / "data"
사진_폴더 = Path(__file__).resolve().parent.parent / "image"  # 레포 맨 위 image/ (읽기만 함)
load_dotenv(override=True)


@st.cache_data
def 읽기(이름):
    return pd.read_csv(폴더 / 이름, encoding="utf-8-sig")


@st.cache_data
def 사진_주소(이름):
    """image/ 의 사진을 페이지에 바로 넣는 data 주소로 (파일은 옮기지 않음)."""
    return "data:image/jpeg;base64," + base64.b64encode((사진_폴더 / 이름).read_bytes()).decode()


@st.cache_data
def 누끼_주소(이름):
    """흰 배경 사진에서 가장자리와 이어진 흰 부분만 투명하게 뺀 PNG (사람 안쪽의 흰색은 그대로).
    원본 파일은 그대로 두고 메모리에서만 처리한다."""
    그림 = Image.open(사진_폴더 / 이름).convert("RGB")
    흰 = (np.asarray(그림).min(axis=2) > 232).astype(np.uint8) * 255   # 거의 흰 픽셀
    판 = Image.fromarray(흰, "L").copy()   # copy: 배열과 메모리를 같이 쓰면 칠하기(floodfill)가 안 먹힘
    w, h = 판.size
    for 점 in [(0, 0), (w - 1, 0), (w - 1, h - 1), (w // 2, 0)]:      # 가장자리에서 흰색 이어진 곳 = 배경
        if 판.getpixel(점) == 255:
            ImageDraw.floodfill(판, 점, 128)
    알파 = Image.fromarray(np.where(np.asarray(판) == 128, 0, 255).astype(np.uint8), "L")
    알파 = 알파.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1))  # 흰 테두리 깎고 부드럽게
    그림.putalpha(알파)
    그림 = 그림.crop(알파.getbbox())                                      # 사람만 남게 자르기
    버퍼 = io.BytesIO()
    그림.save(버퍼, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(버퍼.getvalue()).decode()


def 분_표시(분):
    """1461.5 → '00:21' (초는 버림)."""
    분 = math.floor(분)
    return f"{(분 // 60) % 24:02d}:{분 % 60:02d}"


def 칸_색(도보_분):
    """색_단계 사이를 섞어서 도보 시간에 맞는 색 하나를 만든다."""
    단계 = STYLE["색_단계"]
    if 도보_분 <= 단계[0][0]:
        return 단계[0][1]
    for (앞_분, 앞_색), (뒤_분, 뒤_색) in zip(단계, 단계[1:]):
        if 도보_분 <= 뒤_분:
            비율 = (도보_분 - 앞_분) / (뒤_분 - 앞_분)
            앞, 뒤 = (int(앞_색[i:i + 2], 16) for i in (1, 3, 5)), (int(뒤_색[i:i + 2], 16) for i in (1, 3, 5))
            return "#" + "".join(f"{round(a + (b - a) * 비율):02X}" for a, b in zip(앞, 뒤))
    return 단계[-1][1]


def 오늘_요일():
    """오늘(한국 시간) 기준 기본 요일. 새벽 4시 전이면 전날 막차 기준. 공휴일은 직접 고르기."""
    지금 = datetime.now(ZoneInfo("Asia/Seoul"))
    요일 = (지금.weekday() - (1 if 지금.hour < 4 else 0)) % 7
    return {5: "토요일", 6: "일·공휴일"}.get(요일, "평일")


def 콜라주_제목(줄들, 크게=1.0):
    """글자마다 오려 붙인 종이 조각 HTML. 종이 결·구김은 SVG 노이즈, 자른 테두리는 clip-path."""
    자른_모양 = [  # 가위로 자른 듯 모서리가 조금씩 다른 조각
        "polygon(3% 6%, 97% 0%, 100% 92%, 0% 100%)",
        "polygon(0% 2%, 94% 5%, 100% 100%, 6% 95%)",
        "polygon(5% 0%, 100% 4%, 96% 97%, 0% 92%)",
        "polygon(0% 0%, 100% 8%, 98% 100%, 4% 96%, 0% 60%)",
        "polygon(2% 4%, 60% 0%, 100% 6%, 97% 100%, 0% 97%)",
        "polygon(0% 7%, 100% 0%, 95% 94%, 3% 100%)",
    ]
    조각들 = STYLE["제목_조각"]
    i, 줄_글 = 0, []
    for 줄 in 줄들:
        글자들 = []
        for 글자 in 줄:
            종이, 색, 글꼴, 크기, 기울기, 위아래, 모양, 여백 = 조각들[i % len(조각들)]
            i += 1
            글자들.append(
                f'<span class="조각" style="transform:translateY({위아래 * 크게:.0f}px) rotate({기울기}deg)">'
                f'<span class="종이{" 여백" if 여백 else ""}" style="background-color:{종이};color:{색};'
                f"font-family:'{글꼴}',serif;font-size:{크기 * 크게:.0f}px;clip-path:{자른_모양[모양]}\">{escape(글자)}</span></span>"
            )
        줄_글.append(f'<div class="줄">{"".join(글자들)}</div>')
    return f'<div class="콜라주" role="heading" aria-level="1" aria-label="{escape(" ".join(줄들))}">{"".join(줄_글)}</div>'


# 종이 결(고운 노이즈) + 구김(길게 늘인 큰 노이즈)
_종이결 = quote(
    "<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'>"
    "<filter id='a'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/>"
    "<feColorMatrix values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 .22 0'/></filter>"
    "<filter id='b'><feTurbulence type='fractalNoise' baseFrequency='.012 .05' numOctaves='2' seed='7'/>"
    "<feColorMatrix values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 .16 0'/></filter>"
    "<rect width='100%' height='100%' filter='url(#a)'/><rect width='100%' height='100%' filter='url(#b)'/></svg>"
)
st.set_page_config(page_title="내가 막차에게로 간다", page_icon="🚇", layout="centered", initial_sidebar_state="collapsed")
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Black+Han+Sans&family=Do+Hyeon&family=Gowun+Batang:wght@700&family=Gugi&family=Hahmlet:wght@900&family=Jua&family=Nanum+Myeongjo:wght@800&family=Song+Myung&display=swap');
.콜라주 { display: flex; flex-direction: column; align-items: center; gap: 4px; margin: 6px 0 12px; }
.콜라주 .줄 { display: flex; justify-content: center; align-items: center; gap: 3px; }
.콜라주 .조각 { display: inline-block; filter: drop-shadow(1.5px 2.5px 1.2px rgba(0,0,0,.38)); }  /* 붙인 종이 그림자 */
.콜라주 .종이 { position: relative; display: inline-block; line-height: 1; padding: .16em .24em .1em;
  text-shadow: 0 0 1px rgba(0,0,0,.25);
  background-image:
    url("data:image/svg+xml,__종이결__"),
    linear-gradient(115deg, rgba(255,255,255,.28), rgba(255,255,255,0) 35%, rgba(0,0,0,.12) 58%, rgba(255,255,255,.10) 78%, rgba(0,0,0,.08));
  background-size: 160px 160px, cover; }
.콜라주 .종이::after { content: ""; position: absolute; inset: 0; pointer-events: none;  /* 바랜 잉크 */
  background: url("data:image/svg+xml,__종이결__") 0 0 / 120px 120px; mix-blend-mode: multiply; opacity: .3; }
.콜라주 .종이.여백 { box-shadow: inset 0 0 0 4px #F7F2E7; padding: .22em .3em .16em; }  /* 잡지에서 여백째 오린 조각 */
</style>""".replace("__종이결__", _종이결), unsafe_allow_html=True)

# 휴대폰 폭 한 줄 배치: 본문 폭을 줄이고 여백을 좁힌다
st.markdown(f"""<style>
  .block-container {{ max-width: {STYLE["화면_폭_px"]}px; padding: 1.2rem 0.8rem 3rem; }}
  [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {{ display: none; }}
  [data-testid="stMetricValue"] {{ font-size: 1.6rem; }}
  /* 선택 옵션 · 메뉴 가운데 정렬 */
  /* key 는 영문으로: Streamlit 이 key 의 한글을 '-' 로 바꿔 st-key-○○ 클래스를 만들기 때문 */
  /* 위젯 상자는 내용 폭만큼만 넓어서, 상자 자체를 가운데로 보낸다 */
  .st-key-region, .st-key-day, .st-key-slow {{ align-self: center; }}
  [data-testid="stExpander"] summary {{ justify-content: center; }}
  [data-testid="stExpander"] summary > span {{ flex: 0 1 auto !important; width: auto !important; }}
</style>""", unsafe_allow_html=True)

# ── 표지 화면: 아무 데나 누르면 지도 화면으로 ─────────────────
if st.session_state.get("화면") != "지도":
    st.markdown(f"""<style>
      .표지 {{ position: relative; width: 100%; aspect-ratio: 675 / 1200;
               background: url('{사진_주소(STYLE["표지_사진"])}') center bottom / cover no-repeat; }}
      .표지 .콜라주 {{ position: absolute; top: 8%; left: 0; right: 0; margin: 0; }}
      .표지 .눌러 {{ position: absolute; top: 57%; left: 0; right: 0; text-align: center;
                     font-family: 'Black Han Sans', sans-serif; font-size: 20px; color: #1B1B1B;
                     animation: 깜빡 1.4s ease-in-out infinite; }}
      @keyframes 깜빡 {{ 50% {{ opacity: .3; }} }}
      /* 화면 전체를 덮는 투명 버튼 = 어디를 눌러도 시작 */
      .st-key-start {{ position: fixed; inset: 0; z-index: 1000; }}
      .st-key-start button {{ width: 100vw; height: 100vh; opacity: 0; cursor: pointer; }}
    </style>""", unsafe_allow_html=True)
    st.markdown(f'<div class="표지">{콜라주_제목(STYLE["제목"], STYLE["표지_제목_배율"])}'
                '<div class="눌러">👆 화면을 누르면 지도로</div></div>', unsafe_allow_html=True)
    st.button("지도 보기", key="start", on_click=lambda: st.session_state.update(화면="지도"))
    st.stop()

st.markdown(콜라주_제목(STYLE["제목"]), unsafe_allow_html=True)

# 지도 화면 배경: 사진을 흰색 위에 반투명하게 (흰 막을 1-진하기 만큼 덮음)
# 누끼 사람: 화면 왼쪽 아래 끝에 딱 붙여 고정 (스크롤해도 그 자리, 누르는 데 방해 안 되게)
_흰막 = 1 - STYLE["지도_배경_진하기"]
st.markdown(f"""<style>
  .stApp {{ background: linear-gradient(rgba(255,255,255,{_흰막}), rgba(255,255,255,{_흰막})),
                        url('{사진_주소(STYLE["지도_배경_사진"])}') center / cover fixed; }}
  [data-testid="stHeader"] {{ background: transparent; }}
  /* 배경 사진 위에서도 또렷하게: 옵션 · 메뉴 · 경고를 흰 박스로 */
  .st-key-region, .st-key-day, .st-key-slow {{ background: #FFFFFF; border: 1px solid rgba(0,0,0,.12);
      border-radius: 14px; padding: 6px 14px; box-shadow: 0 2px 8px rgba(0,0,0,.12); }}
  [data-testid="stExpander"] details {{ background: #FFFFFF; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,.12); }}
  .기도 {{ position: fixed; left: 0; bottom: 0; display: block; margin: 0;
           width: {STYLE["기도_너비_px"]}px; height: auto; z-index: 50; pointer-events: none; }}
</style><img class="기도" src="{누끼_주소(STYLE["기도_사진"])}" alt="">""", unsafe_allow_html=True)

방향표, 출입구, 칸표 = 읽기("last_by_dir.csv"), 읽기("exits.csv"), 읽기("cells.csv")

# ── 조건 (화면 위, 한 손으로 누르기 쉽게) ─────────────────────
지역 = st.radio("지역", list(칸표["지역"].unique()), horizontal=True, label_visibility="collapsed", key="region")
요일들 = list(요일_코드)
요일 = st.radio("요일", 요일들, index=요일들.index(오늘_요일()), horizontal=True, label_visibility="collapsed", key="day")
천천히 = st.toggle("천천히 걷기", help=f"보통 분당 {보통_속도}m, 천천히 분당 {천천히_속도}m", key="slow")
속도 = 천천히_속도 if 천천히 else 보통_속도
역들 = list(칸표.loc[칸표["지역"] == 지역, "역"].unique())

# ── 막차: 역마다 방향별 마지막 열차 ("○○ 방면" = 바로 다음 역) ──
방향 = 방향표[방향표["출발역"].isin(역들) & (방향표["주중주말"] == 요일_코드[요일])].sort_values("출발_분")
막차 = 방향.groupby("출발역").tail(1).set_index("출발역")  # 역에서 가장 늦은 열차 (표·숫자 기준)
후보_역 = [역 for 역 in 역들 if 역 in 막차.index]


# ── 필터된 표 view 하나에서 지도를 만든다 ──────────────────
후보 = 칸표[(칸표["지역"] == 지역) & 칸표["역"].isin(후보_역)].copy()
if not 후보.empty:  # 빈 표에 apply 를 하면 칸 종류가 꼬임
    후보["도보_분"] = (후보["도보거리_m"] / 속도).apply(math.ceil)

줄들 = []
for (상권명, 구역), 칸 in 후보.groupby(["상권명", "구역"]):
    칸 = 칸.sort_values(["도보_분", "도보거리_m"])
    줄들.append(칸.iloc[0].to_dict())  # 가장 가까운 역

view = pd.DataFrame(줄들)

# ── 지도 (화면의 주인공) ────────────────────────────────────
if view.empty:
    st.info("조건에 맞는 곳이 없어요")
else:
    js키 = os.getenv("KAKAO_JS_KEY")
    if not js키:
        st.error(".env 에 KAKAO_JS_KEY 가 없어요")
    else:
        점들 = [
            {"위도": 줄["중심_위도"], "경도": 줄["중심_경도"], "색": 칸_색(줄["도보_분"]), "반경_m": 셀_m}
            for _, 줄 in view.iterrows()
        ]
        칸별_역 = 후보.groupby(["상권명", "구역"])["역"].unique()
        지도_설정 = {
            "역들": [
                {"역": 역, "색": STYLE["역_색"][역], "역안_분": 역_안_이동_분[역],
                 "방향들": [{"방면": d["다음역"], "막차_분": float(d["출발_분"]), "막차_시각": d["막차_시각"],
                             "행선지": d["행선지"]} for _, d in 방향[방향["출발역"] == 역].iterrows()],
                 "출구들": [{"이름": 줄["출구"], "위도": 줄["위도"], "경도": 줄["경도"]}
                            for _, 줄 in 출입구[출입구["역"] == 역].iterrows()]}
                for 역 in 후보_역
            ],
            "속도": 속도, "여유_분": 여유_분, "비슷함_분": 비슷함_분,
            "최대_거리_m": 최대_거리_m, "번짐": STYLE["번짐"],
            "구역들": [
                {"이름": f"{줄['상권명']} {줄['구역']}", "남서_위도": 줄["남서_위도"], "남서_경도": 줄["남서_경도"],
                 "북동_위도": 줄["북동_위도"], "북동_경도": 줄["북동_경도"],
                 "역들": list(칸별_역[(줄["상권명"], 줄["구역"])])}
                for _, 줄 in view.iterrows()
            ],
        }
        st.iframe(kakao_map_html(js키, 점들, 지도_설정, STYLE["지도_높이"]), height=STYLE["지도_높이"])

    # ── 자세히 (접어 두기) ──────────────────────────────────
    with st.expander("🚉 역별 방향별 막차"):
        for 역 in 후보_역:
            st.markdown(f"**{역}역**")
            for _, d in 방향[방향["출발역"] == 역].iterrows():
                st.markdown(f"- {d['다음역']} 방면 **{d['막차_시각']}** · {d['행선지']}행")

with st.expander("ℹ️ 계산 방법 · 출처"):
    st.caption(
        "상권을 100m × 100m 칸으로 나누고(상권 영역은 면적 기준 정사각형 근사), 칸 가운데(지도는 탭한 자리) → "
        "가장 가까운 출구까지 **가로+세로 거리**(건물을 가로지르지 않는 격자 경로)로 계산한 예상치예요. "
        "그 자리에서 **걸어서 가장 가까운 역**을 골랐어요. "
        f"걸음 속도 보통 분당 {보통_속도}m · 천천히 분당 {천천히_속도}m, "
        f"역 안 이동 {' · '.join(f'{역} {역_안_이동_분[역]}분' for 역 in 후보_역)}, 여유 {여유_분}분을 뺐어요. "
        "열차 지연은 반영되지 않아요. 시각표 기준 데이터예요."
    )
    st.caption(출처)
