# 上游 v2.2.1-r7 与 fork 差异分析

核对基点：fork main=0c8ba8b；共同祖先=8489c9d；上游 upstream/master=f31a19d（v2.2.1-r7）。依据包括两边提交历史、逐提交 git show --stat/diff、当前源码，以及 git merge-tree HEAD upstream/master。开始时 git status --short 为空。本报告未运行构建或测试，也没有改动代码。

> 上游 CHANGELOG.md 没有单独的 r6/r7 小节。r6 是 389c56d 的版本号更新；r7 在 f31a19d 中升级版本并记录配置路径临时文件缓冲区修正。下面也覆盖同一上游分支在 2026-10-26 至 10-27 的功能提交，不能把全部功能都归到单独的 r7 版本提交。

## 1. r6 / r7 更新摘要

- r6（389c56d）：只把 CMake 版本号更新到 2.2.1-r6，未改运行逻辑（CMakeLists.txt 1 行增删）。
- r7（f31a19d）：版本号升到 2.2.1-r7；配置写入临时路径缓冲区改为按 config_file 字符数组长度加后缀空间分配。CHANGELOG 记为“配置文件路径缓冲区按实际长度分配”，修复 PATH_MAX 缓冲区告警。
- 同期功能（CHANGELOG.md 的 2026-10-26/27 条目）：探测请求绑定 mark、SO_MARK 失败时中止请求、认证主线程探测补 mark、探测目标可配置、心跳失败原因区分、连续两次 302 确认、账号进程单实例锁、配置补全/写回修正、LuCI/Web 探测目标编辑和界面整理。提交细目见下表。

## 2. 15 个上游提交逐项核对

| 提交 | git show --stat 涉及范围 | 实际改动及判断 |
|---|---|---|
| 5005d2e | CheckNetwork.c、NetClientCurl.c、CMake、changelog | OpenWrt 探测设置 CURLOPT_OPENSOCKETFUNCTION，回调按 TLS tl_thread_idx 取 mark，并加边界保护；稳态认证线程探测开始绑定 mark，但启动等待路径索引仍为 -1。fork 对应 4be78f5，之后由 94e64ef 显式传账号上下文补齐。 |
| 128cfd0 | Config.c、changelog | mark 非字符串时警告并按未填写处理，避免空 valuestring 崩溃。没有检查字符串内容；strtoul(..., NULL, 16) 仍接受非法/溢出/零值。 |
| 539119f | DialerClient.c、CMake、changelog | 增加进程锁：POSIX 用 /tmp 锁文件和 flock，Windows 用命名互斥，防同账号 auth 进程重复运行。锁文件无法打开时选择放行，属 fail-open，需结合 fork init/procd 生命周期评估。 |
| a30fd4a | DialerClient.c、DialerInternal.h、DialerNet.c | 心跳结果区分网络层失败与服务端/协议失败；网络错误以 1/2/4/8/16/30 秒退避后留在当前周期，特定服务端错误触发重认证，增加心跳失败分类接口。 |
| cebf02a | DialerClient.c、CMake、changelog | 302 连续出现两次才转认证；记录最近登录时间，60 秒内登录两次后再次 302 则等待 35 分钟；认证退避末档从 30 改 35 分钟。新增确认计数为 _Thread_local，但原 retry_timeout/retry_auth 仍是函数内 static。 |
| 6f81e0d | States.h、Config.c、CheckNetwork.c 及多个状态/配置/网络文件 | 新增 probe_hosts（域名及固定 IP）配置和默认目标；探测按配置固定 IP 并发请求；增加探测目标解析/序列化；集中全局状态到 States，将 login_cfg 扩成含全局配置的 cfg_t，并改配置补全/写回。探测 mark 仍依赖线程索引上下文。 |
| 58befa1 | DialerAuth.c、NetClientCurl.c、CMake、changelog | SO_MARK 设置失败后关闭 socket 并返回 CURL_SOCKET_BAD，本次请求中止；work_auth 加载配置后将主线程 tl_thread_idx 设为 0，覆盖启动等待探测。后者是 TLS 索引方案，不是显式 mark context。 |
| 50aa5c6 | Web 前端、CheckNetwork.c、WebApiGet.c、OpenWrt 默认配置、两套 LuCI 页面 | 新增探测目标的 Web/LuCI 编辑、读取和保存；前端校验 IPv4、规范化 host/IP 条目并提供默认 host 列表；目标并入配置接口和默认配置。 |
| 29e609d | Config API/实现、supervisor、Web POST、portal 页面 | 调整保存与应用分离：显式“应用”才触发配置重载；认证子进程重启，其它进程热重载/重读；页面复位后刷新，并整理默认配置逻辑。 |
| 029b831（“openwrt 补全功能”） | Config.c、CMake、changelog | 修复 OpenWrt realloc/清零账号状态后，提前写入全局 cfg 导致全局字段丢失的问题；将全局参数与 probe_hosts 的解析/拷贝移至账号分支之后。diff 显示解析和复制语句后移，changelog 明确提到全局配置写入旧内存。 |
| fea3b6c（“openwrt 问题”） | Config.c、CMake、changelog | 配置补全写回不再从单账号进程状态重建 accounts（否则会抹掉其它账号），改从完整 JSON 账号数组复制；延后释放 JSON，并在 --account 选择/重分配后写全局字段和探测目标。不是笼统的 LuCI 问题修复。 |
| d287a9f | 两套 LuCI 页面、CMake、changelog | 连接/请求超时、日志目录、探测目标等收进默认折叠的“高级选项”；基础设置保留启用和日志等级。 |
| 8a9f1df | 两套 LuCI 页面、changelog | 调整探测目标表格表头/行 class、操作列宽度和添加按钮容器，属布局兼容/样式改动，不改变探测行为。 |
| 389c56d | CMake | 版本号升到 v2.2.1-r6，无功能代码差异。 |
| f31a19d | Config.c、CMake、changelog | 版本号升到 v2.2.1-r7；write_cfg_json 临时路径用 sizeof(config_file)+32，apply 标记路径也按配置路径和后缀长度分配，消除 PATH_MAX 缓冲区告警。 |

