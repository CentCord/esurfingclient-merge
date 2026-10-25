# ESurfingClient 多拨、保活与重连设计审计

> 范围：只读审计当前 `fix/probe-mark`（基于 v2.2.0-r4，HEAD 为 v2.2.0-r6 探测修复版）及用户提供的 esurface 逆向资料、net-docs 实测运维资料。本文是分析与方案，不代表文中建议已经实现。
>
> 证据口径：逆向文档所述 Windows/macOS 官方客户端行为是特定学校、版本和会话的实证，不应泛化成所有天翼服务端的强制规则。所谓“服务端判定”在无服务端代码时只按客户端协议字段和错误码归纳为证据/推断。

## 1. 结论摘要

1. 官方客户端会话有两种保活层次：本机约 30 秒 pulse/excheck 自检，以及服务端约 300 秒 keep CGI；C 实现只做后者，并依赖登录/心跳响应给出的 `keep-retry`/`interval`。
2. 服务端可见的信号包括 ticket、设备/网络指纹、心跳、反共享 `shared/sproof` 与 excheck 状态。账号异地登录、终端数超限、共享限制、登录频率限制均可由错误码/错误文案直接观察到。单纯 HTTP 探测不能证明某个账号会话仍有效；它只说明当前 mark 对应路径是否呈现互联网/门户状态。
3. 今天的主因已由 `4be78f5` 修复：OpenWrt 网络探测使用账号 mark。此前探测跨账号错路由，可能把所有账号共同判为认证或令某账号误认为在线。修复解决选路串线，但仍没有多账号级认证调度、随机抖动或跨进程互斥。
4. C 代码已有认证失败递增退避和错误分类；对 transient 的分钟序列固定为 1/3/5/10/20/30 且无限持续，对 slow 错误固定采用末档；线路探测错误则固定 10 秒最多 5 次后退出。认证/心跳、探测超时、探测周期等多处仍写死。
5. 建议将重试、探测、心跳、认证错峰、会话失效等待作为根级 `runtime` 配置，默认兼容当前服务端下发间隔；策略层保持“控制面故障/线路故障/会话失效”分开，避免网络探测的单次结果直接触发登出和重新认证。

## 2. 文档知识提取：会话和多拨

### 2.1 官方客户端保活机制

《保活机制.md》《真实会话客户端环境分析.md》《技术细节总账与遗留问题.md》共同记录以下实证：

- **双层心跳**：Windows PortalServer 的本地 pulse 由 `plusInterval=30` 秒触发，执行 ping/UDP hello 等 excheck；服务端 keep CGI 周期通常为 300 秒。真实会话 `svrinfo.log` 有 253 次 keep 成功记录，抓包响应为 `<interval>300</interval>`。
- **动态周期**：登录响应提供 `keep-retry`，心跳响应可提供 `interval`。官方 Windows 会话里 `keepRetry=5` 是重试上限，而 `interval=300` 是成功心跳的周期；两者不是同一个参数。macOS SDK 对空 interval 的缺省记为 150 秒。不同客户端实现存在差异。
- **请求语义**：Windows keep 携带 ticket、excheck-args（hello/conn）、各模块 excheck-resp、shared/sproof 及 IPv4/IPv6/MAC 等网络指纹。macOS 请求较简化，仍含 ticket 与客户端标识等信息。
- **失败处置**：逆向记录 Windows keep errorCode=1 在 keepRetry 上限内重试，网络错误（-2/-3）转重认证；模块更新及状态重置码走专门处理。macOS keepFailed 按 keep-retry 重试或通知 UI 重认证。抓包/逆向文档把超限后的典型流程概括为 term→ticket→state→auth。
- **重要限制**：C 版当前并未实现 Windows pulse/excheck、shared/sproof，也未实现官方客户端同等级的设备活性证明；不能把官方机制简化成“每 N 秒发一次 HTTP”就视为等价。官方具体字段可能依通道/学校而变化。

### 2.2 服务端“异常/共享/踢下线”判断依据

