"""
image_redirect.py
多线程批量创建图片重定向 — 从 image_inventory.db 读取 redirect_state=待更新 的任务，
完成后实时标记为已上传。

用法:
    python image_redirect.py                   # 全部（8 线程）
    python image_redirect.py --debug           # 前 20 条
    python image_redirect.py -t 16             # 16 线程
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

BASE = os.environ.get("BASE_URL", "https://gtnh.huijiwiki.com/api.php")
AUTHKEY = os.environ.get("AUTHKEY", "")
INVENTORY_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image_inventory.db")
USERNAME = os.environ.get("BOT_USERNAME", "")
PASSWORD = os.environ.get("BOT_PASSWORD", "")

_stop = threading.Event()

# MediaWiki 标题非法字符 → "-"
_INVALID_TITLE_CHARS = str.maketrans("[]{}|<>", "-------")


def sanitize_title(title: str) -> str:
    prefix, sep, rest = title.partition(":")
    if sep:
        return prefix + ":" + rest.translate(_INVALID_TITLE_CHARS)
    return title.translate(_INVALID_TITLE_CHARS)


# ── Task DB ────────────────────────────────────────────────────

def get_pending_redirects() -> list[tuple[str, str]]:
    """读取 redirect_state=待更新 的任务 → [(redirect_from, icon_name), ...]"""
    with sqlite3.connect(INVENTORY_DB) as conn:
        rows = conn.execute(
            "SELECT redirect_from, icon_name FROM images "
            "WHERE redirect_state='待更新' AND redirect_from != '' "
            "ORDER BY redirect_from"
        ).fetchall()
    return rows


def mark_redirect_done(redirect_from: str, icon_name: str):
    """重定向创建成功 → redirect_state = 已上传"""
    with sqlite3.connect(INVENTORY_DB) as conn:
        conn.execute(
            "UPDATE images SET redirect_state='已上传' WHERE redirect_from=? AND icon_name=?",
            (redirect_from, icon_name)
        )
        conn.commit()


# ── HTTP ─────────────────────────────────────────────────────

def _make_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )


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
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
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


def fetch_csrf(opener) -> str:
    r = _get(opener, {"action": "query", "meta": "tokens", "format": "json"})
    return r["query"]["tokens"]["csrftoken"]


def create_redirect(opener, from_title: str, to_title: str,
                    csrf: str, summary: str = "", bot: bool = True):
    """返回 None 表示 CSRF 过期需刷新。"""
    text = f"#REDIRECT [[{to_title}]]"
    params = {
        "action": "edit",
        "format": "json",
        "title": from_title,
        "text": text,
        "summary": summary,
        "token": csrf,
    }
    if bot:
        params["bot"] = "1"
    r = _post_form(opener, params)
    if "error" in r:
        err = r["error"]["info"]
        if "Invalid CSRF" in err or "badtoken" in err.lower():
            return None
        raise RuntimeError(err)
    return r["edit"]


# ── Worker ───────────────────────────────────────────────────

def worker(task_list: list, total: int, counters: dict, lock: threading.Lock,
           master_jar):
    """线程：独立 jar + 独立 CSRF，完成一条立刻写 DB。"""
    opener = make_opener_with_jar(clone_jar(master_jar))
    csrf = fetch_csrf(opener)

    for (from_title, icon_name) in task_list:
        if _stop.is_set():
            return
        to_title = f"File:{icon_name}"
        try:
            result = create_redirect(
                opener, from_title, to_title, csrf,
                summary="机器人：批量创建图片重定向",
                bot=True,
            )
            if result is None:
                csrf = fetch_csrf(opener)
                result = create_redirect(
                    opener, from_title, to_title, csrf,
                    summary="机器人：批量创建图片重定向",
                    bot=True,
                )
            if result is None:
                status, msg = "FAIL", "CSRF retry exhausted"
            else:
                mark_redirect_done(from_title, icon_name)
                status = "OK" if "nochange" not in result else "SKIP"
                msg = ""
        except Exception as e:
            err = str(e)
            status, msg = "FAIL", err

        with lock:
            counters[status] = counters.get(status, 0) + 1
            counters["done"] += 1
            extra = f" - {msg}" if msg else ""
            print(f"[{counters['done']}/{total}] {status} {from_title} -> {to_title}{extra}")


# ── Main ─────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    debug = "--debug" in args
    threads = 8

    for i, a in enumerate(args):
        if a == "-t" and i + 1 < len(args):
            threads = int(args[i + 1])

    tasks = get_pending_redirects()
    if not tasks:
        print("No pending redirects. Run image_inventory.py first.")
        return

    if debug:
        tasks = tasks[:20]
        threads = 2
        print(f"[DEBUG] Only {len(tasks)} tasks")

    total = len(tasks)
    print(f"{total:,} pending redirects, {threads} threads")

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
        print(f"  Thread {tid}: {len(chunks[tid])} redirects")

    print(f"\nCreating {total} redirects... (Ctrl+C to stop)\n")

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
            "SELECT COUNT(*) FROM images WHERE redirect_state='待更新'"
        ).fetchone()[0]
    print(f"Done! OK: {counters['OK']}, SKIP: {counters['SKIP']}, FAIL: {counters['FAIL']}")
    if remaining:
        print(f"{remaining} redirects still pending (re-run to retry failures)")


if __name__ == "__main__":
    main()
