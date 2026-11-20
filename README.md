# 华润燃气 Home Assistant 集成（CRC GAS）

[![Release](https://img.shields.io/github/v/release/Boxzhi/ha-crcgas)](https://github.com/Boxzhi/ha-crcgas/releases/latest)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![HA declared minimum](https://img.shields.io/badge/Home%20Assistant-2024.12%2B-green)](#安装要求)
[![Regression tests](https://github.com/Boxzhi/ha-crcgas/actions/workflows/regression.yml/badge.svg)](https://github.com/Boxzhi/ha-crcgas/actions/workflows/regression.yml)
[![License](https://img.shields.io/github/license/Boxzhi/ha-crcgas)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/Boxzhi/ha-crcgas)](https://github.com/Boxzhi/ha-crcgas/commits/main/)

通过 Home Assistant 接入华润燃气微信小程序的数据，查看账户余额、欠费、账单、用气量、阶梯额度以及缴费历史，并支持能源面板统计。

本项目 fork 自 [C3H3-AI/ha-crcgas](https://github.com/C3H3-AI/ha-crcgas)，保留原集成域名 `crcgas`，在原有功能基础上修复数据解析、缴费历史及定时调度问题。本说明以 v2.1.6 为基准。

## 功能特性

- **余额与账单**：查询账户余额、欠费、应收金额、结算状态和抄表记录。
- **阶梯用气**：动态解析官方阶梯额度、累计用量及剩余量，兼容新旧阶梯编号；不以固定地区额度代替实际数据。
- **缴费历史**：获取最近三个自然年的官方缴费记录，区分成功燃气缴费与业务费，支持渠道识别和年度汇总。
- **更新策略**：按小时间隔，或按日、周、月指定时间进行真实日历调度；手动刷新不改变下一次定时计划。
- **缓存与容错**：阶梯数据失效时保留本次运行中的有效快照；缴费历史支持独立本地持久化。
- **能源统计**：支持历史账单、用气量和费用的统计导入。
- **查询接口**：提供已登录用户可按权限查询的缴费历史 WebSocket API，便于第三方仪表盘卡片接入。

## 安装要求

- Home Assistant 已启用 Recorder 与 WebSocket API。
- [hacs.json](hacs.json) 声明最低 Home Assistant 版本为 2024.12.0；该值是仓库声明，并非对所有后续版本的实机兼容保证。
- 已登录华润燃气微信小程序并绑定燃气户号。
- HA 能够访问华润燃气云端接口。
- HACS 为可选安装方式；也可以手动安装。本集成不是 Home Assistant 官方内置集成。

历史统计实现含 Recorder SQLite 直接读写逻辑，其他数据库后端的完整历史统计兼容性未经验证。安装前建议备份 HA，尤其是已有能源统计的环境。

## 安装

### HACS 安装（推荐）

1. 打开 HACS，进入右上角菜单的「自定义存储库」。
2. 仓库地址填写 `https://github.com/Boxzhi/ha-crcgas`。
3. 类别选择「集成（Integration）」。
4. 添加后搜索 **CRC GAS 华润燃气**，下载所需的正式版本。
5. 重启 Home Assistant，再进入「设置 → 设备与服务 → 添加集成」完成配置。

[查看最新正式版本](https://github.com/Boxzhi/ha-crcgas/releases/latest)。

### 手动安装

1. 从 Releases 下载对应版本的 Source code (zip) 并解压。
2. 复制解压后的 `custom_components/crcgas/` 整个目录到 HA 的配置目录（HAOS 一般为 `/config/custom_components/crcgas/`）。
3. 确认 `manifest.json` 位于 `custom_components/crcgas/manifest.json`，且其他 Python 文件及 translations 目录均完整。
4. 重启 HA，再添加集成。

请勿将整个仓库根目录当作集成目录复制。

### 从上游仓库迁移（保留认证配置）

原仓库和本 fork 使用相同的域名 `crcgas`、认证字段及已有实体 Unique ID。常规代码升级无须重新认证，前提是现有 Config Entry 和有效 Token 得以保留。

- 先备份 HA，并备份原 `custom_components/crcgas/`。
- 若 HACS 卸载确认提示会删除已经配置的集成，请取消。不要通过「设备与服务」删除原有配置项。
- 可在停止 HA Core 后手动替换上述代码目录（旧目录备份到配置目录之外），再启动 HA。保留原配置项、实体注册表和历史数据库。
- 不要同时通过两个 HACS 仓库管理同一 `crcgas` 目录。手动切换代码不代表 HACS 下载来源也已切换；需另外确认其仓库管理状态，避免被旧版本覆盖。
- 不建议直接编辑运行中的 HA 的 `.storage` 文件。如果已经删除原配置项，则需重新填写认证信息，不能保证原实体名称和历史关联全部恢复。

## 配置

### 获取认证信息

使用本人已登录的华润燃气微信小程序，通过抓包工具查看一次成功的 API 请求的 Request Headers（请求头），例如 `/bill/getGasBillList`。

| HA 配置项 | 请求头字段 |
| --- | --- |
| Refresh Token | `refresh-token` |
| BO Token | `bo-token` |
| 微信 Code | `wxCode` |

尽量从同一次成功请求中获取三个字段，复制完整值，不要包含额外引号。认证信息属于敏感凭据，不要公开到 Issue、日志截图或聊天记录。

### 首次添加

1. HA → 设置 → 设备与服务 → 添加集成 → 搜索「华润燃气」。
2. 填写三个认证参数，并选择数据更新模式。
3. 点击下一步；集成验证 Token、读取绑定户号，并按所选模式显示专属配置字段。
4. 填写更新周期，提交完成。

当前配置流程采用绑定接口返回的第一个户号，不提供户号选择器；多户号场景的完整隔离和统计兼容性未经验证。

### 数据更新策略（v2.1.5+）

| 更新模式 | 可配置内容 | 默认值 | 示例 |
| --- | --- | --- | --- |
| 按小时间隔 | 1～24 小时 | 6 小时 | 每隔 6 小时更新 |
| 每天定时 | 执行时间 | 07:07 | 每天 07:07 |
| 每周定时 | 星期、执行时间 | 星期一 07:07 | 每周一 07:07 |
| 每月定时 | 1～31 日、执行时间 | 1 日 07:07 | 每月 1 日 07:07 |

时间需精确到分钟，使用 HA 配置的时区。日历模式在短月份自动使用当月最后一天；夏令时缺失时刻使用当日下一个有效时刻，重复时刻仅执行一次。

集成启动时执行一次初始数据更新，之后由独立定时器调度。手动刷新不会重置自动计划；通过「设备与服务 → 华润燃气 → 配置」保存更新选项后会重新安排定时器，无须重新填写认证参数。

**旧版本兼容：** v2.1.4 及更早的按天、周、月配置，在升级后先保留旧版实际轮询频率。例如原「月 / 6」仍是每 180 天，直到重新保存新版配置才切换到日历定时。建议升级后检查配置并重新保存期望的更新规则。

### Token 刷新

Token 刷新与业务数据更新周期独立：

- 每小时主动刷新 Token，并在启动时尝试刷新一次。
- 每分钟检查剩余有效期，少于 5 分钟时尝试紧急刷新。
- 成功获取的新令牌及返回的微信 Code 会通过 Config Entry 保存；业务查询与刷新共享认证锁，明确会话超时后仅在凭据轮换时重试一次。
- 如果 Refresh Token 已失效，需要重新从小程序获取认证信息，在集成的「重新配置」中更新，不必删除配置项。
- 首次 Token 刷新完成后才启动业务平台。部分接口失败显示「部分接口异常」，不再单凭一次会话超时判定密钥过期；余额、欠费暂时失败时保留当次 HA 运行中的上次有效值。

## 实体列表

v2.1.5 的 [SENSOR_TYPES](custom_components/crcgas/const.py) 定义了 **55 个传感器**，另有两个操作按钮。实际实体 ID 由 HA 注册，可能因安装历史和命名而不同；下表保留代码中的实体显示名称。

### 账户余额与账单

| 实体名称 | 单位 | 说明 |
| --- | --- | --- |
| 燃气账户余额 | ¥ | 燃气账户余额，允许真实零值或负值 |
| 欠费金额 | ¥ | 当前欠费金额 |
| 应收金额 | ¥ | 最新账单应收金额 |
| 账单金额 | ¥ | 最新一期账单金额 |
| 账单月份 | — | 最新账单月份 |
| 违约金 | ¥ | 账单违约金 |
| 违约金起算日期 | — | 违约金起算日期 |
| 结算状态 | — | 账单结算状态 |
| 预估燃气账单 | ¥ | 按实际账单阶梯用量及有效单价估算；非未来账单预测 |
| 累计燃气费用 | ¥ | 已保存历史账单金额汇总（最多读取 999 条），不包含充值金额 |

### 用气量与抄表

| 实体名称 | 单位 | 说明 |
| --- | --- | --- |
| 本期表读数 | m³ | 本期燃气表读数 |
| 本期抄表时间 | — | 本期抄表时间 |
| 上期表读数 | m³ | 上期燃气表读数 |
| 上期抄表时间 | — | 上期抄表时间 |
| 本期用气量 | m³ | 本期账单用气量 |
| 图表月用气量 | m³ | 目前采用本期账单用气量，非实时月内用量 |
| 上期用气量 | m³ | 图表接口提供的上期用量 |
| 年度月均用气量 | m³ | 图表接口有效月度用量平均值 |
| 本年累计用气量 | m³ | 自然年图表有效月度用量合计 |
| 燃气表总读数 | m³ | 能源统计累计燃气表读数 |
| 能源仪表盘燃气 | m³ | 能源面板累计燃气读数 |

### 阶梯用气

| 实体名称 | 单位 | 说明 |
| --- | --- | --- |
| 本期一阶梯用气量 | m³ | 本期账单第一阶梯用量 |
| 本期二阶梯用气量 | m³ | 本期账单第二阶梯用量 |
| 本期三阶梯用气量 | m³ | 本期账单第三阶梯用量 |
| 一档总限额 | m³ | 第一阶梯独立气量额度 |
| 二档总限额 | m³ | 第二阶梯独立气量额度 |
| 一阶梯累计已用 | m³ | 第一阶梯计费周期累计已用 |
| 二阶梯累计已用 | m³ | 第二阶梯计费周期累计已用 |
| 三阶梯累计已用 | m³ | 第三阶梯计费周期累计已用 |
| 一阶梯剩余气量 | m³ | 第一阶梯剩余气量 |
| 二阶梯剩余气量 | m³ | 第二阶梯剩余气量 |
| 三阶梯剩余气量 | m³ | 第三阶梯剩余气量（以接口定义为准） |
| 一阶梯气价 | ¥/m³ | 第一阶梯有效单价 |
| 二阶梯气价 | ¥/m³ | 第二阶梯有效单价 |
| 三阶梯气价 | ¥/m³ | 第三阶梯有效单价 |
| 阶梯数据状态 | — | 阶梯数据快照状态 |

### 缴费历史

| 实体名称 | 单位 | 说明 |
| --- | --- | --- |
| 最近缴费时间 | — | 最近一笔成功燃气缴费时间 |
| 最近缴费金额 | ¥ | 最近一笔成功燃气缴费金额 |
| 最近缴费渠道 | — | 最近一笔成功燃气缴费渠道 |
| 年度缴费次数 | — | 本自然年成功燃气缴费次数 |
| 年度燃气缴费金额 | ¥ | 本自然年成功燃气缴费合计 |
| 年度业务缴费金额 | ¥ | 本自然年成功业务缴费合计 |
| 缴费历史状态 | — | 缴费历史快照状态 |
| 缴费历史更新时间 | — | 缴费历史最后成功更新的时间 |

### 账户资料与集成状态

| 实体名称 | 单位 | 说明 |
| --- | --- | --- |
| 用气地址 | — | 用气地址 |
| 燃气公司 | — | 燃气公司名称 |
| 燃气类型 | — | 燃气类型 |
| 购气方式 | — | 购气方式 |
| 户名 | — | 户名 |
| 手机号 | — | 绑定手机号 |
| 表号 | — | 表号（接口未提供时可能未知） |
| 区域 | — | 区域（接口未提供时可能未知） |
| 账户状态 | — | 账户状态（接口未提供时可能未知） |
| 集成状态 | — | 集成状态；不能替代各数据源有效性判断 |
| 数据撤取时间 | — | 本轮数据更新结束时间；不代表所有接口均成功 |

> 「数据撤取时间」为当前代码中的原有实体名称。它记录本轮更新结束时间，不代表所有接口返回有效数据。缴费历史更新时间是独立的成功时间。

### 操作按钮

| 按钮 | 说明 |
| --- | --- |
| 刷新数据 | 手动请求一次数据更新，不改变下一次自动更新计划 |
| 抓取历史记录 | 重新请求可用的历史账单，用于历史统计补录 |

## 阶梯用气数据说明

主要数据来自账单列表接口 `/bill/getGasBillList` 中的 `dataResult.consPrcInfo.consPrcDetailList`。

- 支持 `ruleCode` 为 `1/2/3` 或 `0201/0202/0203`。
- 使用 `levGqSum` 获取各阶梯实际累计用量，使用 `levGqRemain` 获取剩余量，并利用二者校验 `levGq` 的累计上限或独立额度口径。
- 本年累计用气量来自月度用气图表接口，其统计周期与阶梯计费周期可能不同，不能相互代替。
- 本期各阶梯用量来自账单详情 `gasStepList`。
- 单价优先取有效 `catPrc`；缺失时可从最新账单中对应阶梯的金额和用量计算实际结算单价。该值不一定代表当前公告价格。
- API 未提供有效数据时，不使用写死的地区限额或默认单价。

阶梯快照只在当前 HA 运行过程中保留，不跨重启持久化；缴费快照支持持久化。两个状态实体使用以下取值：

| 状态 | 含义 |
| --- | --- |
| `fresh` | 本次对应数据成功取得并解析 |
| `stale` | 当前未取得完整有效数据，使用上次有效快照 |
| `unavailable` | 尚无有效快照 |

## 缴费历史（v2.1.4+）

数据来自官方小程序的 `/mp/pay/queryPayHistory`：

- 支持普通分页以及超过请求页大小的一次性返回，识别重复或重叠页面。
- 保留同一时间的不同缴费记录，不仅按缴费时间去重。
- 燃气缴费（`chargeType=01`）与业务费（`chargeType=02`）独立统计；其他收费编码归为未知类型。
- 最近缴费及年度燃气/业务金额统计仅纳入对应类别的成功缴费记录。
- 保留真实缴费时间、金额、状态、支付渠道及收费类型。
- 只保存最近三个自然年的有效快照；使用北京时间确定年度。
- 流程失败时保留旧快照；本地文件为 `.storage/crcgas_payments_<entry_id>`，与历史账单存储独立。

缴费金额是充值或付款流水，**不等于燃气实际消费费用**。完整缴费明细不会作为大型传感器属性反复写入 Recorder。

### 已核实的缴费渠道

| payType | 渠道 |
| --- | --- |
| `0406` | 微网厅支付 |
| `0409` | 其它 |
| `0201` | 银行代收 |
| `0403` | 支付宝支付 |
| `0101` | 柜台收费 |

其他编码保留原值，按未知渠道处理。

### WebSocket API

在已登录 HA 的页面通过前端 WebSocket 调用：

```javascript
const hass = document.querySelector('home-assistant').hass;
const result = await hass.callWS({
  type: 'crcgas/payment_history',
  // entry_id: '配置项ID',
  // year: 2026,
  category: 'all',
  offset: 0,
  limit: 100
});
console.log(result);
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `type` | string | 必填，固定 `crcgas/payment_history` |
| `entry_id` | string | 仅一个有权访问的已加载配置项时可省略 |
| `year` | integer | 可选，不填则查询全部可用年份 |
| `category` | string | `all`、`gas`、`business`、`unknown`；默认 all |
| `offset` | integer | 起始偏移，默认 0 |
| `limit` | integer | 返回条数，1～200，默认 100 |

返回字段：`entry_id`、`status`、`updated_at`、`available_years`、`summaries`、`total`、`offset`、`records`、`has_more`。

记录包含 payTime、payAmount（金额字符串）、payState、payType、payTypeName、chargeType、chargeTypeName、feeType、category。年度汇总包含总记录数、成功燃气/业务缴费次数和金额、未知费用类型数量。无法确认所查询范围的总条数时 `total` 可能为 null，不能解释成无缴费记录。

该接口只读取本地缓存，不额外请求华润云端。调用者必须是 HA 管理员，或者拥有对应最近缴费金额实体的读取权限。完整协议以 [payment_websocket.py](custom_components/crcgas/payment_websocket.py) 和 [payment_storage.py](custom_components/crcgas/payment_storage.py) 为准。

## 能源面板与历史统计

在「设置 → 能源」中添加燃气来源时，可选择实际环境中存在的累计燃气量实体或统计项：

| 数据 | 实体 / 外部统计 ID |
| --- | --- |
| 累计燃气读数 | 燃气表总读数、能源仪表盘燃气 |
| 历史月度用气量 | `crcgas:monthly_gas_usage` |
| 历史月度燃气费用 | `crcgas:monthly_bill_amount` |
| 累计燃气费用 | 累计燃气费用实体 |

首次安装时尝试导入可获取的账单历史，新账单之后可进行增量统计。历史覆盖范围取决于服务端返回数据以及本地已有记录，不承诺固定起止月份或完整终身累计。原实现使用部分 SQLite 直接写入方式，请先备份 Recorder 数据库。

**不要将年度燃气缴费金额作为能源消耗费用。**

## 仪表盘卡片（可选）

仓库提供两个独立 JavaScript 卡片文件。安装集成本身不代表它们已经复制到 HA 的 `www/` 目录；如需使用，请从当前版本目录 [www/community/crcgas-card](www/community/crcgas-card/) 复制相应文件到 HA 的 `/config/www/community/crcgas-card/`，再在仪表盘资源中以 JavaScript 模块方式添加。

| 文件 | 资源地址 |
| --- | --- |
| crcgas-statistics-card.js | `/local/community/crcgas-card/crcgas-statistics-card.js` |
| crcgas-card.js | `/local/community/crcgas-card/crcgas-card.js` |

统计卡片支持年度对比、图表切换和有效阶梯进度；费用使用实际历史统计，缺少数值时不以当前燃气单价估算历史费用。实体前缀默认尝试自动识别，也可显式配置。

```yaml
type: custom:crcgas-statistics-card
title: 燃气统计
entity_prefix: sensor.hua_run_ran_qi
```

基础卡片示例：

```yaml
type: custom:crcgas-card
title: 华润燃气
entity: sensor.hua_run_ran_qi_ran_qi_zhang_hu_yu_e
entity_prefix: sensor.hua_run_ran_qi
```

示例中的实体 ID 和前缀需按实际环境调整。已有的第三方卡片（如 LUXE）可继续读取原实体，缴费历史弹窗则需要第三方卡片适配上述 WebSocket API；本仓库不包含 LUXE 卡片。

## 数据来源与隐私

| API 路径 | 用途 |
| --- | --- |
| `/mp/pay/queryArrears` | 余额及欠费 |
| `/mp/pay/queryPayHistory` | 缴费记录 |
| `/bill/getGasBillList` | 账单列表及阶梯信息 |
| `/bill/getGasBillList4Chart` | 月度用气图表 |
| `/bill/getBillDetail` | 账单详情 |
| `/binding/getMpBindingCons` | 绑定户号及账户信息 |
| `/public/doRefreshToken` | Token 刷新 |

业务接口按更新流程依次获取，部分请求依赖前序响应，缴费或历史账单可能产生分页请求。接口属于第三方小程序服务，返回格式与可用范围可能随地区或服务端版本变化。

认证配置、手机号、户名、用气地址、账单及缴费明细均涉及私人信息。请限制 HA 用户访问权限，并在分享备份、日志或诊断内容前脱敏。

## 故障排查

| 现象 | 排查方法 |
| --- | --- |
| Token 验证失败 / 会话超时 | 获取有效的三个认证字段，使用重新配置，不要直接删除集成 |
| 阶梯数据未知 | 查看阶梯数据状态及账单列表响应中的阶梯字段；不同地区可能存在未兼容格式 |
| 缴费明细为空或旧数据 | 检查缴费历史状态、成功更新时间以及 WebSocket 的 available_years / total |
| 数据长期不更新 | 检查更新模式、HA 时区、网络与 crcgas 日志；尝试手动刷新 |
| 卡片显示未知 | 核实 HA 实际实体 ID、前缀及资源是否已加载最新文件 |
| 历史统计缺失 | 先备份数据库，再检查 Recorder 设置、数据库类型及历史账单导入日志 |
| HACS 无法安装 / 更新 | 确认仓库地址为本 fork、类别为集成，并查看安装或更新日志 |

## 开发与回归测试

```bash
python -m compileall -q custom_components/crcgas
python -m unittest discover -s tests -v
node --test tests/test_cards.cjs
```

v2.1.5 发布提交通过了 23 项 Python 单元测试、4 项 JavaScript 测试及 Hassfest 检查。测试覆盖数据解析、分页、定时规则及定时器生命周期等。实际 HA 页面、全部地区接口及长期运行仍需要实机验证。HACS 校验在发布时因仓库未启用 Issues 和缺少有效 Topics 未通过，详见 [Actions](https://github.com/Boxzhi/ha-crcgas/actions)。

## 更新日志

完整记录见 [CHANGELOG.md](CHANGELOG.md) 与 [Releases](https://github.com/Boxzhi/ha-crcgas/releases)。

## 上游项目与致谢

- 原项目：[C3H3-AI/ha-crcgas](https://github.com/C3H3-AI/ha-crcgas)
- 当前维护分支：[Boxzhi/ha-crcgas](https://github.com/Boxzhi/ha-crcgas)
- 感谢原作者及社区贡献者。本项目为非官方开源集成，与华润燃气、微信及 Home Assistant 官方无隶属关系。

## 许可证

[MIT License](LICENSE)。保留上游版权及许可声明。
