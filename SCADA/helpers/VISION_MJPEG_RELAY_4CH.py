# -*- coding: utf-8 -*-

import threading
import time
import urllib.request

from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer


# =========================================================
# 기본 설정
# =========================================================

VISION_HOST = "127.0.0.1"
VISION_PORT = 5000

LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 5051


CHANNELS = {
    1: f"http://{VISION_HOST}:{VISION_PORT}/vision1",
    2: f"http://{VISION_HOST}:{VISION_PORT}/vision2",
    3: f"http://{VISION_HOST}:{VISION_PORT}/vision3",
    4: f"http://{VISION_HOST}:{VISION_PORT}/vision4",
}


# =========================================================
# 각 Vision 상태 저장
# =========================================================

_lock = threading.Lock()


_state = {

    1: {
        "jpg": None,
        "time": 0.0,
        "count": 0,
        "error": "",
        "connected": False,
    },

    2: {
        "jpg": None,
        "time": 0.0,
        "count": 0,
        "error": "",
        "connected": False,
    },

    3: {
        "jpg": None,
        "time": 0.0,
        "count": 0,
        "error": "",
        "connected": False,
    },

    4: {
        "jpg": None,
        "time": 0.0,
        "count": 0,
        "error": "",
        "connected": False,
    },

}


# =========================================================
# 최신 JPEG 저장
# =========================================================

def set_frame(channel, jpg):

    with _lock:

        _state[channel]["jpg"] = jpg
        _state[channel]["time"] = time.time()
        _state[channel]["count"] += 1
        _state[channel]["error"] = ""
        _state[channel]["connected"] = True


def set_error(channel, message):

    with _lock:

        _state[channel]["error"] = message
        _state[channel]["connected"] = False


# =========================================================
# Vision PC의 MJPEG 스트림 수신
# =========================================================

def read_mjpeg_forever(channel):

    source_url = CHANNELS[channel]


    while True:

        try:

            req = urllib.request.Request(

                source_url,

                headers={

                    "User-Agent":
                        "CIMON-MJPEG-Relay-4CH/1.0",

                    "Cache-Control":
                        "no-cache",

                    "Pragma":
                        "no-cache",

                },

            )


            with urllib.request.urlopen(
                req,
                timeout=10
            ) as response:


                ctype = response.headers.get(
                    "Content-Type",
                    ""
                )


                if (
                    "multipart/x-mixed-replace"
                    not in ctype.lower()
                ):

                    raise RuntimeError(
                        f"Unexpected Content-Type: {ctype!r}"
                    )


                with _lock:

                    _state[channel]["connected"] = True
                    _state[channel]["error"] = ""


                buf = bytearray()


                while True:


                    chunk = response.read(8192)


                    if not chunk:

                        raise ConnectionError(
                            "Vision stream ended"
                        )


                    buf.extend(chunk)


                    while True:


                        # JPEG 시작
                        start = buf.find(
                            b"\xff\xd8"
                        )


                        if start < 0:

                            if len(buf) > 4_000_000:

                                del buf[:-4096]

                            break


                        # JPEG 끝
                        end = buf.find(
                            b"\xff\xd9",
                            start + 2
                        )


                        if end < 0:

                            if start > 0:

                                del buf[:start]

                            break


                        end += 2


                        jpg = bytes(
                            buf[start:end]
                        )


                        del buf[:end]


                        set_frame(
                            channel,
                            jpg
                        )


        except Exception as e:


            set_error(

                channel,

                f"{type(e).__name__}: {e}"

            )


            # 끊기면 1초 후 재접속
            time.sleep(1.0)


# =========================================================
# 개별 Vision 실시간 페이지
#
# 기존 1채널에서 잘 됐던 방식과 동일
# 페이지 전체 새로고침 X
# IMG만 계속 갱신
# =========================================================

def make_live_html(channel):

    return f"""<!DOCTYPE html>

<html>

<head>

<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta charset="utf-8">

<title>VISION{channel}</title>


<style>

html,
body {{

    margin: 0;
    padding: 0;

    width: 100%;
    height: 100%;

    overflow: hidden;

    background: #000;

}}


#cam {{

    position: absolute;

    left: 50%;
    top: 50%;

    max-width: 100%;
    max-height: 100%;

    width: auto;
    height: 100%;

    border: 0;

    transform: translate(-50%, -50%);

}}

</style>


</head>


<body>


<img
    id="cam"
    alt=""
>


<script type="text/javascript">

(function () {{

    var cam =
        document.getElementById("cam");


    function refresh() {{

        cam.src =
            "/vision{channel}.jpg?t="
            + new Date().getTime();

    }}


    cam.onload = function () {{

        /*
        약 10 FPS
        페이지 전체는 새로고침하지 않음
        */

        window.setTimeout(
            refresh,
            100
        );

    }};


    cam.onerror = function () {{

        window.setTimeout(
            refresh,
            500
        );

    }};


    refresh();


}})();

</script>


</body>

</html>
"""


