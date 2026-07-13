import base64
import os
import urllib.parse

os.environ.setdefault("INTEGRATE_PAGE_SLEEP_SECONDS", "0")
os.environ.setdefault("INTEGRATE_BOOK_SLEEP_SECONDS", "0")
os.environ.setdefault("LIB_REQUEST_TIMEOUT", "25")

import requests
from bs4 import BeautifulSoup
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from requests.utils import cookiejar_from_dict, dict_from_cookiejar

from crawler import fetch_books_by_page, perform_search
from integrate import search_library_status


app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "book-searcher-dev-secret")

BASE_URL = "https://read.chc.edu.tw/index.php"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

READRANG_OPTIONS = [
    ("all", "全部年段"),
    ("1", "國小低年級"),
    ("2", "國小中年級"),
    ("3", "國小高年級"),
    ("4", "國中"),
    ("5", "高中"),
]

TESTING_OPTIONS = [
    ("all", "全部認證"),
    ("1", "可認證"),
    ("2", "不可認證"),
]

REQUEST_TIMEOUT = 15

requests.packages.urllib3.disable_warnings(
    requests.packages.urllib3.exceptions.InsecureRequestWarning
)


def option_label(options, value):
    return dict(options).get(value, value)


def create_search_session():
    session = requests.Session()
    response = session.get(
        BASE_URL,
        params={"inter": "books", "kind": "cht"},
        headers=HEADERS,
        verify=False,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    csrf_token_input = soup.find("input", {"name": "csrf_token"})
    csrf_token = csrf_token_input["value"] if csrf_token_input else ""

    captcha_img = soup.find("img", {"id": "captcha_image"})
    if not captcha_img or not captcha_img.get("src"):
        raise RuntimeError("找不到驗證碼圖片，來源網站版面可能已變更。")

    captcha_url = urllib.parse.urljoin(BASE_URL, captcha_img["src"])
    captcha_response = session.get(
        captcha_url,
        headers=HEADERS,
        verify=False,
        timeout=REQUEST_TIMEOUT,
    )
    captcha_response.raise_for_status()

    content_type = captcha_response.headers.get("content-type", "image/png")
    captcha_base64 = base64.b64encode(captcha_response.content).decode("ascii")

    return session, csrf_token, f"data:{content_type};base64,{captcha_base64}"


def serialize_session(http_session):
    return dict_from_cookiejar(http_session.cookies)


def restore_session(cookies):
    http_session = requests.Session()
    http_session.cookies = cookiejar_from_dict(cookies or {})
    return http_session


def build_context(data):
    return {
        "readrang": data["readrang"],
        "readrang_name": option_label(READRANG_OPTIONS, data["readrang"]),
        "testing": data["testing"],
        "testing_name": option_label(TESTING_OPTIONS, data["testing"]),
        "keywords": data["keywords"],
        "include_library": data["include_library"],
        "initial_page": data.get("initial_page", 1),
    }


@app.get("/")
def index():
    search_data = session.get("search_data")
    search_token = session.get("search_token")
    active_search = search_data if search_data and search_data.get("searched") and search_token else None
    return render_template(
        "index.html",
        readrang_options=READRANG_OPTIONS,
        testing_options=TESTING_OPTIONS,
        active_search=active_search,
        search_token=search_token,
        form={
            "readrang": "all",
            "testing": "all",
            "keywords": "",
            "include_library": False,
            "initial_page": 1,
        },
    )


@app.post("/captcha")
def captcha():
    readrang = request.form.get("readrang", "all")
    testing = request.form.get("testing", "all")
    keywords = request.form.get("keywords", "").strip()
    include_library = request.form.get("include_library") == "1"
    try:
        initial_page = max(int(request.form.get("initial_page", "1")), 1)
    except ValueError:
        initial_page = 1

    try:
        http_session, csrf_token, captcha_src = create_search_session()
    except Exception as exc:
        return render_template("error.html", message=str(exc)), 502

    token = os.urandom(12).hex()
    search_data = {
        "cookies": serialize_session(http_session),
        "csrf_token": csrf_token,
        "readrang": readrang,
        "testing": testing,
        "keywords": keywords,
        "include_library": include_library,
        "initial_page": initial_page,
        "searched": False,
    }
    session["search_token"] = token
    session["search_data"] = search_data

    return render_template(
        "captcha.html",
        token=token,
        captcha_src=captcha_src,
        context=build_context(search_data),
    )


@app.post("/search/<token>")
def search(token):
    data = session.get("search_data")
    if not data or session.get("search_token") != token:
        abort(404)

    captcha_code = request.form.get("captcha_code", "").strip()
    if not captcha_code:
        return render_template(
            "captcha.html",
            token=token,
            captcha_src=None,
            context=build_context(data),
            error="請輸入驗證碼。",
        ), 400

    http_session = restore_session(data.get("cookies"))
    success = perform_search(
        http_session,
        data["csrf_token"],
        data["readrang"],
        data["testing"],
        data["keywords"],
        captcha_code,
    )
    if not success:
        session.pop("search_data", None)
        session.pop("search_token", None)
        return render_template(
            "error.html",
            message="搜尋失敗，可能是驗證碼錯誤或來源網站暫時拒絕請求。請重新查詢。",
        ), 400

    data["searched"] = True
    data["cookies"] = serialize_session(http_session)
    session["search_data"] = data
    return redirect(url_for("results", token=token, page=data.get("initial_page", 1)))


@app.get("/results/<token>")
def results(token):
    data = session.get("search_data")
    if not data or not data.get("searched") or session.get("search_token") != token:
        abort(404)

    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1

    http_session = restore_session(data.get("cookies"))
    books = fetch_books_by_page(http_session, page) or []
    data["cookies"] = serialize_session(http_session)
    session["search_data"] = data

    return render_template(
        "results.html",
        token=token,
        page=page,
        books=books,
        context=build_context(data),
    )


@app.post("/reset")
def reset():
    session.pop("search_data", None)
    session.pop("search_token", None)
    return redirect(url_for("index"))


@app.post("/api/library-status")
def library_status():
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    if not title:
        return jsonify({"has_holding": False, "items": [], "error": "缺少書名"}), 400

    result = search_library_status(title)
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