## 3. UPSTREAM-BUG-REPORT.md 八条缺陷对照

| 报告条目 | 判定 | 上游当前证据与差异 |
|---|---|---|
| 1. 启动探测未绑 mark | 已修，方式不同 | 58befa1 在 DialerAuth.c 的 work_auth() 加载单账号配置后设 tl_thread_idx=0，之后 probe callback 取该账号 cfg.mark，覆盖 wait_need_auth 启动探测。fork 94e64ef 则让 check_network_status(connect_only, account_index) 显式传索引，并用 CURLOPT_OPENSOCKETDATA 传 mark context（fork CheckNetwork.c:180-184,340）。显式上下文不依赖 TLS，能表达聚合探测无账号上下文；上游改动较小但依赖 auth 进程只加载一个账号的不变量（上游 DialerAuth.c:224-239）。上游 5005d2e 初始方案本身仍有启动漏标问题。 |
| 2. 认证超期遍历所有账号 | 未修 | 上游 DialerClient.c 仍在认证时长检查中遍历 j<g_prog_cnt 并逐项置 is_need_reauth（约 792-800 行）。fork 0eb371c 已限制为当前账号。 |
| 3. retry 计数器函数内 static 共享 | 未修 | 上游 DialerClient.c 的 run() 仍定义 static retry_timeout、retry_auth（约 133-136 行），多账号线程共享。新增的 302/登录计数是 _Thread_local，没有改变这两个计数器。fork 560749d 将其移入账号 runtime 状态。 |
| 4. 自动 mark 使用 valid_i 错位 | 未修 | 上游 Config.c 仍在跳过账号后用 0x100+valid_i*0x100（约 633-640、668-690 行）。fork 7f21da5 改用原始配置下标 i+1。 |
| 5. mark 类型/非法值解析 | 改了但不完整 | 128cfd0 只将非字符串按未填写处理（上游 Config.c:654-658）；字符串仍用 strtoul(mark->valuestring, NULL, 16)（约 679-682 行），没有 end、ERANGE、零值或 UINT32_MAX 检查，故非法/负值等问题仍在。fork 3f2ea2f 对语法和范围做完整校验。 |
| 6. SO_MARK 失败后仍发请求 | 已修 | 58befa1 的 NetClientCurl.c 在 setsockopt 失败后 close(sock_fd) 并返回 CURL_SOCKET_BAD。fork 1e09f46 语义相同。上游从 TLS 索引取 cfg.mark；fork callback 用 CURLOPT_OPENSOCKETDATA 传入 socket_mark_context_t，能记录 caller/account，且不依赖 TLS（fork NetClientCurl.c:94-120）。 |
| 7. HTTP 200 错误头未分类 | 未修 | 上游 DialerNet.c 普通请求仍只在 http_code 非 200 或 body 空的分支调用 error_code_record（约 73-87 行）；HTTP 200 且有错误头会进入解密流程，没有在请求入口按错误头分类。a30fd4a 新增心跳失败类型未补此分支。fork 7ad59c0 显式分类。 |
| 8. channel 先窄化后校验 | 未修 | 上游 ConfigAccounts.c 仍先转 uint8_t 再 channel_find（65-79 行）。fork 9792e59 在窄化前校验整数和合法范围。 |

