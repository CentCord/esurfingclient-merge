# esurface（Python）协议客户端审计与 C 版对照

审计日期：2026-10-26。Python 源码位于 `/Users/orpheus/Desktop/天翼校园网/esurface`；下文 `P:` 表示该目录下的相对路径，`C:` 表示本仓库 `esurfingclient/app/` 下的相对路径。审计对象是 **Python 工作树**（HEAD `7872f66`，核心模块已有未提交修改），不是宣称该提交本身含有下述实现。本报告是静态审计；未在真实校园网复现其中的推断。

## 判断

“Python 版更好”在协议可读性、交互式诊断和快速适配门户方面有依据：`P:esurface/auth.py:512-521` 把一次认证清晰写成模块→ticket→auth，`P:esurface/session.py:545-620` 有独立会话检查，`P:esurface/portal.py:58-102,134-165` 与 `P:esurface/webportal.py:73-108,127-146` 同时覆盖网页门户和 CDC。C 版拆成配置、会话、探测、线路控制、HTTP 等模块（`C:src/config/ConfigDefaults.c:80-114`、`C:src/dialer/DialerClient.c:157-222`、`C:src/net/CheckNetwork.c:340-379`、`C:src/dialer/LineControl.c:102-153`），理解成本更高，尤其跨文件追一次登录状态转移。

在当前 OpenWrt 多拨部署中，C 版的显式账号选路与隔离更贴近需求：探测调用显式传 `account_index`，socket callback 依据该账号 mark 设置 `SO_MARK`（`C:src/net/CheckNetwork.c:340`、`C:src/net/NetClientCurl.c:90-119`），已由用户在 VM 中用 nft 计数验证四路 SYN；门户探测后会再次用 keep 确认会话（`C:src/dialer/DialerClient.c:207-222`）；限流和 fatal 进入各账号运行态（`C:src/dialer/LineControl.c:134-153`）。Python 有按接口绑定的能力（`P:esurface/httpclient.py:94-124,258-268`），但其 CLI 主要建立单个客户端与一条 worker 线程（`P:esurface/cli.py:195-267,503-518`）；没有证据表明它提供等价的多账号线路控制。C 的内存优势只是部署形态上的预期，**没有同条件 RSS 数据，不能称为实测胜出**。阶段 3 的线路 down/up 也尚未 VM 验收（`HANDOFF.md`“未验证项与风险”）。

两边都需要把“路径现象”和“本账号会话状态”分开。Python 看到连续门户重定向便触发全量重登（`P:esurface/auth.py:593-605,626-630`），没有先用 keep 确认；C 已为门户路径增加 keep 确认，但心跳超时最终仍可能由线路故障造成，不能等同服务端明确拒绝（`C:src/dialer/DialerClient.c:157-169,207-222`）。`generate_204` 成功也仅证明该出口可通，Python 的 `check_session()` 在 state.cgi 返回 300 或异常时仍可能以 0 退出（`P:esurface/session.py:556-562,612-620`）。

## 协议与失败路径

| 编号 | 结论与证据 | 影响与建议 |
|---|---|---|
| P-01 | Python 的主路径是拉算法模块、取 ticket、auth、keep、term；模块拉取有两种暗号回退，ticket 有最多六轮模块轮换，重登前刷新 URL（`P:esurface/auth.py:139-203,288-339,512-542`）。 | 这些边界比 C 的跨文件调用更易追踪，值得保留为协议参考。 |
| P-02 | `auth()` 拼接凭据 XML，不转义 `&`、`<`；只从解密体取 `keep-url` 判断成功，没有检查 HTTP 状态及 `Error-Code` / `Sub-Error`（`P:esurface/auth.py:341-366`）。 | 特殊字符可造成坏 XML；服务端返回错误头时可能只报泛化解析失败，影响 11064000 / 13012000 分类。应使用 XML 转义并在解析成功体前检查响应头。 |
| P-03 | `keep()` 对非 200 和 `Error-Code=300` 明确失败，但对其他错误头未分类，解密体可解析时会计为成功（`P:esurface/auth.py:368-397`）。 | 限流、致命错误与临时故障应区分；是否出现“错误体可解密却被计成功”需模拟服务端验证。 |
| P-04 | `term()` 最多五次；HTTP 200/302 直接判成功，不检查服务端错误头（`P:esurface/auth.py:399-432`）。 | 302 作为“会话已无”是合理兼容，200 错误头应至少留诊断。失败后服务端是否仍持有会话未知。 |
| P-05 | keep 使用服务端 interval，并提早 50 秒发送；连续五次失败后重登；认证退避为 `(60,300,600,1200,1800)`，耗尽后返回主循环（`P:esurface/auth.py:31-36,103-106,506-510,544-574,610-630`）。 | 有清晰的有限退避，但没有账号间错峰；`_relogin()` 每次先 term（`P:esurface/auth.py:548-562`），多进程同时故障会放大认证风暴。 |
| P-06 | `net_probe()` 的 IP 兜底把任何 `<500` 且 Location 不含三个指定片段的响应当在线，包括跳转到其他形式的门户；连续两次识别门户即重登（`P:esurface/auth.py:473-496,593-605`）。 | 已知模式以外的门户重定向会误报在线；真实服务端心跳可用时，重定向也不宜独立宣告本账号失效。 |
| P-07 | `check_session()` 先用完整 cfg 解析 ticket URL，之后却传 `advanced` 子字典，且 state.cgi 的 300/异常在 `base_ok` 时返回 0（`P:esurface/session.py:549-562,588-620`）。 | 嵌套自定义 `ticket_url` 可能丢失；命令退出码 0 只表示网口可通，不能由调用者理解成该账号认证有效。 |
| P-08 | 启动前可补发 crash 残留 term，支持按账号存档并在成功后清除（`P:esurface/session.py:386-489`）；存档同时含 ticket 与动态密钥，以普通 `open(...,"w")` 写入（`P:esurface/session.py:390-421`）。 | 恢复机制优于单纯依赖 atexit，但文件权限受 umask 影响，且先写后重命名、失败保留策略缺失。需限制读取权限并评估残留会话凭据风险。 |