| 信号 | 证据与判断 | 置信度 |
|---|---|---|
| ticket 连续有效心跳 | keep 请求以 ticket 维持会话，成功响应继续下发 interval；长期无有效 keep 会失去在线状态 | 协议机制有实证；精确服务端超时窗口未确定 |
| 同账号其他位置登录 | C 项目错误表包含 `401`（账号已在别处登录）及 `20010009`（网络断开或账号别处登录） | 错误语义有代码记录；具体检测算法未知 |
| 并发终端数/套餐限制 | `13014000` 对应终端数超限 | 错误码映射证据 |
| 反共享/共享限制 | `-600` 对应“检测到网络共享行为”；Windows keep 字段有 `shared/sproof`，`against-interval=10` | 字段和错误文案有实证；具体判定规则（IP/MAC/流量/终端拓扑权重）未解 |
| 登录频率限制 | `11064000` 为“登录过于频繁”；多账号同秒重新认证会形成高频突发，可能触发账号/IP/AC等维度限流 | 错误码实证；限流键和窗口长度未知，不应假定为单一 IP 规则 |
| 网络/设备指纹变化 | keep 可包含 MAC、IPv4/IPv6、local-time、excheck 状态；真实会话中 AC IP 稳定而客户端 IP 随会话变化 | 字段实证；哪些字段参与风控及阈值属于推断 |
| 缺失 excheck/客户端活性 | Windows 每 30 秒执行 pulse，keep 携带子模块结果；客户端逆向明确将其用于确认反检测组件活性 | 官方客户端设计意图有文档证据；服务端是否单独拒绝每一缺失字段未知 |

因此，应把 **探测到门户（302）**、**外网可达（204/301）**、**账号会话有效（keep 成功）** 作为三个不同观察值。外网探测失败可能是线路故障、目标服务故障或过滤，不等于账号失效；探测 204 也可能由错误线路/错误会话提供，必须先保证 mark 选路。

### 2.3 Python 版可借鉴设计（基于 esurface 文档）

材料对 Python 客户端的重点是协议闭环及诊断能力，不是一个已验证的多进程守护实现；建议借鉴以下原则，而非照搬特定实现：

- **显式会话状态机**：门户检测、模块/ticket、state、auth、keep、term 分阶段；为每步记录结果、错误码和 ticket 生命周期，减少“认证失败”大类误判。
- **按错误分类恢复**：区分不可重试配置错误、认证服务器暂时错误、网络不可达、会话失效、服务端慢速限制；慢速/频率限制不可继续短间隔重试。当前 C 项目的 fatal/slow/transient 分类方向值得保留。
- **动态模块/周期来自服务端**：Python 文档强调模块轮换需追模块、服务器给 keep 周期；不应把服务端下发 interval 与本地建议周期混为一谈。keep 周期默认应尊重服务器值，并只允许配置安全上限/下限或明确的 override 模式。
- **可观测性**：保存 `--check`/`--logout` 类诊断路径、错误码、各阶段耗时、线路标识、探测结果；会话生命周期中记单调时钟。不要在日志中输出密码、完整 ticket 或可复用密钥。
- **多网卡选择**：Python 早期实现支持接口选择（`--iface`），说明探测和认证都要绑定正确出口。此项目按账号 fwmark 做同一件事；所有探测、DNS/连接检测及认证/心跳 socket 都应使用同账号 mark。
- **多账号安全默认值**：文档本身没有证据表明 Python 客户端有全局跨进程锁、账号错峰或分布式退避。应把这一点列为新增设计要求，而非声称 Python 已经解决。

## 3. C 项目缺陷审计

以下行号针对当前 HEAD `c972582`，使用仓库相对路径。`4be78f5` 的探测 mark 修复已在当前源码中体现。

### 3.1 重连与退避

