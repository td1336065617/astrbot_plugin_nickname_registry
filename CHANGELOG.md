# 更新日志

本文件记录所有值得注意的变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

图例：✨ 新增 · 🐛 修复 · ⚙️ 变更 · ⚠️ 升级注意

---

## [0.2.0] - 2026-09-26

> ✨ 后台改为「群名优先」：官方渠道也能看到群名，不再满屏 group_openid。

### ✨ 新增
- **群名缓存与解析**（`src/group_names.py` + `group_meta` 表）：
  - 被动捕获：OneBot 事件自带 `group_name` 时自动入库（进程内缓存，避免每条消息写库）；
  - 主动拉取：OneBot 走 `get_group_info`；**官方渠道走开放接口** `GET /v2/groups/{group_openid}/info`，复用 botpy 已持有的 access_token（`Route` + `inst.client.api._http`），无需另配 appid/secret；
  - 后台「群与同步」新增**「刷新群名」**按钮，只给还没有名字的群补一次（默认最多 50 个）。
- 后台展示统一为「**群名 + ID 简写**」（鼠标悬停看完整 ID）：成员检索、未关联清单、群与覆盖度、成员详情抽屉。

### ⚙️ 变更
- 新增接口 `POST /astrbot_plugin_nickname_registry/groups/refresh`。

---

## [0.1.0] - 2026-09-25

> 🎉 首个版本：记录昵称与 ID 关系，打通 QQ号 ↔ 官方 openid。

### ✨ 新增
- **被动采集**：三个平台（qq_official / qq_official_webhook / aiocqhttp）的群消息发送者入库（昵称 / 群名片 / 角色 / 群 / 时间 / 发言数），只存元数据不存消息内容。
- **OneBot 全量同步**：按群拉取全量成员（含 QQ 号 / 群名片 / 角色）；官方通道明确提示“不提供群成员列表”。
- **身份关联**：QQ号 ↔ openid 的候选生成 / 人工确认 / 驳回 / 解绑；一个 openid 只能属于一个 QQ 号，一个 QQ 号可对应多个 openid。
- **群映射**：官方群 ↔ OneBot 群人工配对（两边 group_id 语义不同），配对内按“同群同昵称”生成候选。
- **改名历史**：昵称 / 群名片变化时追加时间线。
- **双向解析**：`resolve_openid(qq)` / `resolve_qq(openid)`，可供其他插件按插件名反查调用。
- **后台页面**：概览 / 成员检索 / 身份关联 / 未关联清单 / 群与同步 / 数据管理 / 设置。
- **指令**：`查QQ` / `查昵称` / `查ID` / `绑定QQ`（本人自报，默认关闭）/ `档案馆帮助`。
- **导入导出**：CSV（带 BOM，Excel 友好）与 JSON（可回导合并）。
- **数据管理**：按群 / 按成员 / 按平台 / 全量删除；历史条数上限与保留天数设置。

### ⚙️ 变更
- 落库走内存聚合 + 定时/阈值批量 upsert，热路径不做同步 IO；队列有上限，溢出丢弃并计数。