# =========================================================
# Vision 4개 동시보기 페이지
# =========================================================

def make_all_html():

    return """<!DOCTYPE html>

<html>

<head>

<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta charset="utf-8">

<title>VISION ALL</title>


<style>

html,
body {

    margin: 0;
    padding: 0;

    width: 100%;
    height: 100%;

    overflow: hidden;

    background: #111;

}


.cell {

    position: relative;

    float: left;

    width: 50%;
    height: 50%;

    overflow: hidden;

    background: #000;

    box-sizing: border-box;

    border: 1px solid #555;

}


.cam {

    position: absolute;

    left: 50%;
    top: 50%;

    max-width: 100%;
    max-height: 100%;

    width: auto;
    height: auto;

    border: 0;

    transform: translate(-50%, -50%);

}


.title {

    position: absolute;

    left: 8px;
    top: 6px;

    z-index: 10;

    padding: 4px 8px;

    background: rgba(0, 0, 0, 0.7);

    color: #fff;

    font-family: Arial;
    font-size: 16px;

}

</style>


</head>


<body>


<div class="cell">

    <div class="title">
        VISION1
    </div>

    <img
        id="cam1"
        class="cam"
        alt=""
    >

</div>


<div class="cell">

    <div class="title">
        VISION2
    </div>

    <img
        id="cam2"
        class="cam"
        alt=""
    >

</div>


<div class="cell">

    <div class="title">
        VISION3
    </div>

    <img
        id="cam3"
        class="cam"
        alt=""
    >

</div>


<div class="cell">

    <div class="title">
        VISION4
    </div>

    <img
        id="cam4"
        class="cam"
        alt=""
    >

</div>


<script type="text/javascript">


function startCamera(number) {


    var cam =
        document.getElementById(
            "cam" + number
        );


    function refresh() {


        cam.src =
            "/vision"
            + number
            + ".jpg?t="
            + new Date().getTime();

    }


    cam.onload = function () {


        /*
        통합화면은 약 6~7 FPS
        */

        window.setTimeout(
            refresh,
            150
        );

    };


    cam.onerror = function () {


        window.setTimeout(
            refresh,
            500
        );

    };


    refresh();

}


startCamera(1);
startCamera(2);
startCamera(3);
startCamera(4);


</script>


</body>

</html>
"""


# =========================================================
# 기본 메뉴 페이지
# =========================================================

INDEX_HTML = """<!DOCTYPE html>

<html>

<head>

<meta charset="utf-8">

<title>
VISION MJPEG RELAY 4CH
</title>

</head>


<body>

<h2>
VISION MJPEG RELAY 4CH
</h2>


<a href="/vision1.html">
VISION1 LIVE
</a>

<br><br>


<a href="/vision2.html">
VISION2 LIVE
</a>

<br><br>


<a href="/vision3.html">
VISION3 LIVE
</a>

<br><br>


<a href="/vision4.html">
VISION4 LIVE
</a>

<br><br>


<a href="/vision_all.html">
VISION ALL - 4 SCREEN
</a>

<br><br>


<a href="/status">
STATUS
</a>


</body>

</html>
"""


# =========================================================
# 로컬 HTTP 서버
# =========================================================