汇总：2 条已修（启动探测实现不同）、1 条部分处理但仍不安全、5 条未修。该结论仅指源码行为，本次没有对上游构建或 VM 验收。

## 4. 双方改动交集与合并冲突

共同祖先为 8489c9d（git merge-base HEAD upstream/master）。git merge-tree HEAD upstream/master 预测以下 11 个内容冲突文件：

| 文件 | 高概率冲突区域 |
|---|---|
| esurfingclient/app/CMakeLists.txt | 两边新增源文件清单、版本/构建设置。 |
| esurfingclient/app/include/core/States.h | 上游把 login_cfg_t 改成含全局配置的 cfg_t；fork 增加 runtime、线路状态和 mark context 类型。数据模型不宜机械选边。 |
| esurfingclient/app/src/config/Config.c | 上游配置补全/序列化、probe_hosts、cfg_t 与 fork mark 校验/按原始下标分配、iface/runtime 配置；load_cfg 几乎全段重叠。 |
| esurfingclient/app/src/config/ConfigDefaults.c | 上游 probe_hosts/defaults；fork runtime defaults 和范围校验。 |
| esurfingclient/app/src/dialer/DialerAuth.c | 上游认证主线程 TLS 索引、fork 账号隔离及线路初始化/收尾。 |
| esurfingclient/app/src/dialer/DialerClient.c | 上游 302 确认/心跳退避/单实例相关逻辑与 fork 账号级重试、错误分类、持续 302 降级、隔离和线路控制重叠。重点 run()、dialer_app()、认证到期路径。 |
| esurfingclient/app/src/dialer/DialerNet.c | 上游 dialer_request_plain 增加 dialer_fail_t、heartbeat 结果枚举；fork 增加 HTTP 错误分类、限流隔离和会话状态路径。 |
| esurfingclient/app/src/net/CheckNetwork.c | 上游探测目标列表/全局配置与 fork 显式 account_index/mark_ctx 都修改 probe 结构、初始化、请求设置和入口。重点 probe_add()、check_network_status()、探测缓存线程。 |
| esurfingclient/app/src/net/NetClientCurl.c | 两边都改 open_socket_callback：上游按 tl_thread_idx/cfg.mark；fork 用 CURLOPT_OPENSOCKETDATA 显式 context。应保留显式上下文并保留失败关闭行为。 |
| luci-app-esurfingclient/rootfs-legacy/usr/lib/lua/luci/view/esurfingclient.htm | 上游新增探测目标表格/高级选项；fork 增 runtime、持续 302 参数、调试、日志和多拨状态，页面与保存逻辑冲突。 |
| luci-app-esurfingclient/rootfs/www/luci-static/resources/view/esurfingclient.js | 上游 probe_hosts 表单/defaults 与 fork tabs/runtime、日志 tail、重启和线路展示交叠。 |

ConfigAccounts.c、NetClient.h、WebApiGet.c 等双方改过的文件可自动合并，但需再审语义兼容，尤其字段名和 API JSON。上表冲突名单来自 git merge-tree 实际输出。

### 双方改动交集

- 探测选路与 socket 创建：上游 5005d2e/58befa1，fork 4be78f5/94e64ef/1e09f46；冲突点是线程 TLS 与显式 mark context。
- 配置解析、账号状态结构、CMake、DialerClient 状态机，两边都改过。
- LuCI 两个页面：上游增加 probe hosts/高级选项/表格样式；fork 增 runtime、日志、调试、线路可视化和重启服务。

