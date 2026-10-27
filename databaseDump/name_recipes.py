"""从最终配方和矿辞收集每个物品名称的来源、用途处理器。"""

import json
from collections import defaultdict
from pathlib import Path


SLOT_FIELDS = {
    "sourceHandlers": (
        "outputItems", "outputFluids", "otherItems", "normalDrops", "rareDrops",
        "additionalDrops", "infernalDrops", "fishItems", "junkItems", "treasureItems",
        "eliteItems", "ultraItems", "infernoItems",
    ),
    "usageHandlers": ("inputItems", "inputFluids", "otherItems", "keyItem"),
}


def entries(value):
    """槽位可以是字符串、物品对象或嵌套的可选项数组。"""
    if isinstance(value, (str, dict)):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from entries(child)


def collect_handlers(names, raw_keys, query_keys, version_dir):
    """返回名称到两种 handler 列表的映射；handler 使用 Recipes 下的 mod/处理器路径。"""
    root = Path(version_dir)
    tokens, raw_names, wildcard_names = (defaultdict(set) for _ in range(3))
    for name, keys in names.items():
        for key in keys:
            raw = raw_keys.get(key, key)
            tokens[key].add(name)
            if query_keys.get(key):
                tokens[query_keys[key]].add(name)
            raw_names[raw].add(name)
            base, separator, damage = raw.rpartition(":")
            wildcard_names[base if separator and damage.isdigit() else raw].add(name)

    for file in sorted((root / "OreDictionary").rglob("*.json")):
        for ore, values in json.loads(file.read_bytes()).items():
            for entry in entries(values):
                key = entry if isinstance(entry, str) else entry.get("key", "")
                tokens["#" + ore].update(tokens.get(key, ()))
                if key.endswith(":32767"):
                    tokens["#" + ore].update(wildcard_names.get(key[:-6], ()))

    indexes = {field: defaultdict(set) for field in SLOT_FIELDS}
    for file in sorted((root / "Recipes").rglob("*.json")):
        if file.name == "routing-manifest.json":
            continue
        data = json.loads(file.read_bytes())
        parts = file.relative_to(root / "Recipes").parts
        handler = "/".join(parts[:2]).removesuffix(".json")
        for recipe in data.get("recipes", []):
            for field, slots in SLOT_FIELDS.items():
                matched = set()
                for slot in slots:
                    for entry in entries(recipe.get(slot)):
                        values = [entry] if isinstance(entry, str) else [entry.get("key"), entry.get("queryKey")]
                        for token in values:
                            matched.update(tokens.get(token, ()))
                            if field == "sourceHandlers":
                                matched.update(raw_names.get(token, ()))
                        # 单产物名称查询可合并为 rawKey；跨版本合并候选后也可能达到该阈值。
                        if field == "sourceHandlers" and isinstance(entry, dict):
                            matched.update(raw_names.get(entry.get("rawKey"), ()))
                for name in matched:
                    indexes[field][name].add(handler)
    return {field: {name: sorted(paths) for name, paths in index.items()}
            for field, index in indexes.items()}
