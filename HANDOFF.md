# ESurfingClient 下一轮交接

> 本文件是下一轮工作的唯一上下文入口。基于仓库 HEAD `7e4c8a9`（`main`，与 `origin/main` 一致）；规格原文在根目录未跟踪文件 `SPEC-REFACTOR.md`。下一轮先读本文件，再按任务需要查源码和规格。当前任务只要求压缩上下文；本文件之外没有做代码或构建操作。

## 项目目标与多拨架构

OpenWrt 校园网多账号多拨：一物理 WAN 接宿舍网络，通过 macvlan/虚拟交换机建立多个虚拟网卡（如 `wan2_dev/wan3_dev/wan4_dev`），每账号有独立 MAC、DHCP/地址、认证会话、ticket、心跳与线路探测。mwan3 根据账号 `fwmark` 将流量分到各自虚拟网卡，多条线路可叠加带宽。现实部署观测为 4 个账号叠加约 340–360 Mbps。

每账号 auth 进程用 `--role auth --account N` 只加载并拥有一个账号；不能共享另一账号的探测结果、错误计数、隔离计时或会话状态。mark 选路由客户端用 `SO_MARK` 完成；线路控制只在显式配置该账号 `iface` 时直接操作指定虚拟接口，不承担路由/线路调度。

## 已完成阶段与提交

| 提交 | 工作 | 验证程度 |
|---|---|---|
| `94e64ef fix(probe): 显式传递账号 mark 修复探测选路` | 新增 `docs/ARCHITECTURE.md`；探测 API 显式接收账号索引，SO_MARK callback 使用显式 userdata context，不再依赖 TLS 索引；保留 VERBOSE callback 诊断日志。 | OpenWrt VM 实测：4 个假账号，nft 计数器 dummy0=34、wan2_dev/wan3_dev/wan4_dev 各 18；确认每账号探测 SYN 按自己的 mark 走接口。 |
| `4528847 feat(config): 落地 runtime 参数与认证重试策略` | runtime 默认值/解析/范围校验；认证退避与上限、启动错峰、心跳周期与重试、探测参数和旧超时迁移接入；更新 `ANALYSIS.md`。 | 修改源文件通过 OpenWrt 宏下 GCC `-Wall -Wextra -Werror -fsyntax-only`；完整 CMake 构建未执行（环境无 cmake）。没有明确的阶段 2 参数行为 VM 验收记录。 |
| `7e4c8a9 feat(multidial): add isolated line control` | `iface` 配置、线路 down/up/恢复退避、账号级限流隔离与 fatal 暂停、状态文件和七态转换日志；更新架构/分析文档及 rpcd ACL。 | OpenWrt 宏下阶段 3 涉及源文件语法检查通过、`git diff --check` 通过；未在 VM 实测线路 down/up、隔离或停止恢复。 |

写入本文件之前，`git status --short --branch` 为 `main...origin/main`，唯一未跟踪文件是用户提供的 `SPEC-REFACTOR.md`。本轮新增的唯一文件是 `HANDOFF.md`；未触碰该规格文件。

## 关键结论与踩坑

### 探测 mark 根因与修复

- OpenWrt `work_auth()` 在启动期先调用 `wait_need_auth()` 探测线路，早于 `dialer_app()` 设置 `tl_thread_idx`。socket callback 原来仅从 TLS 读取 mark；启动探测时 TLS 索引是 `-1`，因保护分支跳过 `setsockopt(SO_MARK)`。因此 `log_lv=6` 下看不到 SO_MARK 日志，探测 SYN 都走主线路。这是实际根因，不是补丁没编入。
- 修复：`check_network_status(connect_only, account_index)` 显式传账号索引；每个 probe 的 mark context 通过 `CURLOPT_OPENSOCKETDATA` 传给 callback。GET/POST 和认证服务器测速也传稳定生命周期的 context。
- 代码：`esurfingclient/app/include/net/NetClient.h:37`；`src/net/CheckNetwork.c:83, 165–184, 340`；`src/net/NetClientCurl.c:90–119`；调用点 `src/dialer/DialerClient.c:196`、`DialerLocation.c:98`、`DialerNet.c:335`。VERBOSE 诊断日志刻意保留。

### 网络检测与会话判断

- TCP connect/握手成功不等于能上网；未认证状态下公网 TCP 握手也可能成功。
- 会话死亡后已有 TCP 长连接仍可能续传/续播；长连接存活不证明 ticket/会话有效。
- ICMP 不可靠；不要将 mwan3 `track_ip` 改成公网 ICMP 目标。
- 204/301、302、路径错误、keep 心跳成功/失败是不同观测。探测回答线路/门户现象，不应独立宣告认证会话有效。阶段 3 对已认证账号遇到 portal 探测会再发 keep 确认；连续 keep 失败才进入 session invalid。
- 当前阶段 3实现：`DialerClient.c:157–221, 228–370`。实际认证响应的 11064000 和 fatal 分类经 `LineControl.c:134–153` 处理。

