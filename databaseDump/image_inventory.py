"""
image_inventory.py
对比 image_inventory.db 与 dumps/icons/ + dumps/image_redirect.csv 的差异，
更新 icon_state 和 redirect_state。

规则:
    - icon_name 唯一（每行对应 wiki 上一张图片）
    - redirect_from 非空值唯一（每个非空值对应 wiki 上一个重定向页面）
    - 若重定向目标变更，清空旧行 redirect_from，写入新行

用法:
    python image_inventory.py               # 更新状态
    python image_inventory.py --stats       # 仅查看统计
    python image_inventory.py --clean-dynamic  # 删除 icon_name 以 _dynamic.png 结尾的行
"""

import os
import sys
import sqlite3
from contextlib import closing
from image_version import icon_names, image_paths, redirect_map, require_source

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

DUMPS_DIR, DB_PATH, ICONS_DIR, NEW_CSV = image_paths(os.path.dirname(os.path.abspath(__file__)))


# ── Load current dumps state ──────────────────────────────────

def load_new_icons() -> set[str]:
    if not os.path.isdir(ICONS_DIR):
        print(f"WARNING: icons dir not found: {ICONS_DIR}")
        return set()
    icons = icon_names(ICONS_DIR)
    print(f"  Current icons: {len(icons):,}")
    return icons


def load_new_redirects() -> dict[str, str]:
    """读取 dumps/image_redirect.csv → {redirect_from: icon_name}"""
    redirects = {}
    if not os.path.isfile(NEW_CSV):
        print(f"WARNING: redirect CSV not found: {NEW_CSV}")
        return redirects
    redirects = redirect_map(NEW_CSV)
    print(f"  Current redirects: {len(redirects):,}")
    return redirects


# ── Update logic ─────────────────────────────────────────────

def update_inventory():
    """以 dumps 为准，对比 DB 当前状态，更新 icon_state 和 redirect_state。
    redirect_from 分配以新 CSV 为准，有变动就标待更新，旧值不管。"""
    require_source(DUMPS_DIR)
    init_db()
    new_icons = load_new_icons()
    new_redirects = load_new_redirects()   # {redirect_from: icon_name}

    conn = sqlite3.connect(DB_PATH)

    # ── 加载 DB 当前状态 ──
    icon_to_old_rf = {}
    for r in conn.execute("SELECT icon_name, redirect_from FROM images"):
        icon_to_old_rf[r[0]] = r[1]

    # ── 构建新分配：rf → icon（来自 new_redirects）──
    rf_to_new_icon = {}
    for rf, icon in new_redirects.items():
        rf_to_new_icon[rf] = icon

    # ── 反转：icon → rf（一个 icon 至多一个 rf）──
    icon_to_new_rf = {}
    for rf, icon in rf_to_new_icon.items():
        icon_to_new_rf[icon] = rf  # 同名 rf 不可能重复；同名 icon 只保留最后一个

    # ── 先清空所有会变更的 redirect_from（避免 UNIQUE 冲突）──
    for icon, old_rf in icon_to_old_rf.items():
        new_rf = icon_to_new_rf.get(icon, '')
        if old_rf and old_rf != new_rf:
            conn.execute("UPDATE images SET redirect_from='' WHERE icon_name=?", (icon,))

    # ── 逐行更新 ──
    icon_updated = 0
    redir_updated = 0
    new_rows = 0
    all_icons = set(icon_to_old_rf.keys()) | set(icon_to_new_rf.keys()) | new_icons

    for icon in sorted(all_icons):
        old_rf = icon_to_old_rf.get(icon, '')
        new_rf = icon_to_new_rf.get(icon, '')
        new_istate = "已上传" if icon in new_icons else "待更新"

        existing = conn.execute(
            "SELECT icon_state, redirect_state FROM images WHERE icon_name=?", (icon,)
        ).fetchone()

        if existing is None:
            # 新图标：本地有 ≠ wiki 有，一律待上传
            new_istate = "待更新"
            new_rstate = "待更新" if new_rf else "已上传"
            conn.execute("INSERT INTO images VALUES (?,?,?,?)",
                         (icon, new_rf, new_istate, new_rstate))
            new_rows += 1
        else:
            old_istate, old_rstate = existing

            # 已上传 → 永不回退；待更新 → 保持直到真正上传
            if old_istate == "已上传":
                new_istate = "已上传"
            else:
                new_istate = "待更新"

            # redirect_state
            if new_rf != old_rf:
                new_rstate = "待更新"
            elif old_rstate == "待更新":
                new_rstate = "待更新"  # 上次标记了但还没真正上传，保持
            else:
                new_rstate = "已上传"

            if new_istate != old_istate:
                conn.execute("UPDATE images SET icon_state=? WHERE icon_name=?",
                             (new_istate, icon)); icon_updated += 1
            if new_rstate != old_rstate:
                conn.execute("UPDATE images SET redirect_state=? WHERE icon_name=?",
                             (new_rstate, icon)); redir_updated += 1
            if new_rf != old_rf:
                conn.execute("UPDATE images SET redirect_from=? WHERE icon_name=?",
                             (new_rf, icon))

    conn.commit()

    stats = {}
    for state in ("已上传", "待更新"):
        for col in ("icon_state", "redirect_state"):
            n = conn.execute(
                f"SELECT COUNT(*) FROM images WHERE {col}=?", (state,)
            ).fetchone()[0]
            stats[f"{col}={state}"] = n
    conn.close()

    print(f"\nUpdate complete:")
    print(f"  icon_state changes:        {icon_updated}")
    print(f"  redirect_state changes:    {redir_updated}")
    print(f"  new rows:                  {new_rows}")
    print(f"\n  icon_state 已上传:  {stats.get('icon_state=已上传', 0):,}")
    print(f"  icon_state 待更新:  {stats.get('icon_state=待更新', 0):,}")
    print(f"  redirect_state 已上传: {stats.get('redirect_state=已上传', 0):,}")
    print(f"  redirect_state 待更新: {stats.get('redirect_state=待更新', 0):,}")


