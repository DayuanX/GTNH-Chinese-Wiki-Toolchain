# GTNH Chinese Wiki Toolchain

这是 GTNH 中文维基的本地工具链仓库。

## 目录结构

- `wiki.py`：MediaWiki API 辅助脚本，负责登录、读写页面、文件同步等基础操作。
- `databaseDump/`：数据拆分、任务清单、图标与重定向上传的主流程。

## 主要用途

这套工具主要做三件事：

1. 从 `dumps/` 里的原始导出数据拆分出适合维基上传的页面数据。
2. 扫描本地 `wikitext/` 与维基现有内容，生成上传/删除任务。
3. 批量上传页面、图标文件和图片重定向。

## 数据文件

仓库里保留的是工具脚本和必要的任务数据库，不包含庞大的原始 `dumps/` 内容。

- `databaseDump/image_inventory.db` 会保留在仓库中，用于记录图片上传与重定向状态。
- `databaseDump/wiki_inventory.db` 每次都可以重新生成，不提交到仓库。

## 配置

复制 `.env.example` 为 `.env`，然后按本机情况填写路径和账号：

```bash
copy .env.example .env
```

需要重点修改：

- `DUMPS_DIR`：Minecraft 导出的 `dumps` 目录。当前数据源由 [iouter/GTNH-Dumper](https://github.com/iouter/GTNH-Dumper) 生成，`DUMPS_DIR` 应指向这个工具导出的目录。
- `BASE_URL`：目标 wiki 的 MediaWiki API 地址，默认是 `https://gtnh.huijiwiki.com/api.php`。
- `AUTHKEY`：wiki API 需要的认证 key。
- `EDITOR_USERNAME` / `EDITOR_PASSWORD`：普通编辑账号，供 `wiki.py` 使用。
- `BOT_USERNAME` / `BOT_PASSWORD`：批量上传账号，供 `databaseDump` 里的上传脚本使用。

`.env` 不会提交到仓库。

## 运行方式

通常先运行拆分和任务生成，再执行上传脚本：

```bash
python databaseDump/split_all.py
python databaseDump/wiki_inventory.py
python databaseDump/image_inventory.py
python databaseDump/data_upload.py
python databaseDump/image_upload.py
python databaseDump/image_redirect.py
```
