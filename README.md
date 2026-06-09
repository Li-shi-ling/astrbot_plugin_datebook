# Datebook 自定义节日日历

Datebook 是一个 AstrBot 自定义节日日历插件。它不会维护法定节假日，而是让 AI 或用户创建自己的纪念日、活动日、群内节日，并在订阅会话每天 12:00 播报当天内容。

## 功能

- 使用 SQLite 保存自定义节日和播报订阅会话。
- AI 可调用工具创建、更新、删除、查询节日。
- AI 可为当前会话开启或关闭每日 12:00 播报。
- 用户可通过 `/datebook` 命令手动管理节日和订阅。
- 每个订阅会话每天最多播报一次，避免插件重启后重复发送。

## AI 工具

- `datebook_create_festival`：创建自定义节日。
- `datebook_update_festival`：更新节日名称、日期、描述或启停状态。
- `datebook_delete_festival`：删除节日。
- `datebook_list_festivals`：按日期或关键词查询节日。
- `datebook_set_daily_broadcast`：为当前会话开启或关闭每日播报。

## 命令

```text
/datebook 今天
/datebook 列表 [月] [日]
/datebook 添加 月 日 名称 [描述]
/datebook 删除 节日ID
/datebook 启用 节日ID
/datebook 停用 节日ID
/datebook 订阅
/datebook 取消订阅
/datebook 测试播报
```

示例：

```text
/datebook 添加 6 9 摸鱼节 今天适合少开会
/datebook 订阅
```

订阅后，插件会在每天 12:00 向当前会话发送当天自定义节日；如果当天没有节日，会提示暂无自定义节日。
