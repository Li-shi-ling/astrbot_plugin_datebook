# Datebook 自定义节日日历

Datebook 是一个 AstrBot 自定义节日日历插件。它不会维护法定节假日，而是让 AI 或用户创建自己的纪念日、活动日、群内节日，并按插件配置每天播报当天内容。

## 功能

- 使用 SQLite 保存自定义节日和播报订阅会话。
- 同一天可以创建多个自定义节日。
- AI 通过一个综合工具创建、更新、删除、查询节日。
- 每日播报开关、时间和目标会话通过插件配置管理。
- 用户可通过 `/datebook` 命令手动管理节日和测试播报。
- 每个订阅会话每天最多播报一次，避免插件重启后重复发送。

## AI 工具

- `datebook_manage`：综合管理自定义节日。

参数：

- `action`：操作类型，支持 `create`、`update`、`delete`、`list`、`today`。
- `festival_id`：更新或删除时使用的节日 ID。
- `name`：创建或更新时的节日名称；查询时可作为关键词。
- `month` / `day`：节日日期，未填写时默认今天。
- `description`：创建或更新时的节日描述。
- `enabled`：更新时用于启用或停用；查询时填写 `true` 可包含停用节日。

示例：

```text
action=create, name=摸鱼节, description=今天适合少开会
action=list
action=delete, festival_id=3
```

## 插件配置

- `enable_daily_broadcast`：是否启用每日播报。
- `broadcast_time`：播报时间，默认 `12:00`。
- `broadcast_sessions`：播报目标会话列表，填写 `unified_msg_origin`。

## 命令

```text
/datebook 今天
/datebook 列表 [月] [日]
/datebook 添加 月 日 名称 [描述]
/datebook 删除 节日ID
/datebook 启用 节日ID
/datebook 停用 节日ID
/datebook 测试播报
```

示例：

```text
/datebook 添加 6 9 摸鱼节 今天适合少开会
/datebook 测试播报
```

开启 `enable_daily_broadcast` 并配置 `broadcast_sessions` 后，插件会在指定时间向目标会话发送当天自定义节日；如果当天没有节日，会提示暂无自定义节日。
