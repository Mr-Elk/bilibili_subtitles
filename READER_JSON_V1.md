# 本地字幕读取 JSON 协议 v1

本协议供脚本和后续工具读取 `Inventory`、`Map`、`Search`、`Slice` 的结果。文本输出仍是默认格式；需要机器读取时显式传入 `-Format Json`。

## 通用结构

```json
{"schema":"bilibili-subtitles.reader","schema_version":1,"action":"search","target":"D:\\subtitles\\BV...","parameters":{"query":"关键词","context":1,"max_results":8},"match_count":1,"item_count":3,"returned_count":3,"truncated":false,"items":[]}
```

- `schema` 与 `schema_version`：协议身份和版本。后续不兼容变更必须提升版本。
- `action`：`inventory`、`map`、`search` 或 `slice`。
- `target`：本次读取的绝对文件或目录路径。
- `parameters`：影响结果的动作参数。
- `item_count`：限长前的条目数。
- `returned_count`：实际返回的条目数。
- `truncated`：是否因 `MaxChars` 移除了尾部完整条目。
- `items`：动作结果。没有搜索结果或时间范围为空时是空数组。
- `match_count`：仅 `search` 提供，表示命中的字幕数；`items` 还可能包含上下文。

## 各动作条目

- `inventory`：`file`、`cue_count`、`start`、`end`、`text_chars`、`title`。
- `map`：`chunk_id`、`file`、`chunk`、`start`、`end`、`cue_count`、`text_chars`。
- `search`：`file`、`timestamp`、`seconds`、`text`、`is_match`。`is_match=false` 表示上下文字幕。
- `slice`：`file`、`timestamp`、`seconds`、`text`。

## 限长约定

JSON 使用 UTF-8 紧凑输出。超过字符上限时，工具按原顺序保留尽可能多的完整条目，并设置 `truncated=true`。输出始终是可解析的 JSON；如果连协议元数据都无法放入指定上限，命令会失败并提示提高 `MaxChars`。
