"""按已完成 NBT 关联的查询 key 将已适配配方池原位改为分桶分页。"""

import json
from collections import defaultdict
from pathlib import Path

from bounded_json import DEFAULT_MAX_BYTES, HARD_MAX_BYTES, validate_limit, write_array_pages, write_bounded
from routing_hash import BUCKET_COUNT, HASH_ALGORITHM, bucket_id

ROUTE_FIELDS = ("outputItems", "outputFluids")


def output_keys(value):
    """只读取最终输出 key；queryKey 保留为数据，不参与物理路由。"""
    if isinstance(value, str) and value:
        yield value
    elif isinstance(value, list):
        for entry in value:
            yield from output_keys(entry)
    elif isinstance(value, dict):
        yield from output_keys(value.get("key"))


def recipe_route(recipe):
    keys = {key for field in ROUTE_FIELDS for key in output_keys(recipe.get(field))}
    return bucket_id(next(iter(keys))) if len(keys) == 1 else None


def output_slots(recipe):
    """候选列表算一个输出槽；物品数量不影响输出槽数量。"""
    return sum(len(recipe.get(field) or []) for field in ROUTE_FIELDS)


# 只处理本次确认的八个大配方池；其他来源保留原目录。
SUPPORTED_HANDLERS = {
    "minecraft/ShapedRecipeHandler_crafting": "有序合成",
    "minecraft/ShapelessRecipeHandler_crafting": "无序合成",
    "gregtech/GTNEIDefaultHandler_tic_bolt_molding": "匠魂弩箭固化",
    "gregtech/GTNEIDefaultHandler_tic_part_extruding": "匠魂部件压模",
    "gregtech/GTNEIDefaultHandler_assembler": "组装机",
    "gregtech/GTNEIDefaultHandler_alloy_smelter_recycling": "合金炉回收",
    "gregtech/GTNEIDefaultHandler_fluidsolidifier": "流体固化器",
    "gregtech/GTNEIDefaultHandler_hammer": "锻造锤",
}
HAMMER = "gregtech/GTNEIDefaultHandler_hammer"


def write_bucket(recipes, metadata, target, max_bytes):
    return write_array_pages(recipes, metadata, "recipes", target, max_bytes)


def build_recipe_buckets(version_dir, max_bytes=DEFAULT_MAX_BYTES):
    validate_limit(max_bytes)
    version_dir = Path(version_dir)
    source = version_dir / "Recipes"
    output = source
    # 已分桶输入没有根顺序分页，直接重复运行应明确拒绝，不能误当空池清除原数据。
    # 重新生成应从 split_all 的原始来源处理开始，由其先刷新各机器的顺序分页。
    for handler in SUPPORTED_HANDLERS:
        directory = source / handler
        for child in directory.iterdir() if directory.is_dir() else ():
            if child.is_dir() and (child.name == "shared" or
                                   len(child.name) == 2 and child.name.isdecimal()):
                if any(path.stem.isdecimal() for path in child.glob("*.json")):
                    raise ValueError(f"配方池已分桶，请从原始来源重新生成后再分桶: {handler}")
    written, handlers, skipped = set(), {}, {}
    replaced_files = []
    total = written_recipes = shared = 0
    for handler, name in SUPPORTED_HANDLERS.items():
        files = sorted((path for path in (source / handler).glob("[0-9]*.json")
                        if path.stem.isdecimal()), key=lambda path: int(path.stem))
        if len(files) <= 1:
            skipped[handler] = "当前来源少于两个文件，保留原文件"
            continue
        entries, metadata, invalid = [], None, False
        for path in files:
            data = json.loads(path.read_text("utf-8"))
            if metadata is None:
                metadata = {key: value for key, value in data.items() if key != "recipes"}
            for recipe in data.get("recipes", []):
                slots = output_slots(recipe)
                # 不扩展到辅助字段或其他多输出结构；形态变化时整池退回原文件。
                if recipe.get("otherItems") or not slots:
                    invalid = True
                if slots == 1 and recipe_route(recipe) is None:
                    invalid = True
                if slots != 1 and handler != HAMMER:
                    invalid = True
                entries.append(recipe)
        if invalid:
            skipped[handler] = "输出形态或非唯一 final key 超出本次范围，保留原文件"
            continue
        grouped = defaultdict(list)
        for recipe in entries:
            route = "shared" if output_slots(recipe) > 1 else recipe_route(recipe)
            shared += route == "shared"
            total += 1
            grouped[route].append(recipe)
            written_recipes += 1
        metadata["routing"] = {
            "hashAlgorithm": HASH_ALGORITHM, "bucketCount": BUCKET_COUNT,
            "fields": list(ROUTE_FIELDS), "identityFields": ["key"],
            "sharedDirectory": "shared" if handler == HAMMER else None,
        }
        routes = {}
        for route, recipes in sorted(grouped.items()):
            paths = write_bucket(recipes, metadata, output / handler / route, max_bytes)
            written.update(paths)
            routes[route] = [path.relative_to(output).as_posix() for path in paths]
        replaced_files.extend(files)
        handlers[handler] = {
            "name": name, "sourceFiles": [path.relative_to(source).as_posix() for path in files],
            "sourceRecipes": len(entries), "routes": routes,
        }
    report = {
        "sourceRecipes": total, "writtenRecipes": written_recipes, "sharedRecipes": shared,
        "files": len(written), "hashAlgorithm": HASH_ALGORITHM, "bucketCount": BUCKET_COUNT,
        "handlers": handlers, "skipped": skipped,
    }
    output.mkdir(parents=True, exist_ok=True)
    write_bounded(output / "routing-manifest.json", json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"), HARD_MAX_BYTES)
    for path in replaced_files:
        path.unlink()
    print(f"  [Recipe buckets] {len(handlers)} handlers; {total:,} recipes; {shared} shared; {len(written)} files")
    return report
