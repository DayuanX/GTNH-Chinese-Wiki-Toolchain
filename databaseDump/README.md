# databaseDump

这是 GTNH 中文维基工具链中的数据导出、整理与上传子目录。

## 工作原理

整体流程分三段：

1. 先从 Minecraft `dumps/` 里拆出适合 wiki 上传的页面数据。
2. 再扫描本地 `wikitext/` 和 wiki 现有内容，生成上传清单数据库。
3. 最后按清单批量上传页面、图标和图片重定向。

## 主要脚本

- `split_all.py`：读取原始导出数据，合并图标/流体等信息，并切分成 wiki 可上传的小文件。
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
本地模组名为 `GTNH` 时从 dumps 顶层读取，并排除 filtered；其他模组从
`dumps/filtered/<本地模组名>/` 读取，不混入顶层或其他模组的数据。远端名不影响数据源选择。
模组目录不存在会报错；缺失或空的主输入文件会跳过。NBT 扫描缓存按本地模组名/版本保存在
`databaseDump/source_state/` 中。页面清单与上传不再拒绝包含 filtered 的文件路径。
修改配置后应重新运行拆分和清单脚本。缺少配置会报错，不再交互询问。
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

- 本地模组名为 GTNH 时使用 `dumps/icons/` 和 `dumps/image_redirect.csv`。
- 其他模组使用 `dumps/filtered/<本地模组名>/icons/` 和该目录的 `image_redirect.csv`。

依次运行图片清单、图片上传、图片重定向三个脚本。非 GTNH 模组首次运行会创建独立数据库，
GTNH 保留已有数据库和上传记录。图片任务只选择当前来源中存在的文件，重定向任务只选择当前 CSV
中仍有效且目标图片已上传的记录。失败的图片保持待更新状态，重复运行可以重试。

Wiki 图片使用原文件名 `File:<图标文件名>`；模组/版本用于选择数据源和本地清单，不添加到图片标题中。

## 独立 mod 版本数据

在 `.env` 中分别配置本地目录名、远端目录名和版本号，例如上文的
`MOD_LOCAL_NAME=TwistSpaceTechnology`、`MOD_REMOTE_NAME=TST`、`MOD_VERSION=1.0.0`。
然后在工作区根目录 `G:\gtnhwiki` 依次运行：

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
