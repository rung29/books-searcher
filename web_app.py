import base64
import secrets
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup
from flask import Flask, abort, redirect, render_template, request, url_for

from crawler import fetch_books_by_page, perform_search
from integrate import search_library_status


app = Flask(__name__)

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
SEARCH_TTL_SECONDS = 30 * 60
SEARCH_SESSIONS = {}

requests.packages.urllib3.disable_warnings(
    requests.packages.urllib3.exceptions.InsecureRequestWarning
)


def cleanup_sessions():
    now = time.time()
    expired_tokens = [
        token
        for token, data in SEARCH_SESSIONS.items()
        if now - data.get("created_at", now) > SEARCH_TTL_SECONDS
    ]
    for token in expired_tokens:
        SEARCH_SESSIONS.pop(token, None)


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


def build_context(data):
    return {
        "readrang": data["readrang"],
        "readrang_name": option_label(READRANG_OPTIONS, data["readrang"]),
        "testing": data["testing"],
        "testing_name": option_label(TESTING_OPTIONS, data["testing"]),
        "keywords": data["keywords"],
        "include_library": data["include_library"],
    }


def enrich_with_library_status(books):
    enriched_books = []
    for book in books:
        enriched_book = dict(book)
        enriched_book["library"] = search_library_status(book.get("title", ""))
        enriched_books.append(enriched_book)
    return enriched_books


@app.get("/")
def index():
    cleanup_sessions()
    return render_template(
        "index.html",
        readrang_options=READRANG_OPTIONS,
        testing_options=TESTING_OPTIONS,
        form={
            "readrang": "all",
            "testing": "all",
            "keywords": "",
            "include_library": False,
        },
    )


@app.post("/captcha")
def captcha():
    cleanup_sessions()
    readrang = request.form.get("readrang", "all")
    testing = request.form.get("testing", "all")
    keywords = request.form.get("keywords", "").strip()
    include_library = request.form.get("include_library") == "1"

    try:
        session, csrf_token, captcha_src = create_search_session()
    except Exception as exc:
        return render_template("error.html", message=str(exc)), 502

    token = secrets.token_urlsafe(24)
    SEARCH_SESSIONS[token] = {
        "created_at": time.time(),
        "session": session,
        "csrf_token": csrf_token,
        "readrang": readrang,
        "testing": testing,
        "keywords": keywords,
        "include_library": include_library,
        "searched": False,
    }

    return render_template(
        "captcha.html",
        token=token,
        captcha_src=captcha_src,
        context=build_context(SEARCH_SESSIONS[token]),
    )


@app.post("/search/<token>")
def search(token):
    data = SEARCH_SESSIONS.get(token)
    if not data:
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

    success = perform_search(
        data["session"],
        data["csrf_token"],
        data["readrang"],
        data["testing"],
        data["keywords"],
        captcha_code,
    )
    if not success:
        SEARCH_SESSIONS.pop(token, None)
        return render_template(
            "error.html",
            message="搜尋失敗，可能是驗證碼錯誤或來源網站暫時拒絕請求。請重新查詢。",
        ), 400

    data["searched"] = True
    return redirect(url_for("results", token=token, page=1))


@app.get("/results/<token>")
def results(token):
    data = SEARCH_SESSIONS.get(token)
    if not data or not data.get("searched"):
        abort(404)

    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1

    books = fetch_books_by_page(data["session"], page) or []
    if data["include_library"] and books:
        books = enrich_with_library_status(books)

    return render_template(
        "results.html",
        token=token,
        page=page,
        books=books,
        context=build_context(data),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