### mwan3 与线路失效

- mwan3 可在接口 link down 时摘除该线路规则/路由；会话失效但接口仍 up 时它不知道会话死了，仍可能向死线路发流量。
- 未匹配 mark 的流量会落到主路由表；主表默认路由指向死线时可能把 DNS/其他流量一起黑洞，用户表现为全网无互联网。
- 已有 conntrack 连接仍按旧线路固定，需要 flush conntrack 才会重新选路。客户端阶段 3不直接执行 conntrack 清理，也不改 mwan3 规则。
- `ip link set ... down/up` 是否能在目标设备上及时触发 netifd/mwan3 状态变化，尚待阶段 3 VM 验收。

### Python/esurface 可借鉴点

`/Users/orpheus/Desktop/天翼校园网/esurface` 及其 docs 可作为协议背景：按 ticket/state/auth/keep/term 阶段追踪状态，细分网络、会话、致命错误，保留诊断数据和错误码；早期 Python 版有显式接口选择能力。材料没有证明 Python 版实现了可靠的跨进程账号协调，不要把这一点当作现成方案。官方客户端还存在本机 pulse/excheck/shared/sproof 等机制，当前 C 客户端并未等价实现。

## 当前代码结构与接入点

| 目标模块 | 当前代码边界/入口 |
|---|---|
| config | `app/src/config/Config.c` 账号加载、字段迁移；`ConfigDefaults.c` 默认值、runtime 解析和范围校验；共享结构位于 `app/include/core/States.h`。runtime loader 在 `Config.c:510`。 |
| session | `DialerAuth.c` 登录流程；`DialerSession.c` reset/clean；`DialerTicket.c` ticket；`DialerNet.c` keep/term；`DialerClient.c` 主状态循环、退避与认证决策；`ErrorCode.c` 错误分类。 |
| probe | `app/src/net/CheckNetwork.c`，显式 account index 和按账号 mark；`STATUS_PENDING` 与线路/门户连续计数在这里处理。 |
| policy | 暂未拆成独立模块；主要还在 `DialerClient.c` 与新 `LineControl.c`。阶段 1 架构文档列出的 policy 接口是目标边界，不是已实现 API。 |
| netio | `NetClient.c`、`NetClientCurl.c`、`NetClientInternal.h`；curl socket callback 按传入 context 设置 mark。 |
| control | `Main.c`、`utils/runtime/*`、`supervisor/*`、OpenWrt init script；`Shutdown.c` 收尾调用 `line_control_restore_on_shutdown()`。 |
| web | 桌面 REST 在 `app/src/webserver/*`；OpenWrt LuCI 包在 `luci-app-esurfingclient`。阶段 3仅允许 LuCI ACL 读取状态文件，尚无多拨状态页。 |

### Runtime 与线路控制

- 阶段 2 runtime 根级参数族：`runtime.reconnect`（enabled/max attempts、fixed/exponential/sequence、jitter 等）；`runtime.auth`（认证超时、stagger、session wait）；`runtime.keepalive`（server/fixed/clamped interval、失败次数/间隔/jitter）；`runtime.probe`（周期、connect/request timeout、线路错误容忍和 portal 确认）。实际 defaults 以 `ConfigDefaults.c:42, 80–114, 143–254` 为准。
- 为兼容 r7，实际默认值包括认证重试无限（`max_attempts=0`）、认证错峰 0 秒、探测错误/门户确认阈值 1、keep 周期服从服务端、keep 失败重试 5 次；线路错误旧行为以 `line_error_max_attempts=5`、间隔 10 秒保留。旧 `conn_timeout`/`op_timeout` 迁移到 `runtime.auth`。
- 阶段 3新增 `accounts[].iface`（缺省空）、`runtime.line_control.enabled`（true）、`down_on_session_invalid`（true）、`down_on_auth_failed`（false）、`restore_delay_sec`（30）、`restore_max_backoff_sec`（600）、`runtime.isolation.rate_limit_sec`（1800）。范围和说明见 `ANALYSIS.md:169–186`。
- 配置解析校验设备名限 `[a-z0-9_.-]` 且不超过 `IF_LEN-1`：`Config.c:281–312`；命令前再次校验，实际调用 `/sbin/ip` 的代码位于 `LineControl.c:76–99`。`iface_control_allowed()` 在 OpenWrt 且 iface 非空、总开关启用时才为真：`LineControl.c:102–108`。老配置没有 iface 时不动网络。
- 七状态为 `INTERNET_OK / PORTAL_REDIRECT / PATH_ERROR / KEEP_OK / SESSION_INVALID / RATE_LIMITED / FATAL`，枚举在 `States.h:54–56`，输出名、转换 INFO 日志和 `/var/run/esurfing/<account>.state` 在 `LineControl.c:17–73`。Lua/JS 展示页未实现；rpcd ACL 已加 `luci-app-esurfingclient/rootfs/usr/share/rpcd/acl.d/luci-app-esurfingclient.json:7`。
- `11064000` 只暂停当前 auth 进程的账号 1800 秒，期间不尝试认证；期限基于单调钟。fatal 错误暂停当前账号进程直到服务重启。账号间不共享隔离状态。down/up 失败只记日志，不崩溃；up 失败指数退避。SIGTERM 时尝试 up 本进程 down 过的接口。