### 上游新增、fork 暂无等价实现

- 可配置探测 host + 固定 IPv4 列表，并由 Web/LuCI 编辑；含默认目标和规范化逻辑（6f81e0d、50aa5c6）。
- 同一 auth 账号实例进程锁（539119f）。
- 保存/应用分离及 supervisor 的 .apply 标记；OpenWrt 全账号配置写回修正（29e609d、fea3b6c）。
- LuCI/Web 高级选项折叠及探测表格样式（d287a9f、8a9f1df）。
- 认证主线程通过 tl_thread_idx=0 绑定 mark（58befa1）；fork 已有显式 mark context，不应再引入 TLS 方案。
- 新的连续两次 302 确认、心跳失败分流与短退避（cebf02a、a30fd4a）。fork 有自己的降级/重试策略，但算法和状态语义不同。

### fork 有、上游没有的改动

- 多账号探测显式携带 account index/mark context（94e64ef；CheckNetwork.c:180-184,340）。
- 对 mark 范围校验、自动 mark 原始下标、channel 窄化前校验、HTTP 200 错误分类、账号重试计数隔离、认证到期只作用本账号等修复（3f2ea2f、7f21da5、9792e59、7ad59c0、560749d、0eb371c）。
- 线路接口控制、账号级 11064000 隔离与致命暂停（7e4c8a9、LineControl.c）；runtime 策略与参数面板、多拨状态展示（4528847、e7f46ff，LuCI JS:398-425,510-583）。
- 持续门户 302 下线路降级，不登出；阈值可配（bb4b2b3、0c8ba8b，LuCI JS:571-575）。
- LuCI runtime 面板、日志 tail、临时调试按钮、重启按钮和多拨状态（LuCI JS:350-379,510-583,877-965；重启操作也在 :115）。
- 日志轮转/生命周期等 fork 特有运维行为（a2a0792、7abddaa）。

## 5. LuCI 差异单列

| 能力 | 上游 r7 | fork main | 跟进评估 |
|---|---|---|---|
| 探测目标 host + 固定 IP 编辑 | 有；50aa5c6，两套页面均增表格；JS 有 isValidProbeIpv4、DEFAULT_PROBE_HOSTS、normalizeProbeHosts | 无对应配置编辑；当前由 C 内部目标探测 | 值得择机跟进。涉及全局状态、配置补全、两套表单及 IPv4/容量校验，不能只移植前端；必须保留按账号 mark 的 socket 上下文。 |
| 高级选项折叠 | 有；d287a9f，默认收起连接/请求超时、日志目录、probe hosts | runtime 面板分组，但没有这套基础设置折叠（LuCI JS:510-583） | 可择机跟进，属于易用性；不能覆盖 runtime tab。 |
| probe 表格样式 | 有；8a9f1df 调整表头、class 和操作列 | 当前没有相同的探测目标表格 | 低优先级，随 probe hosts 一并移植。 |
| runtime 参数面板 | 未见等价实现 | 有 auth/reconnect/keepalive/probe/line_control/degrade/isolation/log 参数（:510-583） | 合并时保留；它配置 fork 的线路控制和退避语义。 |
| 持续 302 降级 | 上游连续两次 302 后重认证，不是线路降级（cebf02a） | 有启用、302 阈值、恢复确认次数（:571-575）；0c8ba8b 将默认阈值调整为 2 | 保留。fork 在会话仍有效时将线路摘出分流，不登出；与上游动作不同。 |
| 临时调试按钮 | 未见 | 写 /tmp/esf_debug_until 并重启服务（:350-379） | 保留，服务于排障。 |
| 日志 tail/日志选择 | 上游有日志查看基础能力；本次四个 LuCI 提交未见 fork 的大小保护、tail 提示与自动刷新实现 | 有文件选择、大小保护、tail 提示与刷新（:877-965） | 保留，并审查 rpcd 文件权限。 |
| 多拨状态可视化 | 本次上游提交未见 | fork 按账号展示状态（:586-605,978-1002） | 保留，属于多实例运维的可观测性。 |
| 重启服务按钮 | 本次上游 LuCI 改动未见 | 调用 /etc/init.d/esurfingclient restart（:115,:374-379） | 保留；合并时核对 rpcd ACL 和“保存并应用”流程是否重复。 |

