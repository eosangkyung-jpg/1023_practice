"""출입구 마커 + 상권 사각 박스를 그린 카카오 지도 HTML 글자를 만든다. (지도 코드는 여기 한 곳)

다른 파일에서:  from make_map import kakao_map_html
REST 키는 HTML에 넣지 않는다. 지도에는 JavaScript 키(js_key)만 쓴다.
"""
import json

API = {
    "지도SDK": "https://dapi.kakao.com/v2/maps/sdk.js",  # 🔌 카카오 지도 JavaScript SDK (브라우저가 부름)
}

최대_마커 = 200

HTML_틀 = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { margin: 0; font-family: sans-serif; }
  #map { width: 100%; height: __높이__px; background: #ddd; }
  .안내 { margin: 0; padding: 6px 10px; font-size: 13px; color: #555; }
  .말풍선 { padding: 6px 10px; font-size: 13px; line-height: 1.5; min-width: 170px; color: #222; }
  .말풍선 b { display: block; }
</style>
</head>
<body>
<div id="map"></div>
<p class="안내">지도가 회색이면: ① 카카오맵 사용 설정 ② 지금 주소(포트까지) 등록 ③ 서버 주소로 열기</p>
<script src="__SDK__?appkey=__JS키__&autoload=false"></script>
<script>
const 마커들 = __마커__;
const 박스들 = __박스__;
kakao.maps.load(function () {
  const map = new kakao.maps.Map(document.getElementById("map"), {
    center: new kakao.maps.LatLng(37.5665, 126.9780),
    level: 5,
  });
  const 범위 = new kakao.maps.LatLngBounds();
  let 열린창 = null;

  // 줄들은 textContent 로 넣어 따옴표·태그가 있어도 안 깨지게
  function 말풍선(제목, 줄들) {
    const 내용 = document.createElement("div");
    내용.className = "말풍선";
    const b = document.createElement("b");
    b.textContent = 제목;
    내용.append(b);
    줄들.forEach(function (글) {
      const 줄 = document.createElement("div");
      줄.textContent = 글;
      내용.append(줄);
    });
    return new kakao.maps.InfoWindow({ content: 내용, removable: true });
  }
  function 열기(창, 위치, 마커) {
    if (열린창) 열린창.close();
    if (마커) 창.open(map, 마커); else { 창.setPosition(위치); 창.open(map); }
    열린창 = 창;
  }

  박스들.forEach(function (박스) {
    const 남서 = new kakao.maps.LatLng(박스.남서_위도, 박스.남서_경도);  // (위도, 경도) 순서
    const 북동 = new kakao.maps.LatLng(박스.북동_위도, 박스.북동_경도);
    const 사각 = new kakao.maps.Rectangle({
      map: map,
      bounds: new kakao.maps.LatLngBounds(남서, 북동),
      strokeWeight: 박스.선두께, strokeColor: 박스.색, strokeOpacity: 0.9,
      fillColor: 박스.색, fillOpacity: 박스.투명도,
    });
    범위.extend(남서); 범위.extend(북동);
    const 창 = 말풍선(박스.이름, 박스.줄들);
    kakao.maps.event.addListener(사각, "click", function (e) { 열기(창, e.latLng, null); });
  });

  마커들.forEach(function (점) {
    const 위치 = new kakao.maps.LatLng(점.위도, 점.경도);
    const 마커 = new kakao.maps.Marker({ map: map, position: 위치, title: 점.이름 });
    범위.extend(위치);
    const 창 = 말풍선(점.이름, 점.줄들 || []);
    kakao.maps.event.addListener(마커, "click", function () { 열기(창, 위치, 마커); });
  });

  if (마커들.length + 박스들.length > 0) map.setBounds(범위);
});
</script>
</body>
</html>
"""


def _스크립트용(값):
    # json.dumps 로 넣고, 글자에 </script> 가 있어도 스크립트가 끊기지 않게 </ 를 바꾼다
    return json.dumps(값, ensure_ascii=False).replace("</", "<\\/")


def kakao_map_html(js_key, markers, boxes=(), 높이=520):
    """지도 HTML 글자를 돌려준다.

    markers: [{이름, 위도, 경도, 줄들?}, ...]  (200개까지)
    boxes:   [{이름, 남서_위도, 남서_경도, 북동_위도, 북동_경도, 색, 투명도, 선두께, 줄들}, ...]
    """
    if not js_key:
        raise RuntimeError("⛔ .env 에 KAKAO_JS_KEY 가 없어요")
    return (
        HTML_틀.replace("__SDK__", API["지도SDK"])
        .replace("__JS키__", js_key)
        .replace("__높이__", str(int(높이)))
        .replace("__박스__", _스크립트용(list(boxes)))
        .replace("__마커__", _스크립트용(list(markers)[:최대_마커]))  # 데이터는 맨 마지막에 넣어 이름 속 __글자__ 가 안 바뀌게
    )
