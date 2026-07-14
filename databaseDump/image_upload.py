"""
image_upload.py
多线程批量上传图标 — 从 image_inventory.db 读取 icon_state=待更新 的任务，
上传完成后实时标记为已上传。

用法:
    python image_upload.py                   # 全部（8 线程）
    python image_upload.py --debug           # 前 20 张
    python image_upload.py -t 16             # 16 线程
"""

import os
import sys
import io
import json
import copy
import sqlite3
import signal
import threading
import urllib.request
import urllib.parse
import http.cookiejar

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)


def _load_dotenv():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search = [script_dir]
    parent = os.path.dirname(script_dir)
    search.append(parent)
    search.append(os.path.dirname(parent))
    for d in search:
        env_path = os.path.join(d, ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line or _line.startswith("#"):
                        continue
                    _k, _sep, _v = _line.partition("=")
                    _k, _v = _k.strip(), _v.strip()
                    if _k and _v:
                        os.environ.setdefault(_k, _v)
            return


_load_dotenv()

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMPS_DIR = os.environ.get("DUMPS_DIR", "")

BASE = os.environ.get("BASE_URL", "https://gtnh.huijiwiki.com/api.php")
AUTHKEY = os.environ.get("AUTHKEY", "")
ICONS_DIR = os.path.join(DUMPS_DIR, "icons")
INVENTORY_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image_inventory.db")
USERNAME = os.environ.get("BOT_USERNAME", "")
PASSWORD = os.environ.get("BOT_PASSWORD", "")

_stop = threading.Event()


# ── Task DB ────────────────────────────────────────────────────

def get_pending_icons() -> list[str]:
    """读取 icon_state=待更新 的图标（去重）。"""
    with sqlite3.connect(INVENTORY_DB) as conn:
        rows = conn.execute(
            "SELECT DISTINCT icon_name FROM images "
            "WHERE icon_state='待更新' AND icon_name != '' "
            "ORDER BY icon_name"
        ).fetchall()
    return [r[0] for r in rows]


def mark_icon_done(icon_name: str):
    """图标上传成功 → 该图标所有行的 icon_state = 已上传"""
    with sqlite3.connect(INVENTORY_DB) as conn:
        conn.execute(
            "UPDATE images SET icon_state='已上传' WHERE icon_name=?",
            (icon_name,)
        )
        conn.commit()


# ── HTTP ─────────────────────────────────────────────────────

def _make_opener(jar=None):
    if jar is None:
        jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _get(opener, params: dict) -> dict:
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-authkey": AUTHKEY, "User-Agent": "WikiBot/1.0"})
    with opener.open(req) as resp:
        return json.loads(resp.read())


def _post_form(opener, params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(BASE, data=data,
        headers={"X-authkey": AUTHKEY, "User-Agent": "WikiBot/1.0",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with opener.open(req) as resp:
        return json.loads(resp.read())


def login_once(username: str, password: str) -> http.cookiejar.CookieJar:
    jar = http.cookiejar.CookieJar()
    opener = _make_opener(jar)
    r = _get(opener, {"action": "query", "meta": "tokens", "type": "login", "format": "json"})
    lt = r["query"]["tokens"]["logintoken"]
    r = _post_form(opener, {
        "action": "login", "format": "json",
        "lgname": username, "lgpassword": password, "lgtoken": lt,
    })
    if r["login"]["result"] != "Success":
        raise RuntimeError(f"Login failed: {r['login'].get('reason', r['login']['result'])}")
    print(f"Logged in as: {r['login']['lgusername']}")
    return jar


def clone_jar(src: http.cookiejar.CookieJar) -> http.cookiejar.CookieJar:
    dst = http.cookiejar.CookieJar()
    dst._cookies = copy.deepcopy(src._cookies)
    return dst


def make_opener_with_jar(jar):
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def upload_one(opener, path: str, bot: bool = True) -> dict:
    """上传一张图片（multipart，per-thread CSRF 独立管理）。"""
    filename = os.path.basename(path)
    with open(path, "rb") as f:
        file_data = f.read()

    for attempt in range(2):
        r_csrf = _get(opener, {"action": "query", "meta": "tokens", "format": "json"})
        csrf = r_csrf["query"]["tokens"]["csrftoken"]

        boundary = "----WikiBotBoundary"
        body = io.BytesIO()

        def add_field(k, v):
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
            body.write(f"{v}\r\n".encode())

        add_field("action", "upload")
        add_field("format", "json")
        add_field("token", csrf)
        add_field("filename", filename)
        add_field("ignorewarnings", "1")
        if bot:
            add_field("bot", "1")

        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode())
        body.write(b"Content-Type: application/octet-stream\r\n\r\n")
        body.write(file_data)
        body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())

        req = urllib.request.Request(BASE, data=body.getvalue(),
            headers={"X-authkey": AUTHKEY, "User-Agent": "WikiBot/1.0",
                     "Content-Type": f"multipart/form-data; boundary={boundary}"})
        with opener.open(req) as resp:
            r = json.loads(resp.read())

        if "error" in r:
            err = r["error"]["info"]
            if "Invalid CSRF" in err and attempt == 0:
                continue
            raise RuntimeError(err)
        return r["upload"]

    raise RuntimeError("CSRF retry exhausted")


# ── Worker ───────────────────────────────────────────────────

def worker(icon_list: list, total: int, counters: dict, lock: threading.Lock,
           master_jar):
    """线程：独立 jar + 独立 CSRF，上传自己分到的图标。"""
    opener = make_opener_with_jar(clone_jar(master_jar))

    for icon_name in icon_list:
        if _stop.is_set():
            return
        path = os.path.join(ICONS_DIR, icon_name)

        if not os.path.exists(path):
            with lock:
                counters["SKIP"] = counters.get("SKIP", 0) + 1
                counters["done"] += 1
            print(f"[{counters['done']}/{total}] SKIP {icon_name} (file not found)")
            continue

        try:
            result = upload_one(opener, path, bot=True)
            if result.get("result") == "Success":
                status, msg = "OK", ""
            else:
                status, msg = "SKIP", result.get("result", "?")
            mark_icon_done(icon_name)  # wiki 已接受 = 已上传
        except Exception as e:
            err = str(e)
            if any(kw in err.lower() for kw in ("fileexists", "duplicate", "已存在")):
                status, msg = "SKIP", ""
                mark_icon_done(icon_name)
            else:
                status, msg = "FAIL", err

        with lock:
            counters[status] = counters.get(status, 0) + 1
            counters["done"] += 1
            extra = f" - {msg}" if msg else ""
            print(f"[{counters['done']}/{total}] {status} {icon_name}{extra}")


# ── Main ─────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    debug = "--debug" in args
    threads = 8

    for i, a in enumerate(args):
        if a == "-t" and i + 1 < len(args):
            threads = int(args[i + 1])

    tasks = get_pending_icons()
    if not tasks:
        print("No pending icons. Run image_inventory.py first.")
        return

    if debug:
        tasks = tasks[:20]
        threads = 2
        print(f"[DEBUG] Only {len(tasks)} icons")

    total = len(tasks)
    print(f"{total:,} pending icons, {threads} threads")

    master_jar = login_once(USERNAME, PASSWORD)

    def on_interrupt(_sig, _frame):
        print("\n\nInterrupted! Stopping...")
        _stop.set()
    signal.signal(signal.SIGINT, on_interrupt)

    chunks = [[] for _ in range(threads)]
    for i, t in enumerate(tasks):
        chunks[i % threads].append(t)

    counters = {"OK": 0, "SKIP": 0, "FAIL": 0, "done": 0}
    lock = threading.Lock()
    workers = []
    for tid in range(threads):
        if not chunks[tid]:
            continue
        t = threading.Thread(
            target=worker,
            args=(chunks[tid], total, counters, lock, master_jar),
            daemon=True,
        )
        t.start()
        workers.append(t)
        print(f"  Thread {tid}: {len(chunks[tid])} icons")

    print(f"\nUploading {total} icons... (Ctrl+C to stop)\n")

    try:
        for t in workers:
            while t.is_alive():
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\n\nInterrupted! Stopping...")
        _stop.set()
        for t in workers:
            t.join(timeout=2)

    with sqlite3.connect(INVENTORY_DB) as conn:
        remaining = conn.execute(
            "SELECT COUNT(DISTINCT icon_name) FROM images WHERE icon_state='待更新'"
        ).fetchone()[0]
    print(f"Done! OK: {counters['OK']}, SKIP: {counters['SKIP']}, FAIL: {counters['FAIL']}")
    if remaining:
        print(f"{remaining} icons still pending (re-run to retry failures)")


if __name__ == "__main__":
    main()