- `esurfingclient/app/src/dialer/DialerClient.c:43-50, 222-242`：认证重试序列硬编码为 1/3/5/10/20/30 分钟；slow 错误直接取最后一档 30 分钟。序列耗尽后固定 30 分钟无限重试，没有用户可选的最大次数/无限开关/最大间隔/抖动。
- `.../DialerClient.c:252-263`：线路状态 `STATUS_ERROR` 固定 10 秒重试，超过 5 次即 `RUN_FAILED`。对短暂外网/认证服务器异常，行为与认证 transient 退避不一致；OpenWrt auth 进程退出后交给 procd 的 respawn，实际频率又受 init 脚本策略影响。
- `.../DialerAuth.c:368-371, 391-399`：认证进程失败退出由外部监管者重启；异常快速返回的兜底固定睡 5 秒。各账号进程独立启动时没有共享的重试预算。
- `.../DialerNet.c:184-199, 274-342`：登出和获取“需要认证”状态路径分别固定最多 5 次、每次 1 秒；网络等待成功轮询固定 3 秒。`DialerLocation.c` 还存在固定 10 秒/1 秒轮询和 5 次上限（约 103-124 行）。
- 以上 `sleep_ms(..., true)` 可被停止/重启请求打断，这是良好的可维护行为；新参数必须保留可中断等待。

### 3.2 会话失效与线路故障分离不足

- `esurfingclient/app/src/net/CheckNetwork.c:242-243, 309-324, 338-363`：通用 HTTP 探测仅按 204/301 判在线、302 判需要认证；两个互联网目标都无结果后再探认证服务器。它观察的是路径/门户响应，不是账号 ticket 的有效性。
- `.../DialerClient.c:140-145, 191-198`：认证状态机按探测的 `STATUS_NEED_AUTH` 进入 reset/auth；当前实现未见连续 N 次同状态确认或“线路可达后再做会话有效性核验”的独立阶段。瞬态重定向或探测误判可触发注销/重认证放大。
- `.../DialerNet.c:212-237`：心跳失败返回 false，外层统一最多重试 5 次、每次间隔 1 秒；重试耗尽以 `RUN_FAILED` 退出（`DialerClient.c:167-180`），没有把明确的会话失效响应与超时/连接失败分别采取策略。`dialer_request_plain` 中具体错误码虽有记录，策略入口仍偏粗。
- 建议结果模型至少包含 `INTERNET_OK`、`PORTAL_REDIRECT`、`AUTH_SERVER_REACHABLE`、`PATH_ERROR`、`KEEP_OK`、`SESSION_INVALID`、`KEEP_TRANSIENT_ERROR`，并且仅服务端明确会话失效码或连续独立证据才触发重新认证。

### 3.3 多账号协调和错峰

- `esurfingclient/app/include/config/Config.h:63-65`（注释）及 `esurfingclient/app/src/dialer/DialerAuth.c:360` 显示 OpenWrt 每账号一个认证进程实例。重试计数、`wait_auth_retry()` 等均在各自进程内（`DialerClient.c:104-122, 126-127`），没有共享的跨进程锁/速率协调器。
- 账号认证成功/失败后的时间序列相同且无随机抖动（`DialerClient.c:49, 234-242`）；若同一时刻因探测/线路波动进入 NEED_AUTH，各进程可以同秒 reset、term、ticket/auth。当前 mark 修复消除错误选路导致的公共误判来源，但不负责错峰。
- Web 后台探测线程也采用固定周期（`CheckNetwork.c:35-36, 410-434`）；这和每个 auth 进程的探测共同产生规则性请求。探测本身不登录，但状态跃迁仍可能同步触发认证。
- 适合 OpenWrt 的最小方案：各账号在本地记单调随机初始抖动 + 错误后 full-jitter；更可靠方案由 init/单一协调器分配认证时隙，并用 `/var/run/esurfingclient/` 下基于账号/设备的锁和令牌桶控制单位时间认证数。单纯进程内 mutex 无法协调独立进程。

### 3.4 硬编码/配置缺口

