# astrbot_plugin_simple_rss

一个面向 AstrBot 的轻量 RSS 订阅插件，支持按频道管理订阅源、定时拉取并主动推送新内容，也支持手动查询最新内容。

## 功能

- 按频道（`unified_msg_origin`）隔离订阅列表。
- 支持添加、查看、删除、修改订阅更新频率。
- 支持手动获取单个或全部订阅源的最新内容。
- 定时任务自动轮询并推送新内容。
- 支持 RSS 2.0 与常见 Atom Feed 的基础解析。

## 命令

### 1) 添加订阅

```text
/rss add <origin-url> [cron exp]
```

- `origin-url`：RSS/Atom 链接。
- `cron exp`：可选，支持 5 段或 6 段 cron 表达式。
- 未传 `cron exp` 时，使用默认值（`*/30 * * * *`，每 30 分钟）。

示例：

```text
/rss add https://dedicated.wallstreetcn.com/rss.xml * 0/5 * * * 0-7
```

### 2) 查看当前频道订阅

```text
/rss ls
```

返回当前频道下的订阅序号、标题、URL 与 cron。

### 3) 移除订阅

```text
/rss remove <list-index>
```

- `list-index`：来自 `/rss ls` 的序号。

### 4) 修改订阅频率

```text
/rss change <list-index> [cron exp]
```

- 不传 `cron exp` 时会重置为默认 cron。

### 5) 获取最新内容

```text
/rss get [all|list-index] [number]
```

- `all`：获取当前频道全部订阅源。
- `list-index`：获取指定订阅源。
- `number`：每个订阅源返回的最新条数，默认 `15`。

示例：

```text
/rss get
/rss get all
/rss get all 10
/rss get 0
/rss get 0 5
```

## 配置项

通过 AstrBot 插件配置可调整：

- `default_cron_exp`（string，默认 `*/30 * * * *`）
  - 新增订阅时默认使用的 cron 表达式。
- `init_fetch_count`（int，默认 `20`）
  - 新增订阅时初始化拉取并记录的条目数，用于建立去重基线。

兼容性说明：

- 代码中同时兼容读取 `default_corn_exp`（历史拼写）作为兜底。

## 数据存储

- 本地数据文件：`data/astrbot_plugin_simple_rss_data.json`
- 包含订阅源信息、频道订阅关系、cron 配置与去重检查点。

## 依赖

- `aiohttp`
- `apscheduler`

## 开发说明

- 插件入口：`main.py`
- 数据层：`data_handler.py`
- RSS 拉取与解析：`rss_client.py`
- Cron 工具：`cron_utils.py`
- 数据模型：`rss.py`
