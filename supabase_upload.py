import json
import os
import sys
import urllib.parse
from pathlib import Path

import requests


DATA_FILES = (
    "books_with_library_data.json",
    "ebooks_data.json",
)
DEFAULT_BUCKET = "book-data"
REQUEST_TIMEOUT = 30


def load_env_file(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and value:
            os.environ.setdefault(name, value)


def _required_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少環境變數 {name}")
    return value


def _storage_headers(secret_key):
    headers = {
        "apikey": secret_key,
        "Content-Type": "application/json; charset=utf-8",
        "cache-control": "max-age=60",
        "x-upsert": "true",
    }
    # Legacy service_role keys are JWTs and still require the Bearer header.
    if secret_key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {secret_key}"
    return headers


def _object_path(prefix, filename):
    parts = [part.strip("/") for part in (prefix, filename) if part.strip("/")]
    return "/".join(parts)


def _validate_json_file(path):
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise RuntimeError(f"{path.name} 的 JSON 根節點必須是陣列")
    return len(data)


def upload_file(supabase_url, secret_key, bucket, object_path, local_path):
    encoded_bucket = urllib.parse.quote(bucket, safe="")
    encoded_path = urllib.parse.quote(object_path, safe="/")
    upload_url = (
        f"{supabase_url.rstrip('/')}/storage/v1/object/"
        f"{encoded_bucket}/{encoded_path}"
    )
    response = requests.post(
        upload_url,
        headers=_storage_headers(secret_key),
        data=local_path.read_bytes(),
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        detail = response.text.strip()[:500]
        raise RuntimeError(
            f"上傳 {local_path.name} 失敗：HTTP {response.status_code} {detail}"
        )


def write_public_config(script_dir, supabase_url, bucket, prefix):
    public_parts = [
        supabase_url.rstrip("/"),
        "storage/v1/object/public",
        urllib.parse.quote(bucket, safe=""),
    ]
    if prefix.strip("/"):
        public_parts.append(urllib.parse.quote(prefix.strip("/"), safe="/"))
    public_base_url = "/".join(public_parts)
    config = (
        "// Public Supabase Storage URL only. Never put a secret key in this file.\n"
        "window.BOOK_SEARCHER_DATA_CONFIG = {\n"
        f"  supabasePublicBaseUrl: {json.dumps(public_base_url)},\n"
        "  timeoutMs: 6000,\n"
        "  noticeDurationMs: 3000,\n"
        "};\n"
    )
    (script_dir / "data_source_config.js").write_text(config, encoding="utf-8")
    return public_base_url


def main():
    script_dir = Path(__file__).resolve().parent
    try:
        load_env_file(script_dir / ".env.local")
        supabase_url = _required_env("SUPABASE_URL")
        secret_key = _required_env("SUPABASE_SECRET_KEY")
        bucket = os.getenv("SUPABASE_BUCKET", DEFAULT_BUCKET).strip() or DEFAULT_BUCKET
        prefix = os.getenv("SUPABASE_DATA_PREFIX", "").strip("/")

        for filename in DATA_FILES:
            local_path = script_dir / filename
            if not local_path.exists():
                raise RuntimeError(f"找不到 {local_path}")
            count = _validate_json_file(local_path)
            remote_path = _object_path(prefix, filename)
            upload_file(
                supabase_url,
                secret_key,
                bucket,
                remote_path,
                local_path,
            )
            print(f"已上傳 {filename}（{count} 筆）至 {bucket}/{remote_path}")

        public_base_url = write_public_config(
            script_dir,
            supabase_url,
            bucket,
            prefix,
        )
        print(f"Supabase 公開資料路徑：{public_base_url}")
        print("已更新 data_source_config.js；首次設定後需一併部署此檔案。")
    except (OSError, ValueError, requests.RequestException, RuntimeError) as exc:
        print(f"Supabase 上傳失敗：{exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