- 已配置的 `conn_timeout`/`op_timeout` 在 `esurfingclient/app/src/config/Config.c:477-478` 读取、用于 `NetClient.c:163-165, 293-296`；默认值来自 `Config.h`。但探测单独固定 3000ms/4000ms、后台间隔 2000ms（`CheckNetwork.c:24-39`），所以修改 HTTP 超时不会影响探测。
- 认证/网络固定值集中在 `DialerClient.c:49, 104-120, 167-180, 252-263`、`DialerNet.c:184-199, 277-342`、`DialerAuth.c:391-399`、`DialerLocation.c:103-124`。此外服务器地址、探测目标 IP、目标数量和 HTTP 状态码策略在 `CheckNetwork.c:13-16, 71-81` 写死；建议把高频策略值配置化，网络协议端点/固定 IP 则作为程序版本兼容常量或可选高级覆盖，不宜普通用户随意配置。
- `ConfigDefaults.c:31-47` 当前根级默认表只有启用、日志、HTTP 超时、Web 端口；尚无 retry/keep/probe/session 参数。配置缺失补默认的基础设施已存在（`ConfigDefaults.c:75-145`），适合扩展。

### 3.5 其他风险与优点

- **优点**：服务端 keep interval 被解析并减去操作超时余量（`DialerNet.c:218-237, 251-270`）；认证时间与心跳 tick 使用单调钟（`DialerClient.c:158-165`）；错误分类表明确含 11064000 slow、401 账号异地、-600 共享限制（`ErrorCode.c:29-35, 76-81`）；OpenWrt探测已配置 `open_socket_callback`（`CheckNetwork.c:166-183`）。
- **心跳周期的可控性**：服务端 interval 当前直接成为周期减余量；建议只增加“respect_server / override”模式，默认尊重服务端。若允许覆盖，必须限制在服务端值附近或明确显示风险，避免过长造成掉线、过短造成服务端压力。
- **缺乏探测失败容忍**：探测返回值单轮即进入状态机，未见专用 consecutive-failure threshold。Web 缓存状态也是单次探测覆盖（`CheckNetwork.c:414-418`）。应分别配置“连续失败转线路故障”和“连续门户重定向才确认失效”，不要用单一阈值混合状态。
- **测试边界**：本次按要求不运行测试。审计只读代码、配置、文档和 git 历史；提交前应另行验证新配置的边界值与多进程错峰行为。

## 4. 参数体系设计

### 4.1 配置结构原则

所有参数放根级 `runtime` 对象，作用于本机所有账号；不要把调度参数放到每个账号里，以免四份值漂移。例：

```json
{
  "runtime": {
    "reconnect": {
      "infinite": true,
      "max_attempts": 0,
      "backoff": "sequence",
      "intervals_sec": [60, 180, 300, 600, 1200, 1800],
      "base_sec": 60,
      "multiplier": 2.0,
      "max_sec": 1800,
      "jitter_sec": 30,
      "line_error_max_attempts": 0,
      "line_error_interval_sec": 10
    },
    "auth": {
      "connect_timeout_sec": 10,
      "request_timeout_sec": 15,
      "stagger_sec": 20,
      "session_invalid_wait_sec": 60
    },
    "keepalive": {
      "interval_mode": "server",
      "interval_sec": 300,
      "min_interval_sec": 30,
      "max_interval_sec": 1800,
      "failure_retries": 5,
      "retry_interval_sec": 1
    },
    "probe": {
      "interval_sec": 2,
      "connect_timeout_ms": 3000,
      "request_timeout_ms": 4000,
      "failure_tolerance": 3,
      "portal_confirmations": 2
    }
  }
}
```

### 4.2 参数清单

默认值以兼容当前实现和实测（server keep 常见 300s）为目标；范围是推荐校验范围。函数名按当前结构指向，未来可拆配置读取和策略模块。