## 未完成工作

### 阶段 4：内存、日志、进程安全

依照 `SPEC-REFACTOR.md` 阶段 4：审计启动/探测临时分配和缓冲复用；新增 `/api/status/mem`（RSS、堆估算）；将 LuCI 日志改为 tail/增量；可配置日志持久化目录、大小与轮转、`O_APPEND` 和定期 fsync；单实例锁 `/var/run/esurfingclient.lock`；短时崩溃循环退避；确认 SIGTERM 优雅收尾与 SIGHUP 配置重载。每阶段单独提交。

### 阶段 5：LuCI 多拨可视化

新建多拨状态页面/标签：每线路卡片显示账号、通道、mark、iface、IP、会话状态、最近认证/心跳时间、rx/tx、错误计数；汇总线路数、总速率、隔离账号；2 秒轮询、只更新变化节点。接入阶段 3状态文件；新增 API 时避免全量大日志和大表格。

### 阶段 6：文档和验收

更新 `ANALYSIS.md`、新增 `docs/MULTIDIAL.md`，记录每阶段 VM 命令与可观察结果；明确哪些行为完成编译验收、哪些完成运行实测。阶段 3应补线路 down/up、失败恢复、11064000 隔离、fatal 暂停、SIGTERM 恢复及多账号互不影响验收。

## 持续约束

- 不做按需扩缩容、账号/设备调度器、重设备独占或其它调度层。
- 不修改 mwan3 配置、规则结构或把 `track_ip` 改成公网 ICMP。
- 不引入第三方依赖。
- 缺省行为必须兼容 v2.2.0-r7；对旧配置不产生接口控制副作用。
- 只有账号显式配置合法 `iface` 且 runtime 线路控制开启时，才允许执行接口命令。
- 每阶段独立提交，提交标题格式 `type(scope): 摘要`；阶段结束按规格 §5 报告改动文件/行号、验证、限制。
- 当前保留 SO_MARK VERBOSE 诊断日志，不删除或改级别。
- 本轮只允许更新 `HANDOFF.md`；不要借交接任务改代码、运行构建或测试。

## 未验证项与风险

1. 阶段 3只做过 OpenWrt 宏下 `-Wall -Wextra -Werror -fsyntax-only` 和 `git diff --check`；完整 CMake/link 构建未跑，执行环境无 `cmake`。当前未在 VM 实测 down/up、恢复退避、限流隔离、fatal 暂停、关服务恢复。
2. `system("/sbin/ip link set dev ...")` 前有字符集/长度校验，但要在目标 OpenWrt 确认 ip 路径、执行权限、netifd/mwan3 响应、DHCP/地址恢复和 conntrack 实际行为。
3. 限流截止时间是进程单调钟内存状态；状态文件放 `/var/run`，是展示/运行态文件，不提供跨重启持久隔离。手动重启清除 fatal/限流状态符合当前设计，但自动外部重启行为需观察。
4. SIGTERM 时线路恢复逻辑已写入 auth 收尾；若进程被 SIGKILL、掉电或看门狗硬杀，无法运行清理，接口可能保持 down。阶段 4可考虑启动恢复策略，但需避免误把人工 down 的接口拉起。
5. 阶段 3状态文件由多个进程各写各自 `<account>.state`；页面目前没有展示实现。LuCI rpcd glob 权限需在目标固件检查是否可正常列举/读取。
6. 目前单个状态枚举表示一个主状态，portal、keep 成功等状态转换可能产生 INFO 日志；在实际日志量和状态持续时间上需 VM 观察。
7. 旧材料中阶段 1 VM mark 实测已由用户确认；不要把该证据误写成阶段 3也已经实机验证。
