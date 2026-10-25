# ESurfingClient 缺陷报告

基线：`v2.2.0-r4`（`8489c9d`），并对照了 `5005d2e`（探测绑定 mark）。
验证方式：本机编译 + 加密 mock 服务端 + OpenWrt 24.10 aarch64 虚拟机（假账号、4 条 macvlan 线路、nft 计数）。
以下每一条都在源码里定位过，并尽量给出可复现证据。

---

## 摘要

| # | 严重度 | 问题 | 最小修复量 |
|---|---|---|---|
| 1 | 高 | 探测的 mark 在认证启动路径上仍然丢失（`5005d2e` 未覆盖） | 改 3 个调用点传参 |
| 2 | 高 | HTTP 200 里的 `Error-Code` 不分类 → 限流漏判 | 1 个判断 |
| 3 | 高 | 认证超约 2 天会遍历**所有**账号强制重认证 | 删内层循环 |
| 4 | 高 | 重试计数器是函数内 `static`，多线程共享 | 移进账号状态 |
| 5 | 中 | 自动 mark 用 `valid_i`，跳过账号后整体错位 | 1 行 |
| 6 | 中 | `mark` 解析未判类型 + 未校验 → 空指针/静默 0 | 3 行 |
| 7 | 中 | `channel` 先窄化再校验（259 变 3） | 1 行 |
| 8 | 中 | `SO_MARK` 失败后仍然发请求 → 串线 | 2 行 |
| 9 | 中 | 关闭自动重连后仍会重连 | 1 个判断 |
| 10 | 低 | 一次 204 覆盖刚记录的 `KEEP_OK` | 1 个条件 |

---

## 高优先级

### 1. 探测的 mark 在"认证启动"路径上仍然丢失

`5005d2e` 给 `probe_add()` 挂上了 `OPENSOCKETFUNCTION`，并在回调里加了 `tl_thread_idx >= 0` 保护。
但认证进程启动时**第一次探测发生在设置线程索引之前**，此时 `tl_thread_idx == -1`，保护条件直接跳过 mark，
探测仍然走主路由表。

**实测证据**（OpenWrt VM，4 条 macvlan，账号 mark 分别 0x100/0x200/0x300/0x400）：

```
# 修复前：所有探测 SYN 都走主线路
tcp flags syn oifname "dummy0"   packets 34
tcp flags syn oifname "wan2_dev" packets 0
tcp flags syn oifname "wan3_dev" packets 0
tcp flags syn oifname "wan4_dev" packets 0

# 加诊断日志后确认
SO_MARK诊断 caller=CheckNetwork.probe_add tl_thread_idx=-1 account_index=0 mark=0x200
```

**位置**：`CheckNetwork.c` 的 `probe_add()`；调用方在 `DialerNet.c` 的 `check_network_status()`。

**最小修复**：让探测显式携带账号索引，不要从线程局部变量取。

```c
// CheckNetwork.c
network_status_t check_network_status(const bool connect_only, const int account_index);

// probe_add 里把 mark 存进 probe 自己的上下文，用 OPENSOCKETDATA 传给回调
probes[*count].mark_ctx.mark = (account_index >= 0 && account_index < g_prog_cnt)
    ? g_prog_status[account_index].login_cfg.mark : 0;
curl_easy_setopt(handle, CURLOPT_OPENSOCKETDATA, &probes[*count].mark_ctx);
```

修复后同一 VM、同一场景：

```
tcp flags syn oifname "dummy0"   packets 34   ← 账号1（0x100 → 表1 → 主线路，符合预期）
tcp flags syn oifname "wan2_dev" packets 18   ← 账号2
tcp flags syn oifname "wan3_dev" packets 18   ← 账号3
tcp flags syn oifname "wan4_dev" packets 18   ← 账号4
```

**影响**：这条不修，`5005d2e` 想解决的问题（"主线路会话一死，所有账号同时退登重登"）在启动阶段依然存在。

---

### 2. HTTP 200 里的错误体不分类 → 限流漏判，每秒重登

`DialerNet.c:67`：

```c
if (resp.http_code != HTTP_OK || resp.body_size == 0 || resp.body_data == NULL)
{
    LOG_ERROR("%s响应失败", safe_str(what));
    error_code_record(&resp);          // ← 只有非 200 或空 body 才记录错误码
    ...
    return NULL;
}
```

如果服务端用 **HTTP 200** 返回：

```xml
<Error-Code>102</Error-Code>
<Sub-Error>11064000</Sub-Error>
```

客户端认不出这是限流，当成普通失败，进入默认重试（1 秒一次），越试越被限流。

**证据**：mock 服务端专门用 `HTTP 200 + Error-Code=102/Sub-Error=11064000` 复现：

- 修复前日志：每秒一次重登，没有 `RATE_LIMITED`
- 修复后：正确进入 `RATE_LIMITED`，按隔离策略等待

**最小修复**：HTTP 200 时也解析响应体里的 `Error-Code` / `Sub-Error`，再决定是否成功。
注意保留 `Error-Code=200 + ZSM` 的 algoid 更新特例。

---

### 3. 认证超过约 2 天会遍历**所有**账号强制重认证

`DialerClient.c:611-630`：

```c
if (get_steady_tm_ms() - g_prog_status[i].auth_cfg.auth_time >= 172200000 && ...)
{
    LOG_WARN("认证时间超过 172200000 毫秒 ... 正在重新进行认证");
    for (uint8_t j = 0; j < g_prog_cnt; j++)        // ← 遍历全部账号
    {
        g_prog_status[j].runtime_status.is_need_reauth = true;
        uint8_t retry_wte = 1;
        while (g_prog_status[j].runtime_status.is_authed)
        {
            if (retry_wte > 5) sim_thread_destroy(g_prog_status[j].thread);   // ← 还会销毁线程
            retry_wte++;
            sleep_ms(2000, true);
        }
    }
}
```

