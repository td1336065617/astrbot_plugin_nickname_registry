# 昵称ID档案馆（astrbot_plugin_nickname_registry）

解决 **QQ 官方平台拿不到 QQ 号、也无法用 QQ 号反查 openid** 的问题：把两侧的「昵称 ↔ ID」沉淀下来，
在“人”这一层建立 **QQ号 ↔ openid** 关联，并支持双向查询、改名历史、同名多人分析与导出。

## 平台能力

| 能力 | QQ 官方 | OneBot（aiocqhttp） |
| --- | --- | --- |
| 被动记录发言者（昵称 + ID） | ✅（openid） | ✅（QQ 号） |
| 群名片 / 群角色 | ❌ | ✅ |
| 全量群成员列表 | ❌ 不支持 | ✅ |
| 拿到 QQ 号 | ❌ | ✅ |

> 官方群只能记录**发过言的人**；要拿到 QQ 号必须依赖 OneBot 侧或人工确认。

## 安装

把本目录放到 AstrBot 的 `data/plugins/` 下即可（无需第三方依赖，只用标准库）。

## 指令（仅后台管理员）

| 指令 | 说明 |
| --- | --- |
| `查QQ <QQ号>` | 查该 QQ 号对应的官方 openid（含候选） |
| `查昵称 <关键词>` | 按昵称 / 群名片检索成员 |
| `查ID <openid>` | 反查该 openid 的 QQ 号与昵称 |
| `绑定QQ <QQ号>` | 本人自报（默认关闭，且只生成候选） |
| `档案馆帮助` | 用法 |

权限：AstrBot 内置管理员（`event.is_admin()`），或后台“设置”里配置的附加管理员名单。

## 后台页面

`页面路径：插件管理 → 昵称ID档案馆 → manage`

概览 / 成员检索 / 身份关联 / 未关联清单 / 群与同步 / 数据管理 / 设置。

- **群名**：后台优先显示群名（OneBot 被动捕获；官方走开放接口 `Route` 拉取），ID 作副标题与悬停提示；缺名时点「刷新群名」批量补齐。

关键概念：

- **群映射**：官方群的 ID 是 `group_openid`、OneBot 群是 QQ 群号，**同一个真实群两边 ID 不同**，
  必须先在「群与同步」里人工配对，之后才能在配对内做“同群同昵称”候选。
- **关联状态**：`candidate`（候选，需人工确认）/ `confirmed`（已确认）/ `rejected`（已驳回）。
  一个 openid 只能属于一个 QQ 号；一个 QQ 号可有多个 openid（不同机器人）。
- **未关联清单**：官方侧已建档但还没有 QQ 号的成员。

## 数据与隐私

- 只存元数据：昵称、群名片、ID、群、时间、发言计数、改名历史。
- **不存聊天内容**；仅后台管理员可见；支持按群 / 按成员 / 全量删除。
- 数据库位置：`data/plugin_data/astrbot_plugin_nickname_registry/nickname.db`（SQLite）。

## 文档

- `docs/昵称ID档案馆-需求文档.md`
- `docs/昵称ID档案馆-设计文档.md`
- `docs/昵称ID档案馆-实现文档.md`

## 开发

```bash
python -m pytest tests -q
ruff check --select F,E9 main.py src/ tests/
```