| 参数名 / JSON 键 | 类型；默认；范围 | 作用 | 使用位置（建议） |
|---|---|---|---|
| 最大认证重试次数 `runtime.reconnect.max_attempts` | 整数；`0`；0–1000，0 表示不设上限 | 控制认证失败重试预算 | `Config.c` 解析；`DialerClient.c:run()` 认证失败分支 |
| 无限重试 `runtime.reconnect.infinite` | 布尔；`true`；true/false | 明确无限重试；false 时达到 max_attempts 后暂停并告警。与 max_attempts=0 的语义需校验一致 | 同上 |
| 退避策略 `runtime.reconnect.backoff` | 枚举；`sequence`；`fixed`/`exponential`/`sequence` | 固定间隔、指数退避或自定义序列 | `DialerClient.c:run()`/纯函数 `next_retry_delay()` |
| 自定义序列 `runtime.reconnect.intervals_sec` | 正整数数组；`[60,180,300,600,1200,1800]`；1–86400 秒，最多 32 项 | sequence 策略的间隔，最后一项封顶复用 | 同上，替代 `DialerClient.c:49` |
| 固定间隔 `runtime.reconnect.fixed_interval_sec` | 整数；300；1–86400 | fixed 策略间隔 | 同上 |
| 指数初始间隔 `runtime.reconnect.base_sec` | 整数；60；1–3600 | exponential 起点 | 同上 |
| 退避倍数 `runtime.reconnect.multiplier` | 浮点；2.0；1.1–5.0 | 每次失败的倍数 | 同上 |
| 最大退避 `runtime.reconnect.max_sec` | 整数；1800；1–86400 | 所有策略的最大等待上限 | 同上 |
| 随机抖动 `runtime.reconnect.jitter_sec` | 整数；30；0–3600 | 每账号额外随机错开，默认应使用 full jitter 或对称 jitter 并避免负等待 | `DialerClient.c:wait_auth_retry()`；OpenWrt 多进程时用安全随机数 |
| 线路错误重试上限 `runtime.reconnect.line_error_max_attempts` | 整数；0；0–1000，0 无限 | 代替线路错误固定 5 次退出；线路恢复后重置计数 | `DialerClient.c:252-263`、`DialerNet.c:wait_need_auth()` |
| 线路错误间隔 `runtime.reconnect.line_error_interval_sec` | 整数；10；1–3600 | 当前固定 10 秒；线路错误建议也用退避并抖动 | `DialerClient.c:252-263` |
| 认证连接超时 `runtime.auth.connect_timeout_sec` | 整数；10；1–120 | connect timeout；作为现有 `conn_timeout` 的兼容别名/迁移目标 | `NetClient.c` get/post；`Config.c` |
| 认证请求总超时 `runtime.auth.request_timeout_sec` | 整数；15；1–300 | 请求整体超时；对应现有 `op_timeout` | `NetClient.c` get/post；`Config.c` |
| 认证错峰 `runtime.auth.stagger_sec` | 整数；20；0–600 | 首次启动按账号序号错开（账号序号偏移 + 抖动）；不建议每次重启都完全同步 | OpenWrt init 调度/`DialerAuth.c` 进入 auth 前 |
| 会话失效最小等待 `runtime.auth.session_invalid_wait_sec` | 整数；60；0–86400 | 明确 SESSION_INVALID 后等待再认证；频率限制响应应由 slow 错误更长退避覆盖 | `DialerClient.c` 错误分类到重认证的转换点 |
| 心跳周期模式 `runtime.keepalive.interval_mode` | 枚举；`server`；`server`/`fixed`/`clamped` | server 尊重服务端下发；fixed 使用用户周期；clamped 使用服务端周期但限于 min/max | `DialerNet.c:218-237, 251-270` |
| 固定心跳周期 `runtime.keepalive.interval_sec` | 整数；300；30–3600 | 仅 fixed 模式使用；默认展示建议值，不覆盖服务端 | `DialerClient.c:156-165` 周期判定 |
| 心跳周期下限 `runtime.keepalive.min_interval_sec` | 整数；30；10–3600 | clamped 模式下限，防止过快心跳 | `DialerNet.c` interval 解析 |
| 心跳周期上限 `runtime.keepalive.max_interval_sec` | 整数；1800；60–7200 | clamped 模式上限，避免过久不 keep | `DialerNet.c` interval 解析 |
| 心跳失败重试次数 `runtime.keepalive.failure_retries` | 整数；5；0–20 | 取代 `DialerClient.c:167-180` 固定 5 次；0 表示不重试，不能表示无限 | `DialerClient.c:run()` |
| 心跳失败重试间隔 `runtime.keepalive.retry_interval_sec` | 整数；1；1–300 | 当前固定 1 秒；应采用轻量指数/抖动，避免多账号同步打 keep | `DialerClient.c:167-180` |
| 探测间隔 `runtime.probe.interval_sec` | 整数；2；1–3600 | Web 后台及 auth 探测节奏需分别支持 override 或共用配置 | `CheckNetwork.c:35-36, 410-434`；认证轮询等待点 |
| 探测连接超时 `runtime.probe.connect_timeout_ms` | 整数；3000；250–30000 | 替代 curl 探测连接固定超时 | `CheckNetwork.c:24-33, probe_add()` |
| 探测总超时 `runtime.probe.request_timeout_ms` | 整数；4000；500–60000 | 替代探测总超时与 deadline 常量 | `CheckNetwork.c:24-33, 281-289` |
| 探测失败容忍次数 `runtime.probe.failure_tolerance` | 整数；3；1–20 | 连续线路错误达到阈值才标记线路故障；不直接重认证 | `CheckNetwork.c:probe_group()` 调用方状态聚合 |
| 门户确认次数 `runtime.probe.portal_confirmations` | 整数；2；1–10 | 连续观察到 302 才确认需要认证；与线路错误计数独立 | `DialerClient.c:run()` 状态判定前 |
| 会话确认心跳次数 `runtime.keepalive.invalid_confirmations` | 整数；1；1–5 | 如协议响应可辨认会话失效，要求明确码；模糊错误不计为失效，网络错误按 transient 处理 | `DialerNet.c:dialer_request_plain()` 与 `DialerClient.c` 状态转换 |
| 本地 pulse `runtime.keepalive.local_pulse_interval_sec` | 整数；30；5–300 | 仅当未来实现 excheck/pulse 才生效；当前 C 版无 pulse，UI 应标为“预留/未支持”或暂不暴露 | 新增 pulse 模块，不对应现有使用点 |