任何一个账号跑满约 2 天，就把所有账号一起标记重认证；等待超限时还会 `sim_thread_destroy()`。
多账号部署下这就是"一个账号到期，全家登出"。

**最小修复**：删掉内层 `for`，只处理当前账号 `g_prog_status[i]`。
另外 `sim_thread_destroy()` 之后没有 `break`，请确认不会继续访问已销毁的线程。

---

### 4. 重试计数器是函数内 `static`，多线程共享

`DialerClient.c:126-127`：

```c
static uint8_t retry_timeout = 1;
static uint8_t retry_auth = 1;
```

单进程多账号模式（不带 `--role auth` 直接运行）下，多个账号线程共用同一份计数器：
一个账号的失败会推进另一个账号的退避档位。

**最小修复**：移进 `g_prog_status[tl_thread_idx].runtime_status`，按账号隔离。

---

## 中优先级

### 5. 自动 mark 用 `valid_i`，跳过账号后整体错位

`Config.c:542`、`Config.c:564`：

```c
login->mark = 0x100 + valid_i * 0x100;   // valid_i 是"有效账号"的序号
```

前面有账号因非法被 `continue` 跳过时，后续账号的 mark 会前移：

```
账号1 无 mark → 0x100
账号2 mark 非法 → 跳过
账号3 无 mark → 0x200   （本该 0x300）
账号4 无 mark → 0x300   （本该 0x400）
```

mwan3 的约定是 `mark = iface_id << 8`，错位就等于让账号流量走别人的线路。

**最小修复**：改用配置原始下标 `i + 1` 计算。
（`Config.c:685-686` 的注释已经预见过这个风险，但只防住了 `--account` 裁剪。）

---

### 6. `mark` 解析未判类型 + 未校验

`Config.c:548-556`：

```c
const cJSON* mark = cJSON_GetObjectItem(account, "mark");
...
if (mark->valuestring[0] != '\0')          // ← 没先判 cJSON_IsString
{
    login->mark = strtoul(mark->valuestring, NULL, 16);   // ← 没传 end，没校验
```

两种后果：

- `"mark": 512`（数字）→ `valuestring` 为 NULL → **空指针崩溃**
- `"mark": "0xGG"` → `strtoul` 返回 0，静默变成"不打标记"
- `"mark": "-1"` → 变成 `0xFFFFFFFF`

**最小修复**：先 `cJSON_IsString(mark)`；再 `strtoul(..., &end, 16)` 并检查
`end != start && *end == '\0' && errno != ERANGE && parsed != 0 && parsed <= UINT32_MAX`。

---

### 7. `channel` 先窄化再校验

`ConfigAccounts.c:73-76`：

```c
const uint8_t value = (uint8_t)chn->valueint;   // 259 → 3
if (channel_find(value) != NULL) return value;  // 3 是合法通道, 于是接受
```

配置写 `259` 会静默变成 Android 通道，不报错。

**最小修复**：先校验 `valueint` 在合法范围且为整数，再窄化。

---

### 8. `SO_MARK` 失败后仍然发请求

`NetClientCurl.c:101-113`：

```c
if (setsockopt(sock_fd, SOL_SOCKET, SO_MARK, &mark, sizeof(mark)) == -1)
{
    LOG_ERROR("设置 SO_MARK 失败 ...");    // ← 只打日志
}
...
return sock_fd;                             // ← 照常返回，请求沿未标记路由发出
```

多拨环境下这意味着**串到别的线**。

**最小修复**：失败时 `close(sock_fd)` 并返回 `CURL_SOCKET_BAD`。宁可这次请求失败，也不要串线。

---

### 9. 关闭自动重连后仍会重连

`DialerClient.c` 的重试分支没有检查"是否允许重连"，即使配置里关掉了也会继续重试。

**最小修复**：进入重试前判断一次开关，关闭时置暂停态并返回。

---

### 10. 一次 204 覆盖刚记录的 `KEEP_OK`

`DialerClient.c` 的 `run()`：探测返回 204（路径可达）会把状态置为"在线"，
覆盖掉刚刚由心跳确认的 `KEEP_OK`（会话有效）。两者本来是不同观察值，不该互相覆盖。

**最小修复**：204 只更新"路径可达"，不要清掉会话侧的 `KEEP_OK`。

---

## 附：以下**不是**缺陷（我已核实，避免重复报告）

| 项 | 说明 |
|---|---|
| 日志默认放 tmpfs | `log_dir` 是可配置项，默认 tmpfs 是有意省闪存。已确认，撤回该条 |
| 崩溃后自动重启 | `procd_set_param respawn 60 5 5` 已提供退避重启，不是缺失 |
| 每个账号一个进程 | 多拨的部署形态要求（独立会话、独立 mark、独立崩溃域），不是冗余 |

---

## 复现环境（供参考）

- OpenWrt 24.10.4 aarch64（QEMU + hvf），`kmod-macvlan` + `kmod-dummy`
- 一条 dummy WAN + 3 个 macvlan，4 条 fwmark 规则（0x100-0x400 / mask 0x3f00）
- 观测手段：`nft add rule ip cnt out tcp flags syn oifname <dev> counter`
- 全部使用假账号，未触碰真实校园网账号
- 协议层用 Python + `pycryptodome` 实现的加密 mock（12 个场景），可复现限流、会话失效、门户重定向等

需要的话可以把 VM 镜像配置、mock 场景与证据日志整理成可复现的脚本一并提供。