### CheckNetwork.c 的冲突判断

上游 6f81e0d/50aa5c6 将探测 host/IP 列表放入全局配置，再按 host 展开固定 IP 并发探测；fork 94e64ef 在每个 probe handle 保存 mark_ctx，由 check_network_status(connect_only, account_index) 指定账号。fork 当前实现见 CheckNetwork.c:85,180-184,340。两种能力可以组合：目标集合表达“测哪里”，每个 socket 的 context 表达“从哪条线测”。不应只依赖上游 tl_thread_idx；Web 聚合探测的 -1 也不代表某账号。上游探测结构与 probe_add diff 在 6f81e0d，目标配置及接口在 50aa5c6。

## 6. 合并建议（分档）

### 立刻合：只移植经过语义核对的独立小修

- f31a19d 临时路径缓冲区修正：改动小，可直接带入，不覆盖 fork 配置语义。
- 58befa1 的 fail-closed 行为 fork 已实现（1e09f46），不必重复移植；合并时确认 callback 仍关闭失败 socket。

### 择机合：需适配，不建议原样 cherry-pick

- 单实例锁 539119f：评估是否需要防手工重复启动；结合 fork init/procd 生命周期，检查 O_CLOEXEC、角色 key 与锁失败策略。
- 可配置探测服务器 6f81e0d/50aa5c6：有配置收益；要将 host/IP 配置与 fork mark_ctx 保持正交，联动合并状态结构、默认值、写回、API、LuCI 和容量校验。
- 配置写回修正 029b831/fea3b6c：借鉴保留完整 accounts JSON，以及重分配/账号选择后再写全局字段；逐项对照 fork iface/runtime 配置和 mark 计算，避免上游写回吞掉 fork 专属字段。
- 心跳失败分流/两次 302 保护 a30fd4a/cebf02a：fork 已有 keep 错误分类、会话状态和持续 302 降级；需择取策略语义，不能直接覆盖 runtime 可配策略。
- LuCI 探测表格/高级折叠 d287a9f/8a9f1df：随探测目标配置一起移植，避免破坏现有 tabs。

### 不合：保留 fork 实现，不按上游方式替换

- 上游 5005d2e 只靠 TLS 索引回调的早期实现；58befa1 的 tl_thread_idx=0 对单账号 auth 流程有效，但 fork 显式 mark context 更适合跨调用边界。
- 上游 128cfd0 只把非字符串 mark 当未填；fork 已验证字符串语法及范围。
- 上游自动 mark 仍基于 valid_i、channel 仍先窄化、HTTP 200 错误头仍未分类、认证过期仍遍历账号、retry 计数仍共享。不能回退 fork 对应修复。
- 不要用上游两次 302 后重认证替代 fork 的“会话有效时持续 302 则摘线、不登出”；动作和目标不同。

### 总体结论

不建议将 upstream/master 整体直接合进当前 main。git merge-tree 已给出 11 个内容冲突，集中在配置模型、账号状态机、探测上下文和 LuCI 页面。建议保留 fork 语义，再逐主题适配“择机合”功能；若需一次性追上游，先在集成分支合并并逐文件解决，重点保持 mark_ctx、fork 错误分类/线路控制/runtime 配置，以及 OpenWrt 全账号配置写回。当前没有构建或 VM 集成验收证据。

## 7. 证据索引

- 上游提交范围和顺序：git log --oneline 8489c9d..upstream/master；版本发布条目：upstream/master:CHANGELOG.md 2026-10-26 至 2026-10-27。
- 上游 mark/探测/状态：5005d2e、58befa1、6f81e0d、50aa5c6。
- 上游心跳/认证保护/锁：a30fd4a、cebf02a、539119f。
- 上游配置修正：128cfd0、029b831、fea3b6c、f31a19d。
- 上游 LuCI：50aa5c6、29e609d、d287a9f、8a9f1df。
- fork 八项问题与审计证据：UPSTREAM-BUG-REPORT.md；修复提交见第 3 节。
- 合并冲突：git merge-tree HEAD upstream/master，11 个冲突文件见第 4 节。