**语义要求**：`max_attempts=0` 与 `infinite=true` 不允许互相矛盾；建议以 `infinite` 为权威开关，有限时 max_attempts 必须 ≥1。区分“会话失效等待”和错误码 11064000 的慢速限流等待：后者应按 `AUTH_ERROR_SLOW` 单独设置保守间隔，不能只等默认 60 秒。

### 4.3 建议状态流

```text
每账号/mark 探测
  ├─ 204/301 → 路径可上网；若已有会话则按服务端 keep 周期维护
  ├─ 302 连续达到 portal_confirmations → 候选需认证
  │    └─ 最小等待 + 全局/跨进程错峰 → 仅清理本账号旧会话 → 认证
  └─ 超时/不可达连续达到 failure_tolerance → 线路故障
       └─ 保留会话上下文，不登出；按线路退避重试

keep 请求
  ├─ 成功 → 采用服务端 interval（经策略边界处理）
  ├─ 明确 session-invalid 错误 → 最小等待、错峰后重认证
  ├─ slow/rate-limit → slow 退避，停止短周期登录
  └─ timeout/连接失败 → 判为 keep transient / 路线问题，不等同会话失效
```

全局协调建议补一层持久化轻量调度：按路由器生成节点 ID、账号 index 生成稳定相位（避免每次启动随机碰撞），每次实际认证前用 flock 令牌桶预约时隙；重启退避状态最好存入 `/var/run`（重启丢失可接受）或 `/var/lib`（跨重启保留），避免服务反复重启清空退避。

## 5. LuCI 界面设计

### 5.1 新版 JavaScript 页面

文件：`luci-app-esurfingclient/rootfs/www/luci-static/resources/view/esurfingclient.js`。建议把全局认证参数放主配置表单的“高级设置”折叠区，避免和账号表混在一起；账号行只保留账号、通道、mark、时间窗口。

