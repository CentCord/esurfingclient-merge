# C 版与 esurface 核心模块审查

审查固定点为 `8489c9da0620882cdf29c7df6348d00ce3dce056`，差异命令 `git diff 8489c9d...0ea8723`；本轮审查 C 版 config/session/probe 的完整现状，同时对照近期改动。Python 审的是 `/Users/orpheus/Desktop/天翼校园网/esurface` 的**带未提交修改工作树**（HEAD `7872f66`）。标准来源为 `.clang-tidy`、`README.md`、用户工作树中的 `DESIGN-PHILOSOPHY.md`、`SPEC-REFACTOR.md` 与 Python 项目的 `AGENTS.md`；规格来源为 `SPEC-REFACTOR.md`、`SPEC-REVIEW-MOCK.md`。两轴由 `code-review` 技能的两个独立审查员并行完成；下文保留 Standards 与 Spec 各自编号，不合并排序。仅有源码证据的风险标为“条件触发”，未写成 VM 实测。

## Standards：工程约定与代码气味

| 条目 | 性质/级别 | 证据与判断 |
|---|---|---|
| S-01 | 硬约束 / 高 | `esurfingclient/app/src/net/NetClientCurl.c:106-121`：`setsockopt(SO_MARK)` 失败只记日志，仍返回未标记 socket。该账号可能落到主路由，违反 `DESIGN-PHILOSOPHY.md:27-35` 每账号探测选路约束。失败分支确定；触发与实际路由后果待 OpenWrt VM。 |
| S-02 | 硬约束 / 高 | `esurfingclient/app/src/dialer/LineControl.c:117-123`：丢弃 `/sbin/ip down` 的返回值却设置 `line_is_down=true`。mwan3 可能仍留死线，运行态声称已摘线，违反 `DESIGN-PHILOSOPHY.md:63-85`。 |
| S-03 | 硬约束 / 高 | `LineControl.c:112-114,126-131,153`：`down_on_auth_failed=true` 且 `down_on_session_invalid=false` 时普通认证失败不会 down；两个独立开关被耦合，违背 `SPEC-REFACTOR.md:255-269`。 |
| S-04 | 硬约束 / 中 | `DialerClient.c:227,277` 每轮先写 `INTERNET_OK`，心跳后又写 `KEEP_OK`；单个状态丢失路径与会话的并列观察值，健康时反复产生 INFO。`tests/mock/evidence/c-native-normal.log:5-16` 实际显示每秒双向切换；与 `DESIGN-PHILOSOPHY.md:41-59,115-116` 的分离和可观测性目标不符。 |
| S-05 | 硬约束 / 中 | `ConfigDefaults.c:241-254`：`runtime.line_control`、`runtime.isolation` 若非对象，不报告类型错误；与 `ConfigDefaults.c:153-156` 对其他四组的处理不一致，也违反 `SPEC-REFACTOR.md:151-152` 非法值告警。 |
| S-06 | 启发式：Duplicated Code / 中 | `DialerNet.c:240-250,275-285` 对 keep interval 的减去超时余量逻辑重复两次。当前结果一致；后续改其中一处分支容易漂移。 |
| S-07 | 启发式：Duplicated Code / 中 | `ConfigDefaults.c:41-42,80-113,168-254` 在 JSON 模板、结构体初始化和解析 fallback 中重复默认值，需回归验证以防版本漂移。现有规格兼容 r7 优先，不能仅为抽象而大改。 |

`.clang-tidy` 已能检查的机械规则未重复报告。Python 仓库核心模块已有未提交修改（`git -C /Users/orpheus/Desktop/天翼校园网/esurface status --short`）；无法从这两个仓库的当前对话推断修改来源，因此**不**将它们当作本轮 agent 越权修改。

## Spec：规格实现与交付边界

