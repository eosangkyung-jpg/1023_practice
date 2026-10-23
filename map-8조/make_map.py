"""상권 칸을 부드럽게 번지는 원으로 그리고, 탭(PC는 커서)한 자리의 예상 도보 시간을 지도 아래 카드로 보여 주는 카카오 지도 HTML.

다른 파일에서:  from make_map import kakao_map_html
REST 키는 HTML에 넣지 않는다. 지도에는 JavaScript 키(js_key)만 쓴다.
커서 계산은 브라우저 안에서 끝난다 (카카오 API를 새로 부르지 않음). 계산식은 data_prep.py 와 같다:
  가장 가까운 출구까지 가로+세로 거리 → 올림(거리 ÷ 걸음 속도) = 도보 분
  나와야 할 시각 = 방향별 막차 − 도보 − 역 안 이동 − 여유
"""
import json

API = {
    "지도SDK": "https://dapi.kakao.com/v2/maps/sdk.js",  # 🔌 카카오 지도 JavaScript SDK (브라우저가 부름)
}

최대_점 = 400

HTML_틀 = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  html, body { margin: 0; overflow: hidden; font-family: sans-serif; }  /* iframe 안 스크롤바 없애기 */
  #틀 { position: relative; }
  #map { width: 100%; height: __높이__px; background: #ddd; }
  .안내 { margin: 0; padding: 3px 2px 0; font-size: 10.5px; line-height: 14px; color: #999;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  /* 휴대폰: 떠다니는 말풍선 대신 지도 아래에 붙는 카드 */
  .카드 { position: absolute; left: 8px; right: 8px; bottom: 8px; z-index: 5; pointer-events: none;
          background: rgba(255,255,255,0.97); color: #222; border-radius: 14px;
          box-shadow: 0 4px 14px rgba(0,0,0,0.22); padding: 10px 12px; font-size: 13px; line-height: 1.55; }
  .카드 b { display: block; font-size: 15px; margin-bottom: 2px; }
  .카드 .작게 { color: #777; font-size: 11.5px; margin-top: 2px; }
  .내위치 { width: 14px; height: 14px; border-radius: 50%; background: #222; border: 3px solid #fff;
           box-shadow: 0 0 0 2px rgba(0,0,0,0.25); }
  .출구점 { width: 12px; height: 12px; border-radius: 50%; border: 2px solid #fff;
           box-shadow: 0 0 0 1px rgba(0,0,0,0.4); }
</style>
</head>
<body>
<div id="틀"><div id="map"></div></div>
<p class="안내">지도가 회색이면: ① 카카오맵 사용 설정 ② 주소(포트까지) 등록 ③ 서버 주소로 열기</p>
<script src="__SDK__?appkey=__JS키__&autoload=false"></script>
<script>
const 점들 = __점__;
const 설정 = __설정__;
kakao.maps.load(function () {
  const map = new kakao.maps.Map(document.getElementById("map"), {
    center: new kakao.maps.LatLng(37.5665, 126.9780),
    level: 5,
  });

  // ① 칸마다 겹친 원 여러 겹 → 가운데는 진하고 바깥은 투명하게 번짐 (테두리 없음)
  const 범위 = new kakao.maps.LatLngBounds();
  점들.forEach(function (점) {
    const 중심 = new kakao.maps.LatLng(점.위도, 점.경도);  // (위도, 경도) 순서
    설정.번짐.forEach(function (겹) {
      new kakao.maps.Circle({
        map: map, center: 중심, radius: 점.반경_m * 겹[0],
        strokeWeight: 0, strokeOpacity: 0, fillColor: 점.색, fillOpacity: 겹[1],
      });
    });
    범위.extend(중심);
  });
  if (점들.length > 0) map.setBounds(범위);

  // ② 커서 자리 계산
  const 팁글 = document.createElement("div");
  팁글.className = "카드";
  팁글.textContent = "📍 지금 있는 곳을 지도에서 탭하세요 (PC는 마우스를 올리면 돼요)";
  document.getElementById("틀").append(팁글);
  const 내점 = document.createElement("div");
  내점.className = "내위치";
  const 내위치 = new kakao.maps.CustomOverlay({ content: 내점, xAnchor: 0.5, yAnchor: 0.5, zIndex: 10 });
  const 점글 = document.createElement("div");
  점글.className = "출구점";
  const 출구점 = new kakao.maps.CustomOverlay({ content: 점글, xAnchor: 0.5, yAnchor: 0.5, zIndex: 9 });
  const 길 = new kakao.maps.Polyline({ strokeWeight: 3, strokeColor: "#333", strokeOpacity: 0.85, strokeStyle: "shortdash" });

  function 분_표시(분) {
    분 = Math.floor(분);
    const 시 = Math.floor(분 / 60) % 24, 남은 = 분 % 60;
    return String(시).padStart(2, "0") + ":" + String(남은).padStart(2, "0");
  }
  function 가로세로_m(위도, 경도, 출구) {
    // 위도에 따른 1도의 실제 길이(WGS84 타원체 공식) — data_prep.py 의 pyproj 거리와 맞추려고
    const φ = 위도 * Math.PI / 180;
    const 위도_1도 = 111132.92 - 559.82 * Math.cos(2 * φ) + 1.175 * Math.cos(4 * φ);
    const 경도_1도 = 111412.84 * Math.cos(φ) - 93.5 * Math.cos(3 * φ);
    return Math.abs(경도 - 출구.경도) * 경도_1도 + Math.abs(위도 - 출구.위도) * 위도_1도;
  }
  function 구역_찾기(위도, 경도) {
    return 설정.구역들.find(function (k) {
      return k.남서_위도 <= 위도 && 위도 <= k.북동_위도 && k.남서_경도 <= 경도 && 경도 <= k.북동_경도;
    });
  }
  function 계산(위도, 경도, 허용) {
    // 상권 칸 안이면 그 칸에 정해진 역만 후보 (예: 송리단길은 송파나루·석촌)
    return 설정.역들.filter(function (역) { return !허용 || 허용.indexOf(역.역) >= 0; }).map(function (역) {
      let 가까운 = null, 거리 = Infinity;
      역.출구들.forEach(function (출구) {
        const d = 가로세로_m(위도, 경도, 출구);
        if (d < 거리) { 거리 = d; 가까운 = 출구; }
      });
      const 도보 = Math.ceil(거리 / 설정.속도);
      return { 역: 역.역, 색: 역.색, 출구: 가까운, 거리: 거리, 도보: 도보,
               방향들: 역.방향들.map(function (d) {
                 return d.방면 + " 방면 " + d.막차_시각 + " " + d.행선지 + "행 → 늦어도 "
                   + 분_표시(d.막차_분 - 도보 - 역.역안_분 - 설정.여유_분);
               }) };
    }).sort(function (a, b) { return a.도보 - b.도보 || a.거리 - b.거리; });
  }
  function 숨기기() { 팁글.style.display = "none"; 내위치.setMap(null); 출구점.setMap(null); 길.setMap(null); }

  function 보이기(위치) {
    const 위도 = 위치.getLat(), 경도 = 위치.getLng();
    const 칸 = 구역_찾기(위도, 경도);
    const 결과 = 계산(위도, 경도, 칸 ? 칸.역들 : null);
    const 첫 = 결과[0];
    if (!첫 || 첫.거리 > 설정.최대_거리_m) { 숨기기(); return; }

    // 줄들은 textContent 로 넣어 글자가 안 깨지게
    팁글.replaceChildren();
    const 제목 = document.createElement("b");
    제목.textContent = 첫.역 + "역 " + 첫.출구.이름 + " 출구 · 도보 " + 첫.도보 + "분";
    팁글.append(제목);
    첫.방향들
      .concat(결과.slice(1).filter(function (r) { return r.도보 - 첫.도보 <= 설정.비슷함_분; })
        .map(function (r) { return r.역 + "역이면 도보 " + r.도보 + "분"; }))
      .forEach(function (글) { const 줄 = document.createElement("div"); 줄.textContent = 글; 팁글.append(줄); });
    const 작게 = document.createElement("div");
    작게.className = "작게";
    작게.textContent = (칸 ? 칸.이름 + " · " : "") + "현재 위치 예상치 · 행선지는 따로 확인";
    팁글.append(작게);
    팁글.style.display = "block";
    내위치.setPosition(위치); 내위치.setMap(map);

    // 가는 출구 하나만 표시 + 가로·세로로 꺾인 점선 (건물을 가로지르지 않는 격자 경로)
    const 출구_위치 = new kakao.maps.LatLng(첫.출구.위도, 첫.출구.경도);
    점글.style.background = 첫.색;
    출구점.setPosition(출구_위치); 출구점.setMap(map);
    길.setPath([위치, new kakao.maps.LatLng(위도, 첫.출구.경도), 출구_위치]);
    길.setMap(map);
  }

  let 대기 = null;
  kakao.maps.event.addListener(map, "mousemove", function (e) {
    if (대기) return;
    대기 = requestAnimationFrame(function () { 대기 = null; 보이기(e.latLng); });
  });
  kakao.maps.event.addListener(map, "click", function (e) { 보이기(e.latLng); });
  document.getElementById("map").addEventListener("mouseleave", 숨기기);
});
</script>
</body>
</html>
"""


def _스크립트용(값):
    # json.dumps 로 넣고, 글자에 </script> 가 있어도 스크립트가 끊기지 않게 </ 를 바꾼다
    return json.dumps(값, ensure_ascii=False).replace("</", "<\\/")


def kakao_map_html(js_key, 점들, 설정, 높이=560):
    """지도 HTML 글자를 돌려준다.

    점들: [{위도, 경도, 색, 반경_m}, ...]  번지는 원으로 그릴 칸 가운데 (400개까지)
    설정: {역들: [{역, 색, 역안_분, 방향들: [{방면, 막차_분, 막차_시각, 행선지}], 출구들: [{이름, 위도, 경도}]}],
           속도(m/분), 여유_분, 비슷함_분, 최대_거리_m, 번짐: [[반경 비율, 투명도], ...],
           구역들: [{이름, 남서_위도, 남서_경도, 북동_위도, 북동_경도, 역들}]}
    """
    if not js_key:
        raise RuntimeError("⛔ .env 에 KAKAO_JS_KEY 가 없어요")
    return (
        HTML_틀.replace("__SDK__", API["지도SDK"])
        .replace("__JS키__", js_key)
        .replace("__높이__", str(int(높이)))
        .replace("__설정__", _스크립트용(설정))
        .replace("__점__", _스크립트용(list(점들)[:최대_점]))  # 데이터는 맨 마지막에 넣어 이름 속 __글자__ 가 안 바뀌게
    )
