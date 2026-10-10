"""图片流程的数据源、状态目录和当前任务范围。调用方先加载 .env。"""

import csv
import os
from pathlib import Path

from data_version import local_mod_name, source_directory, version_tag


def image_paths(script_dir: str) -> tuple[str, str, str, str]:
    _, version = version_tag(required=True).split("/")
    mod = local_mod_name(required=True)
    source = source_directory(os.environ.get("DUMPS_DIR", ""), mod, check_exists=False)
    script = Path(script_dir)
    # 保留 GTNH 已有的上传记录；独立模组不使用 GTNH 的任务清单。
    db = (script / "image_inventory.db" if mod == "GTNH" else
          script / "source_state" / mod / version / "image_inventory.db")
    return str(source), str(db), str(source / "icons"), str(source / "image_redirect.csv")


def icon_names(directory: str) -> set[str]:
    path = Path(directory)
    if not path.is_dir():
        return set()
    return {file.name for file in path.iterdir() if file.is_file()}


def redirect_map(path: str) -> dict[str, str]:
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8-sig", newline="") as file:
        return {row[0].strip(): row[1].removeprefix("File:").strip()
                for row in csv.reader(file) if len(row) >= 2 and row[0].strip()}


def require_source(path: str) -> None:
    if not os.path.isdir(path):
        raise ValueError(f"数据源目录不存在: {path}")