def clean_dynamic():
    """删除 icon_name 以 _dynamic.png 结尾的行。"""
    conn = sqlite3.connect(DB_PATH)
    n = conn.execute(
        "SELECT COUNT(*) FROM images WHERE icon_name LIKE '%\\_dynamic.png' ESCAPE '\\'"
    ).fetchone()[0]
    if n == 0:
        print(f"No rows with _dynamic.png suffix found.")
        conn.close()
        return
    print(f"Found {n:,} rows with icon_name ending in _dynamic.png. Deleting...")
    conn.execute(
        "DELETE FROM images WHERE icon_name LIKE '%\\_dynamic.png' ESCAPE '\\'"
    )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    conn.close()
    print(f"Deleted {n:,} rows. Remaining: {total:,}")


# ── Main ─────────────────────────────────────────────────────

def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS images (
            icon_name TEXT PRIMARY KEY,
            redirect_from TEXT DEFAULT '',
            icon_state TEXT DEFAULT '待更新',
            redirect_state TEXT DEFAULT '待更新'
        )""")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_redirect ON images(redirect_from) WHERE redirect_from != ''")
        conn.commit()


def main():
    require_source(DUMPS_DIR)
    init_db()
    if "--stats" in sys.argv:
        conn = sqlite3.connect(DB_PATH)
        total = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        print(f"Total rows: {total:,}")
        for col in ("icon_state", "redirect_state"):
            for state in ("已上传", "待更新"):
                n = conn.execute(
                    f"SELECT COUNT(*) FROM images WHERE {col}=?", (state,)
                ).fetchone()[0]
                print(f"  {col} = {state}: {n:,}")
        conn.close()
        return

    if "--clean-dynamic" in sys.argv:
        clean_dynamic()
        return

    update_inventory()
    print(f"\nDB: {DB_PATH}")


if __name__ == "__main__":
    main()