| 条目 | 级别/确定性 | 证据、规格与结果 |
|---|---|---|
| R-01 | 高 / 直接 | 有 `iface` 时心跳失败先 down 并在 `DialerClient.c:251-260` 返回，跳过 `:261-266` 的 `reconnect.enabled=false` 暂停；`LineControl.c:185-192` 后续 up 并置 `line_force_auth`，`DialerClient.c:196-201` 强制认证。关闭自动重连仍可能自动重登，违反 `SPEC-REFACTOR.md:151-154`。门户确认失败路径 `DialerClient.c:217-223` 亦未先检查此开关。 |
| R-02 | 高 / 直接 | 独立的 `down_on_auth_failed` 和 `down_on_session_invalid` 在 `LineControl.c:112-114,153` 共用了后者的 gate；配置组合失效。规格 `SPEC-REFACTOR.md:255-269`。 |
| R-03 | 高 / 条件触发 | mark 设置失败仍发未标记包（`NetClientCurl.c:106-121`），可能串线；规格 `SPEC-REFACTOR.md:31-34`。这是 `8489c9d` 基线已有的 fail-open，近期修补扩大了探测的调用覆盖，但未解决失败分支。 |
| R-04 | 中 / 直接 | `ip down` 失败仍记已 down（`LineControl.c:117-123`）；与真实接口状态不符，规格 `SPEC-REFACTOR.md:266-270`。 |
| R-05 | 验收边界 | 新增 `tests/mock/c_native_smoke.py:24-68,88-111` 的 C 原生编译及假账号进程测试，三场景分别有 `tests/mock/evidence/c-native-normal.log:4-6`、`c-native-session_expire_after.log:8-12`、`c-native-rate_limit.log:4-6` 和对应 `.jsonl`；C 协议接入要求已在本机完成。**OpenWrt VM 的 SO_MARK/iface/mwan3 联动仍未验收**；`tests/mock/README.md` 记录 nft 重定向步骤。规格验收口径见 `SPEC-REVIEW-MOCK.md:70-74`。 |
| R-06 | 中 / 直接 | `ConfigDefaults.c:241-254` 非对象 `line_control/isolation` 静默回退；规格 `SPEC-REFACTOR.md:151-152` 要求非法值告警。 |
| R-07 | 中 / 直接，Python | `esurface/auth.py:341-354` 直接拼凭据 XML，`&`/`<` 使报文无效。规格 `SPEC-REVIEW-MOCK.md:27-30` 要审认证正确性；已在 `docs/ESURFACE-REVIEW.md:18` 记录。本轮禁止改 Python 业务行为。 |
| R-08 | 中 / 直接，Python | `esurface/auth.py:593-605,626-630` 两次门户重定向即可 term+重登，没有先用 keep 确认，可能注销有效会话。与 `DESIGN-PHILOSOPHY.md:41-59` 路径/会话分离原则冲突；本轮只审计。 |

## 全模块覆盖与历史问题

- C config：`Config.c`、`ConfigDefaults.c`、`ConfigAccounts.c`、`ConfigTimeWindow.c`。额外发现 `Config.c:582-589` 对非字符串 `mark` 解引用 `valuestring[0]`，可空指针崩溃；这一点在 `8489c9d` 已存在，记为 **H-01 历史缺陷**，不冒充近期改动。`ConfigAccounts.c:73-80` 的通道数字先窄化为 `uint8_t` 再校验，值如 259 可意外变成 3，记为 **H-02 历史缺陷**。
- C session：`DialerAuth.c`、`DialerClient.c`、`DialerSession.c`、`DialerTicket.c`、`DialerNet.c`、`DialerServer.c`、`LineControl.c`、`ErrorCode.c`。`DialerNet.c:60-84,256-272` 仅在 HTTP 非 200 或空 body 时记服务端错误头；**HTTP 200 且 `Error-Code=102/Sub-Error=11064000` 的错误体**会丢失具体分类，记为 **H-03 条件缺陷**，需要用此响应形态的 mock 或真实抓包证实可触发再改。现有 mock 限流使用 HTTP 403，不能证明这一分支发生过。
- C probe/netio：`CheckNetwork.c`、`NetClient.c`、`NetClientCurl.c`。`CheckNetwork.c:340-377` 的账号索引显式传递已有用户 VM nft 计数证明；本轮本机测试不覆盖 SO_MARK 实际网路选路。Web 聚合探测仍使用账号索引 `-1`（`CheckNetwork.c:425-433`），不能把它展示为某个账号线路健康。
- Python auth/session/crypto：`auth.py:139-203,288-432,444-496,544-641`；`session.py:32-106,386-489,545-620`；`crypto.py:9-33,85-100,157-208,465-477,686-743`。除 R-07/R-08 外，`session.py:589` 使用 `advanced` 子对象解析 ticket URL，可能丢失嵌套配置；`:612-620` 在 state.cgi 300 或异常时仍可能以 0 退出；`auth.py:217-235` 与 `crypto.py:9-33` 共用可变全局密钥，构成同进程多账号风险。`crypto.py:85-100` 以 `assert` 校验网络数据，优化模式会移除校验；`:470-477` 的宽松 SM4 填充需先以真实密文检验兼容性，不能贸然收紧。完整 Python 审计见 `docs/ESURFACE-REVIEW.md`。