## Crypto、HTTP、并发

| 编号 | 结论与证据 | 影响与建议 |
|---|---|---|
| P-09 | F6B5CFB0 是双层 3DES-CBC 固定 IV，mac codex 1 用 AES-ECB、codex 2 用 AES-CBC（`P:esurface/crypto.py:9-33,157-208`）；算法按模块或静态表选择（`P:esurface/auth.py:166-203,206-255`）。 | 这些模式来自协议互通，不能直接按现代密码学偏好替换；应把协议兼容风险与本地凭据保护分开。 |
| P-10 | ZSM 解析以 `assert` 检查网络数据，随后直接索引第 4 字节；`-O` 会去掉断言（`P:esurface/crypto.py:85-100`）。SM4 去填充只检查末字节取值，不核验所有填充字节（`P:esurface/crypto.py:470-477`）。 | 坏包可引起 `IndexError` 或不一致异常；宽松填充可能是与 C/服务端兼容所需，修改前应先用真实密文回归。 |
| P-11 | `_apply_module()` 修改 `crypto.KS1/KS2/IV` 模块全局量，而 `encrypt()/decrypt()` 读取这些全局量（`P:esurface/auth.py:217-235,241-256`；`P:esurface/crypto.py:9-33`）。 | **同一 Python 进程若并发运行不同模块账号**会交叉污染密钥；目前 CLI 为一个 `AuthClient` 启一个 worker（`P:esurface/cli.py:195-267,503-518`），这是多账号扩展风险，不是已证实的单账号故障。 |
| P-12 | CDC `requests.Session` 复用对象，但每次请求头带 `Connection: close`；connect/read 超时 `(4,10)`，CDC GET/POST 均禁自动重定向（`P:esurface/httpclient.py:289-327`）。 | 不应宣称已有 TCP 连接复用；关闭连接本身可能为协议行为。重试主要由 `AuthClient` 的状态机承担，而非 HTTP adapter。 |
| P-13 | `bind_session()` 在 iface 不存在/不可绑定时只警告后返回；后续请求仍通过默认路由（`P:esurface/httpclient.py:94-124,258-268`）。 | 多拨时可串线；显式配置接口应 fail closed，或至少阻止认证并给出结构化错误。 |
| P-14 | `AuthState.set/snapshot` 加锁，但 worker 会直接读状态；CLI 退出只睡 0.3 秒后发 term，worker 是 daemon、没有 join（`P:esurface/auth.py:44-81,610-631`；`P:esurface/cli.py:57-73,195-267,546-555`）。 | 请求最长可达 10 秒读取超时；0.3 秒不能保证 keep 与 term 不重叠。这是可由慢响应稳定触发的竞态。 |
| P-15 | 网页门户把 user/passwd/captcha 直接插进 JSON 字符串再 RSA 加密，缺少 JSON 转义；填充随机字节来自 `random.randint`（`P:esurface/webportal.py:33-59,127-135`）。 | 含引号/反斜杠的凭据会形成错误 JSON；随机填充应在协议允许时改用系统 CSPRNG。网页门户能力本身扩大了适用网络类型（`P:esurface/portal.py:58-102`）。 |

## 对 C 版的迁移边界

- 值得借鉴：Python 把协议阶段写成明确的 `_auth_round()`、`keep()`、`term()` 和 `check_session()`，而 C 的主决策在 `C:src/dialer/DialerClient.c:157-222`，会话 I/O 分散在 `C:src/dialer/DialerAuth.c`、`DialerTicket.c`、`DialerNet.c`。可在 C 审查时检查错误分类与阶段边界，不要为了“少文件”而合并状态所有权。
- 已有 C 优点：参数缺省与校验集中在 `C:src/config/ConfigDefaults.c:80-254`，探测 mark 由显式账号索引传至 socket（`C:src/net/CheckNetwork.c:340`、`C:src/net/NetClientCurl.c:90-119`），rate limit/fatal 隔离基于当前账号运行态（`C:src/dialer/LineControl.c:134-153`）。这些是多拨需求的具体能力，而非语言优劣。
- 共同未解决：网络路径探测与服务端会话信号仍可能相互冲突（`P:esurface/auth.py:593-625`；`C:src/dialer/DialerClient.c:157-222`）。真实“线路故障”与“服务端踢线”的区分需要模拟故障及目标 VM 复验，不能仅凭 TCP 握手、204 或存活长连接判定。
- 本报告中 P-02、P-06、P-07、P-13、P-14、P-15 为源码可直接推出的风险；P-03 的“错误体计成功”依赖服务端响应形态；C 的阶段 3 行为仍待 VM 实测。下一任务的 mock 用于把这些推断转成日志证据。