| 分组 | 控件 | 中文说明建议 |
|---|---|---|
| 重连与限流 | checkbox 无限重试；select 退避策略；number 最大次数/固定间隔/初始间隔/倍数/上限/抖动；可增删的序列输入框 | “认证失败后等待多久再试。服务端提示登录过于频繁时会自动进入慢速退避；多个账号会按账号错峰并增加随机抖动。” |
| 多账号认证 | number 首次认证错峰（秒）、会话失效最小等待（秒） | “账号按配置顺序错开认证，减少同时登录请求。运行中线路故障不会触发该认证队列。” |
| 连接超时 | number 连接超时、请求总超时 | “只控制认证/心跳 HTTP 请求；探测超时在探测分组单独设置。”保留现有 conn_timeout/op_timeout 兼容显示或迁移。 |
| 保活 | select server/fixed/clamped；fixed 周期、最小/最大周期；失败重试数/间隔 | “默认使用认证服务器下发的心跳间隔。覆盖服务器值可能导致掉线或额外请求。”注明 C 版目前不提供官方 Windows 的本地 excheck pulse。 |
| 网络探测 | number 间隔、连接超时、总超时、连续失败容忍、302 确认次数 | “探测按每个账号 mark 选路；线路不可达与需要认证分别累计，不会因单次超时直接登出。” |
| 调试/兼容 | 若有 pulse 字段，仅在能力实现后开放；否则隐藏 | “当前实现只发送协议 keep 心跳；本地 UDP hello/excheck 尚未实现。” |

实现约束：JS model 应增加 `runtime` 默认对象、读写/校验/序列化路径；动态控件随策略显隐；范围错误要在表单旁说明，不默默截断。保存后沿用现有“保存并应用”重启行为。不要把 server interval 的只读观测值写回配置作为用户 override。

### 5.2 旧版 htm 页面

文件：`luci-app-esurfingclient/rootfs-legacy/usr/lib/lua/luci/view/esurfingclient.htm`。沿用现有连接超时、操作超时的输入与 JS 配置对象，新增一个 `<fieldset>`“重连与网络”并用 `<legend>` 分组。使用 `type=number`、`min/max/step`；退避策略使用 `<select>`；无限重试使用 checkbox；序列使用逗号分隔文本输入（保存时解析并校验）。保活模式用 select，server 模式下禁用固定周期输入；探测参数使用单独 fieldset，避免用户误以为 conn_timeout 会控制探测。

旧版页面需同步完成：默认 JSON 模板、读取配置 fallback、控件 change 处理、保存 JSON、编辑/重载时回填、错误提示。新版 JS 与旧 htm 必须共享同一 JSON 键名和默认值。建议页面说明简短而直白：

- “认证重试：失败后按所选退避策略等待。登录频率限制会使用慢速等待。”
- “账号错峰：按配置顺序错开启动认证；秒数为账号之间的基础间隔。”
- “心跳周期：默认遵从认证服务器返回值；固定周期仅供排障使用。”
- “探测容忍：连续达到次数后才判为线路故障。线路故障不会直接清空会话。”

## 6. 资料来源

- `esurface/docs/保活机制.md`：官方 Windows/macOS 双层保活、keep 字段和错误处理。
- `esurface/docs/真实会话客户端环境分析.md`：真实会话中的 `plusInterval=30`、`keepRetry=5`、253 次 keep 成功及 interval=300 抓包。
- `esurface/docs/WindowsUA链路分析.md`：当前主要是 UA/ZSM 动态模块链路；对会话保活仅作旁证，不将算法内容作为本设计依据。
- `esurface/docs/事件复盘.md`：Python 实现认证流程、模块轮换和现场测试背景；没有证明 Python 已解决跨进程多账号协调。
- `esurface/docs/技术细节总账与遗留问题.md`：协议、keep、反共享字段与未知项（sproof 算法、level 含义等）。
- `esurface/docs/开源项目对比分析.md`：Python esurface 的接口选择、诊断工具与认证能力差异。
- `net-docs/01-架构与原理.md`、`02-配置清单.md`、`03-踩坑记录.md`、`04-运维手册.md`、`05-性能实测.md`、`06-SQM流量整形.md`、`07-内存优化与五路.md`：本部署环境配置/踩坑/实测背景；多拨 mark 隔离、性能和运行维护策略。
- 当前仓库 `4be78f5`：探测设置 SO_MARK，修复各账号探测误走主线路造成的错误状态共振。
