# databaseDump

这是 GTNH 中文维基工具链中的数据导出、整理与上传子目录。

## 工作原理

整体流程分三段：

1. 先从 Minecraft `dumps/` 里拆出适合 wiki 上传的页面数据。
2. 再扫描本地 `wikitext/` 和 wiki 现有内容，生成上传清单数据库。
3. 最后按清单批量上传页面、图标和图片重定向。

## 主要脚本

- `split_all.py`：读取原始导出数据，合并图标/流体等信息，并切分成 wiki 可上传的小文件。
- `item_names.py generate-config`：生成本地中文图片候选报告，供编辑首选配置时参考。
- `wiki_inventory.py`：扫描本地 `wikitext/`，对比 wiki 现有页面，生成 `wiki_inventory.db`。
- `image_inventory.py`：扫描 `dumps/icons/` 和重定向映射，生成 `image_inventory.db`。
- `data_upload.py`：读取 `wiki_inventory.db`，把待上传页面推到 wiki。
- `image_upload.py`：读取 `image_inventory.db`，上传图标文件。
- `image_redirect.py`：读取 `image_inventory.db`，创建 `File:` 图片重定向页。

## 数据库

- `wiki_inventory.db`：页面上传任务表，每次运行 `wiki_inventory.py` 都可以重新生成，不提交到仓库。
- `image_inventory.db`：GTNH 图标上传与重定向任务表，会提交到仓库用于保留图片上传状态。
  其他模组的图片状态独立保存在 `source_state/<本地模组名>/<MOD_VERSION>/image_inventory.db`，不提交到仓库。

## 配置

脚本默认从仓库根目录的 `.env` 读取配置。先复制 `.env.example` 为 `.env`，再按本机环境填写：

```bash
copy .env.example .env
```