Standards 轴：5 项硬约束问题、2 项启发式气味；最严重的是 S-01/S-02 的跨线及假摘线风险。Spec 轴：5 项 C 实现问题、2 项 Python 实现风险，另有 R-05 的 VM 验收边界；最严重的是 R-01 的禁重连失效和 R-03 的 mark 失败后串线风险。

## D 阶段修复与复验

上表行号指审查时的 `0ea8723` 代码；以下行号指本轮修复后的代码。每项优化单独提交，括号内为对应审查条目。

| 提交 / 条目 | 变更证据 | 验证边界 |
|---|---|---|
| `1e09f46` / S-01、R-03 | `NetClientCurl.c:109-115`：SO_MARK 失败关闭 socket 并让 libcurl 得到 `CURL_SOCKET_BAD`，阻止该请求沿未标记路由发出。 | OpenWrt 宏分支语法检查通过；故障注入及 VM 实际选路尚未验证。 |
| `5294621` / R-01 | `DialerClient.c:173-178,229-231,269-271` 在会话失效后检查禁重连；`LineControl.c:169-173` 暂停态不自动 up。 | OpenWrt 宏分支语法检查通过；带 iface 的 VM 复验待做。 |
| `0d93515` / S-02、R-04 | `LineControl.c:117-125` 仅在 `ip down` 成功后记 `line_is_down` 和恢复时间。 | OpenWrt 宏分支语法检查通过；真实命令失败分支待 VM 注入。 |
| `afa441b` / S-03、R-02 | `LineControl.c:112-114,137-161` 按认证失败开关独立决定 down，不再受会话失效开关阻断。 | OpenWrt 宏分支语法检查通过；所有配置组合的 VM 联动待做。 |
| `39f7320` / S-05、R-06 | `ConfigDefaults.c:241-263` 对两个非对象 runtime 组告警并回退默认值。 | OpenWrt 宏分支语法检查通过。 |
| `3f2ea2f` / H-01 | `Config.c:560-564,594-606` 校验 mark JSON 类型、完整十六进制、范围和非零值，非法账号跳过。 | OpenWrt 宏分支语法检查通过。 |
| `9792e59` / H-02 | `ConfigAccounts.c:73-82` 在窄化前检查 channel 数值范围和整数性。 | OpenWrt 宏分支语法检查通过。 |
| `5892313` / S-04 | `DialerClient.c:236-239` 的 204 不再覆盖最近一次 `KEEP_OK`；`tests/mock/evidence/c-native-normal-after-s04.log:5-6` 只有初次 `INTERNET_OK → KEEP_OK`，之后的探测没有重复 INFO。 | 本机 85 个 C 源文件编译，normal/session_expire_after/rate_limit 三场景通过；七态单枚举仍不能同时表达路径和会话，见下节。 |
| `7ad59c0` / H-03 | `DialerNet.c:49-55,76-90,184-192` 检查 HTTP 200 错误头，保留 ticket 的 `Error-Code=200 + ZSM` 特例；`tests/mock/evidence/c-native-rate_limit_http_200-before-h03.log:4-16` 显示旧版每秒重登，修复后的 `c-native-rate_limit_http_200.jsonl:10-11` 和同名前缀 `.log:4-6` 显示 HTTP 200 下进入 `RATE_LIMITED`。 | 本机 C 四场景全部通过；Python mock 全部 12 场景通过。真实服务端是否用 HTTP 200 返回此类错误仍需抓包确认。 |

## 保留的限制

- S-04 的 INFO 抖动只修复健康 204/keep 的重复切换。单一 `line_state` 仍不能并列保存路径探测结果和会话心跳结果；`portal_redirect_all` 持续发生时可能继续在 `PORTAL_REDIRECT` 与 `KEEP_OK` 间切换。后续状态输出设计应分别记录两种观察值。
- S-06 两处心跳间隔扣除超时余量目前一致；S-07 默认值分布在模板、结构体初始化和解析中。没有已观察到的行为错误，且本轮要求保持 r7 缺省行为，因此不做无证据的抽象重排。
- R-07、R-08 及 Python `session.py`、`crypto.py` 的其他风险保留在 `docs/ESURFACE-REVIEW.md`；本轮按约束没有修改 Python 业务代码。
- R-05 仍需在 OpenWrt VM 验证 SO_MARK 失败、带 iface 的 down/up、mwan3 摘线与恢复。本机 mock 使用 `iface=""`，不能代替这项验收。发布包仍只来自 `esurfingclient/app`，`tests/mock/` 不进入发布包（`esurfingclient/Makefile:27-35`）。