class Handler(BaseHTTPRequestHandler):


    def do_GET(self):


        path = (
            self.path
            .split("?", 1)[0]
            .lower()
        )


        # =================================================
        # 기본 메뉴
        # =================================================

        if path == "/":


            body = INDEX_HTML.encode(
                "utf-8"
            )


            self.send_response(
                200
            )


            self.send_header(

                "Content-Type",

                "text/html; charset=utf-8"

            )


            self.send_header(

                "Cache-Control",

                "no-store, no-cache, "
                "must-revalidate, max-age=0"

            )


            self.send_header(

                "Content-Length",

                str(len(body))

            )


            self.end_headers()


            self.wfile.write(
                body
            )


            return


        # =================================================
        # 4개 통합 화면
        # =================================================

        if path == "/vision_all.html":


            body = (
                make_all_html()
                .encode("utf-8")
            )


            self.send_response(
                200
            )


            self.send_header(

                "Content-Type",

                "text/html; charset=utf-8"

            )


            self.send_header(

                "Cache-Control",

                "no-store, no-cache, "
                "must-revalidate, max-age=0"

            )


            self.send_header(

                "Pragma",

                "no-cache"

            )


            self.send_header(

                "Expires",

                "0"

            )


            self.send_header(

                "Content-Length",

                str(len(body))

            )


            self.end_headers()


            self.wfile.write(
                body
            )


            return


        # =================================================
        # VISION1~4 개별 실시간 화면
        # =================================================

        if (
            path.startswith("/vision")
            and path.endswith(".html")
        ):


            num = path[
                len("/vision"):
                -len(".html")
            ]


            if (
                num.isdigit()
                and int(num) in CHANNELS
            ):


                channel = int(num)


                body = (
                    make_live_html(
                        channel
                    )
                    .encode("utf-8")
                )


                self.send_response(
                    200
                )


                self.send_header(

                    "Content-Type",

                    "text/html; charset=utf-8"

                )


                self.send_header(

                    "Cache-Control",

                    "no-store, no-cache, "
                    "must-revalidate, max-age=0"

                )


                self.send_header(

                    "Pragma",

                    "no-cache"

                )


                self.send_header(

                    "Expires",

                    "0"

                )


                self.send_header(

                    "Content-Length",

                    str(len(body))

                )


                self.end_headers()


                self.wfile.write(
                    body
                )


                return


        # =================================================
        # VISION1~4 최신 JPEG
        # =================================================

        if (
            path.startswith("/vision")
            and path.endswith(".jpg")
        ):


            num = path[
                len("/vision"):
                -len(".jpg")
            ]


            if (
                num.isdigit()
                and int(num) in CHANNELS
            ):


                channel = int(num)


                with _lock:

                    jpg = (
                        _state[channel]["jpg"]
                    )


                if not jpg:


                    body = (

                        f"VISION{channel}: "
                        f"No frame received yet"

                    ).encode(
                        "utf-8"
                    )


                    self.send_response(
                        503
                    )


                    self.send_header(

                        "Content-Type",

                        "text/plain; charset=utf-8"

                    )


                    self.send_header(

                        "Content-Length",

                        str(len(body))

                    )


                    self.send_header(

                        "Cache-Control",

                        "no-store"

                    )


                    self.end_headers()


                    self.wfile.write(
                        body
                    )


                    return


                self.send_response(
                    200
                )


                self.send_header(

                    "Content-Type",

                    "image/jpeg"

                )


                self.send_header(

                    "Content-Length",

                    str(len(jpg))

                )


                self.send_header(

                    "Cache-Control",

                    "no-store, no-cache, "
                    "must-revalidate, max-age=0"

                )


                self.send_header(

                    "Pragma",

                    "no-cache"

                )


                self.send_header(

                    "Expires",

                    "0"

                )


                self.end_headers()


                self.wfile.write(
                    jpg
                )


                return


        # =================================================
        # 상태 확인
        # =================================================

        if path == "/status":


            now = time.time()


            lines = []


            with _lock:


                for channel in sorted(
                    CHANNELS
                ):


                    s = _state[
                        channel
                    ]


                    if s["time"]:

                        age = (
                            now
                            - s["time"]
                        )

                    else:

                        age = -1


                    lines.extend([

                        f"[VISION{channel}]",

                        f"source="
                        f"{CHANNELS[channel]}",

                        f"connected="
                        f"{s['connected']}",

                        f"frame_received="
                        f"{s['jpg'] is not None}",

                        f"frame_count="
                        f"{s['count']}",

                        f"frame_age_sec="
                        f"{age:.3f}",

                        f"last_error="
                        f"{s['error']}",

                        "",

                    ])


            body = (

                "\n"
                .join(lines)
                .encode("utf-8")

            )


            self.send_response(
                200
            )


            self.send_header(

                "Content-Type",

                "text/plain; charset=utf-8"

            )


            self.send_header(

                "Cache-Control",

                "no-store"

            )


            self.send_header(

                "Content-Length",

                str(len(body))

            )


            self.end_headers()


            self.wfile.write(
                body
            )


            return


        # =================================================
        # 없는 주소
        # =================================================

        self.send_response(
            404
        )


        self.end_headers()


    def log_message(
        self,
        fmt,
        *args
    ):

        pass


# =========================================================
# 프로그램 시작
# =========================================================

def main():


    print(
        "VISION MJPEG relay 4CH"
    )


    print()


    for channel in sorted(
        CHANNELS
    ):


        print(

            f"VISION{channel} : "
            f"{CHANNELS[channel]}"

        )


    print()


    print(

        f"MAIN : "
        f"http://{LOCAL_HOST}:"
        f"{LOCAL_PORT}/"

    )


    print(

        f"ALL  : "
        f"http://{LOCAL_HOST}:"
        f"{LOCAL_PORT}/vision_all.html"

    )


    print(

        f"STATUS : "
        f"http://{LOCAL_HOST}:"
        f"{LOCAL_PORT}/status"

    )


    print()


    # VISION1~4 각각 독립적으로 수신

    for channel in CHANNELS:


        threading.Thread(

            target=read_mjpeg_forever,

            args=(
                channel,
            ),

            daemon=True,

            name=(
                f"VISION{channel}"
            ),

        ).start()


    # 로컬 웹서버 시작

    server = ThreadingHTTPServer(

        (
            LOCAL_HOST,
            LOCAL_PORT
        ),

        Handler

    )


    try:


        server.serve_forever()


    except KeyboardInterrupt:


        pass


    finally:


        server.server_close()


if __name__ == "__main__":

    main()