其中 `DUMPS_DIR` 对应 Minecraft 导出的 `dumps` 目录。当前数据源由 [iouter/GTNH-Dumper](https://github.com/iouter/GTNH-Dumper) 导出，填写时应让 `DUMPS_DIR` 指向 GTNH-Dumper 生成的 dumps 目录。

## 常用命令

在 `.env` 中设置上传目标，例如：

```dotenv
MOD_LOCAL_NAME=GTNH
MOD_REMOTE_NAME=GTNH
MOD_VERSION=2.9.0-beta-1
```

可用 `MOD_LOCAL_NAME` 和 `MOD_REMOTE_NAME` 分别指定本地数据源名和远端目录名。
两项均须填写，缺失或为空时会报错。例如：

```dotenv
MOD_LOCAL_NAME=TwistSpaceTechnology
MOD_REMOTE_NAME=TST
MOD_VERSION=1.0.0
```

此时从 `dumps/filtered/TwistSpaceTechnology/` 读取，上传到 `Data:Versions/TST/1.0.0/`。
`split_all.py` 输出到 `wikitext/Data/Versions/<远端模组名>/<MOD_VERSION>/`；
`wiki_inventory.py` 只扫描和查询该模组/版本，`data_upload.py` 只执行该前缀下的上传和删除任务。
本地模组名为 `GTNH` 时读取 dumps 顶层；其他模组读取
`dumps/filtered/<本地模组名>/`。远端名用于生成上传页面的目录。
模组目录不存在会报错；缺失或空的主输入文件会跳过。NBT 扫描缓存按本地模组名/版本保存在
`databaseDump/source_state/` 中。页面清单支持 filtered 目录中的文件。
修改配置后应重新运行拆分和清单脚本。缺少配置时，按报错提示补齐 `.env` 后重试。
普通 Wiki 页面源码上传仍使用文件对应的页面路径。

```bash
python split_all.py
python wiki_inventory.py
python image_inventory.py
python data_upload.py
python image_upload.py
python image_redirect.py
```

## 图片上传

`image_inventory.py`、`image_upload.py`、`image_redirect.py` 同样读取 `.env` 的模组配置：

- 本地模组名为 GTNH 时使用 `dumps/icons/`，其他模组使用 `dumps/filtered/<本地模组名>/icons/`。
- 中文图片重定向使用预处理生成的 `source_state/<本地模组名>/<版本>/image_redirect.resolved.csv`。
  原始 `image_redirect.csv` 保留作参考。重名选择规则见下文。

依次运行图片清单、图片上传、图片重定向三个脚本。非 GTNH 模组首次运行会创建独立数据库，
GTNH 保留已有数据库和上传记录。图片任务只选择当前来源中存在的文件，重定向任务只选择当前 CSV
计划中仍有效且目标图片已上传的记录。失败的图片保持待更新状态，重复运行可以重试。

三个图片脚本都会在执行前通过 `image_plan.py` 重新计算当前 PNG 的哈希，需要安装 Pillow：
`python -m pip install Pillow`。运行 `image_inventory.py` 生成本地计划和状态，随后由上传脚本执行。
计划保存在同一状态目录的 `image-upload-plan.json`，包含实体上传列表、最终重定向和各组哈希。

只在同一注册物品 `modid:name` 内比较不同 damage/NBT 变体。哈希覆盖尺寸与完整 RGBA
像素，PNG 压缩和文字元数据不同不影响比较；动画条带比较全部帧，APNG 比较逐帧合成后的完整 RGBA 画布、帧时长、循环次数和默认图像。
同图优先选无 NBT、damage 为 0 的图片；缺少该项时依次按无 NBT、零 damage、damage 数值、
完整 shortKey 和文件名排序。每个注册物品分别选择代表图片。

同图变体保留自己的文件名，通过重定向指向代表图片并退出上传列表；中文名先按人工配置
确定物品，再直接指向该物品最终代表图片。因此中文名和变体别名都是一级重定向。
物品 key、NBT、配方数据、本地 PNG 和已上传文件历史均保留。额外素材沿用原有上传方式。

Wiki 图片标题为 `File:<图标文件名>`；模组和版本用于选择数据源及本地清单。

### 中文重名图片的人工选择

物品预处理会自动扫描重名，生成诊断报告和重定向表。人工选择和默认规则确定的标题进入
重定向任务，其余标题留在候选报告中，供编辑者选择。
名称经过文件名清理、空格/下划线归一和首字母规范化之后发生的冲突也会被合并检查。

在本目录运行：

```bash
# 先生成最终物品 key、名称索引和重名报告。
python split_all.py items

# 生成候选报告，查看各名称对应的物品。
python item_names.py generate-config --output image-name-candidates.json

# 编辑 config/preferred-items.json 的明确约定后，重新应用选择。
python split_all.py items
```

默认读取 `config/preferred-items.json`（`schemaVersion: 2`）。每个标题的 `key` 是明确指定的
完整物品 key，优先于自动规则；显式 `null` 表示跳过该别名。未配置的名称自动使用默认偏好：
优先流体身份 `fluid.*`，然后优先 GregTech。在同一物品身份内优先无 NBT
版本，在同一注册物品内优先 damage 为 0 的版本。GT 矿石优先普通石头底岩；天然状态和
小型矿石属性仍保留区分。筛选后仍不能唯一确定的名称留在本地未解决报告中。
同一 rawKey 没有无 NBT 版本时，按完整 key 字典序选择第一个作为图片代表，配方继续使用各自的完整 key。
GT 电池箱按 tooltip 选择 1 槽版本；GT 控制器依据“已弃用”或 `deprecated` 标记筛选当前版本。多个流体优先选择 `registeredBy=gregtech` 的候选；
此字段来自 Forge 注册表；该规则适用于包含此字段的数据。
同模组不同注册物品、没有零 damage 且无专门规则的多个子类型仍留给人工。
矿石规则适用于 GT `gt.blockores2`～`gt.blockores7`。

`null` 表示不发布该中文图片重定向。下面使用演示数据：

```json
{
  "schemaVersion": 2,
  "choices": {
    "示例物品.png": {"key": "example:item:3", "reason": "编辑者确认的显示约定"},
    "不发布的别名.png": {"key": null, "reason": "不为该名称发布图片别名"}
  }
}
```

公共清单只保存明确约定，通过 PR 修改 `key` 和选择理由 `reason`。
`IMAGE_NAME_CONFIG` 可显式指定同格式的自定义清单，替代默认公共清单。
候选列表写入本地 `image-name-candidates.json`，冲突和失效选择写入
`image-name-resolution.json`。这两个文件供本地检查，公共 PR 修改 `config/preferred-items.json`。
失效 key 或已消失名称会报告并跳过，请在报告中核对并更新首选配置。
配置、原物品数据或图标映射变更后必须重新预处理；图片发布前会验证当前配置路径和文件的 SHA-256，切换配置路径后也须重新预处理。

图片状态使用 `images` 与 `redirects` 两张本地表，支持多个中文别名指向同一个 PNG。
首次运行图片清单时会迁移旧表中的上传状态。图片清单及上传脚本负责维护该数据库。
旧表中同一个中文标题关联了多张图片时，该标题重新标为待更新，按当前人工选择纠正目标。

### 中文名称查询索引

`split_all.py items` 同时生成 `ItemNames/<桶号>/<分块号>.json`，结构为：

```json
{"hashAlgorithm":"sha256-xor128-base62-15","bucketCount":64,"names":{"示例物品":["example:item:3","another:item"]}}
```

`names[中文名]` 的数组使用物品预处理结束后的最终 `key`，包含仍然需要的 NBT 哈希后缀。
索引保留同名的全部候选，中文图片重定向单独应用人工选择。
每页 `names` 是对象，可直接用 `names[中文名]` 取得完整 key 数组。同一名称的超大数组可以跨页，
每页同名只出现一次；查询时合并所有分页中该名称的数组并去重。
普通名称可在 MongoDB projection 中指定 `names.铁锭`，但必须通过 `find` 获取该桶的全部匹配分页，
以合并跨页候选。包含点或 `$` 的名称需要按字面键读取；
可在服务器支持时使用聚合的 `$getField` 配合 `$literal`，或先读取桶页再用 Lua `names[名称]` 索引。
使用聚合前，请确认服务端支持这些操作符。
索引用于精确名称查询；模糊搜索需要扫描相应范围的数据。

### 物品 key 分桶

`split_all.py items` 生成 `Items/<桶号>/<分块号>.json`。它以最终 `key` 分桶，
保存每个物品的完整记录，包括名称、说明、图片映射、NBT 和原 `index`。
原 `Items/` 根目录数字顺序分页在生成成功后清理，只保留一份物品数据。查询端取得 key 后计算对应桶，再在桶内精确匹配 key；
同一桶超过大小限制时需要读取该桶的所有分块。

### 可选配方分桶

```bash
# 先完成物品处理；配方内部先做 NBT 关联，再生成可选分桶。
python split_all.py items recipes --recipe-buckets
```

启用后，适配池原位写到 `Recipes/<原机器目录>/<桶号>/<分块号>.json`，成功生成清单后删除对应机器根目录的旧数字顺序页。
未启用分桶或不在适配范围的配方池仍使用 `Recipes/<原机器目录>/<分块号>.json`。
范围固定为有序合成、无序合成、匠魂弩箭固化、匠魂部件压模、组装机、合金炉回收、流体固化器、锻造锤
这八个主要 handler，并且只在当前来源已拆成多个文件时处理。其他 handler 保留原文件。
输入为关联完成后的产物 `key`；仍带有未关联 NBT 的产物按基础 `key` 路由，NBT 原样保留。
单输出槽允许物品或流体；嵌套候选须归约为同一个最终 key。
锻造锤的多输出配方单独写入该 handler 的 `shared/<页>.json`；查询锻造锤时同时读取目标桶与 shared 目录的全部数字分块。
其余七个 handler 若出现多输出、`otherItems` 或无可识别输出，整池保留原文件，诊断写入 manifest。
普通配方仅按唯一的最终输出 `key` 写入一个桶，`queryKey` 只保留在记录中，不参与路由。
同槽候选若均为同一个最终 key 可归同桶；存在多个唯一 key 或没有最终 key 时，整池保留顺序分页。
记录保留原 `index`；按 `(handler, index)` 识别记录，按 index 的数字页号、行号排序。
`Recipes/routing-manifest.json` 的 `writtenRecipes` 记录实际写入条数，并列出处理的 handler、原文件、分桶文件和共享文件，便于读取端定位。读取配方时遍历清单指向的数据文件。
重新生成分桶时，从原始 dumps 运行 `split_all.py`。分桶器接受顺序分页的配方文件。

查询端读取目标桶后，按原有条件匹配、排序和分页；未指定产物时需遍历该配方池的全部数据文件。

### 分块哈希约定

名称索引以名称为输入，物品与配方分桶以最终查询 key 为输入。三者共用 `routing_hash.bucket_id(text, bucket_count=64)`；物品显式传入 `ITEM_BUCKET_COUNT=128`，名称与配方使用默认 64。JSON/TABX 页的 `bucketCount` 标明实际桶数，哈希算法不变：

1. 对输入的 UTF-8 字节计算 SHA-256。
2. 前后各 16 字节逐字节异或。
3. 按 Dumper 的 `0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ` 字符表，保留低 15 位 Base62，左侧补零。
4. 名称和配方将该 15 位值对 64 取模，输出 `00` 至 `63`；物品对 128 取模，输出 `00` 至 `99`、`100` 至 `127`。

前三步与当前 Dumper 的 NBT 后缀算法一致，测试包含现有 `{}` → `gLuB0gegBqh1aRe` 向量。
对于 64 或 128 桶，桶号也等于
`(digest[15] XOR digest[31]) % bucketCount`（字节从 0 计数），因此无需在 Lua 中用浮点数表示完整哈希整数。

## 独立 mod 版本数据

在 `.env` 中分别配置本地目录名、远端目录名和版本号，例如上文的
`MOD_LOCAL_NAME=TwistSpaceTechnology`、`MOD_REMOTE_NAME=TST`、`MOD_VERSION=1.0.0`。
然后在仓库的 `databaseDump` 目录依次运行：

```bash
python split_all.py
python wiki_inventory.py
python data_upload.py
```

来源为 `dumps/filtered/TwistSpaceTechnology/`，配方目标例如
`Data:Versions/TST/1.0.0/Recipes/gregtech/GTNEIDefaultHandler_assembler/0.json`。
修改配置后重新运行拆分和清单。页面清单和上传任务范围使用远端名及版本号。

仅处理原拆分器支持的数据类型。缺失或零字节的输入会提示跳过，CSV 清单和筛选报告
不生成数据页。物品所需的 `itemicon.json`、`fluid.json` 优先取本地模组目录，缺失时
从完整 dumps 读取作为关联数据。

### 分桶文件大小限制

`Items` 使用 128 个逻辑桶（JSON 和 TABX 一致），`ItemNames` 与已启用分桶的 `Recipes` 使用 64 个逻辑桶；每桶按需生成多个数字分页。
默认分页上限为 **1,800,000 字节**；可调参数 `max_bytes` 必须为正整数且不得超过硬上限
**2,000,000 字节**（十进制 2 MB）。所有页面按最终紧凑 JSON 的 UTF-8 字节数分页，
计入中文、emoji、metadata、括号和逗号；写入前再次检查完整字节长度。
记录只序列化一次后累计长度，不随每条新增记录反复序列化整批。

名称索引的 `names[名称]` 长数组可跨文件分页，读取端应合并各页同名数组。
单条物品、配方、名称或 key 超出一页容量时，生成停止并报告错误。
`Recipes/routing-manifest.json` 同样受 2,000,000 字节硬上限约束，超限时生成失败。
生成失败后应修正数据或分页参数并重新生成成功，再执行清单和上传步骤。
