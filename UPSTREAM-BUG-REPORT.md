# ESurfingClient 缺陷报告

基线：上游最新 `5005d2e`（`OpenWRT 网络探测绑定 mark 值`）。与 `v2.2.0-r4`（`8489c9d`）相比只改了
`CheckNetwork.c`、`NetClientCurl.c`、`CMakeLists.txt`、`CHANGELOG.md` 四个文件，其余完全一致。

以下 8 条全部回到源码逐行核实过，并标注了触发条件与影响范围。
核实方式：把 `5005d2e` 检出到独立工作树逐函数阅读 + 本机编译 + 加密 mock + OpenWrt 虚拟机（假账号）。

---

## 摘要

| # | 问题 | 影响范围 | 多拨相关 | 最小修复量 |
|---|---|---|---|---|
| 1 | 启动探测不带 mark（`5005d2e` 只覆盖了稳态探测） | 全部 | ⭐ | 3 个调用点传参 |
| 2 | 认证超约 2 天遍历**所有**账号强制重认证 | 单进程多账号模式 | ⭐ | 删内层循环 |
| 3 | 重试计数器是函数内 `static`，多线程共享 | 单进程多账号模式 | ⭐ | 移进账号状态 |
| 4 | 自动 mark 用 `valid_i`，跳过账号后错位 | 用自动 mark 的配置 | ⭐ | 1 行 |
| 5 | `mark` 未判类型就取 `valuestring` → 空指针 | 配了数字型 mark | ⭐ | 3 行 |
| 6 | `SO_MARK` 失败后仍然发请求 | 全部 | ⭐ | 2 行 |
| 7 | HTTP 200 + `Error-Code` 头时错误分类丢失 | 全部 | ⭐ 放大 | 1 个判断 |
| 8 | `channel` 先截断成 `uint8_t` 再校验 | 全部 | — | 1 行 |

---

## 1. 启动探测不带 mark

### 源码事实

调用链（`5005d2e`）：

```
work_auth()              DialerAuth.c:218   —— 不设置 tl_thread_idx
  └─ wait_need_auth()    DialerNet.c:317    —— check_network_status(true)
  └─ dialer_app()        DialerClient.c:275 —— 只有这里设置 tl_thread_idx = arg
```

`tl_thread_idx` 初值是 `-1`（`States.c:56`），所以 `wait_need_auth()` 里那次探测执行时它是 `-1`。

`5005d2e` 新增的保护：

```c
if (tl_thread_idx >= 0 && g_prog_status[tl_thread_idx].login_cfg.mark != 0)
```

在这一次探测上会直接跳过 mark。

### 但 `5005d2e` 是有效的

它让 `run()` 里每 2 秒一次的**稳态探测**都带上了 mark。而"多账号同时误判需要认证 → 集体退登"
主要发生在稳态探测里，这条通路已被堵住。

### 剩余影响

启动那次探测只决定"要不要进入认证循环"。进入后 `run()` 会立刻用带 mark 的探测重判，
**通常在一个探测周期（约 2 秒）内自我纠正**。

所以这是**完整性补漏**，不是"修复无效"。在 N 个账号同时开机时，会增加一轮无谓的错峰抖动。

### 为什么值得修

探测结果会触发 `reset() → clean() → term()`，**主动登出**这个账号的会话。
让"决定要不要登出"的判断基于错误线路的状态，是这类问题的根。

### 最小修复

让探测显式携带账号索引，不要从线程局部变量取：

```c
// CheckNetwork.c
network_status_t check_network_status(const bool connect_only, const int account_index);

// probe_add 内
probes[*count].mark_ctx.mark = (account_index >= 0 && account_index < g_prog_cnt)
    ? g_prog_status[account_index].login_cfg.mark : 0;
curl_easy_setopt(handle, CURLOPT_OPENSOCKETDATA, &probes[*count].mark_ctx);
```

`wait_need_auth()` 里传 `0`（认证进程只加载一个账号）。

更省事的等价做法：在 `work_auth()` 里 `load_cfg()` 之后补一句 `tl_thread_idx = 0;`，
启动期的探测也能拿到 mark。

### 实测证据

OpenWrt VM，4 条 macvlan，mark 0x100/0x200/0x300/0x400：

```
# 只有 5005d2e 的保护
tcp flags syn oifname "dummy0"   packets 34
tcp flags syn oifname "wan2_dev" packets 0
tcp flags syn oifname "wan3_dev" packets 0
tcp flags syn oifname "wan4_dev" packets 0

# 显式传索引后
tcp flags syn oifname "dummy0"   packets 34   ← 账号1，符合预期
tcp flags syn oifname "wan2_dev" packets 18
tcp flags syn oifname "wan3_dev" packets 18
tcp flags syn oifname "wan4_dev" packets 18
```

---

## 2. 认证超过约 2 天会遍历所有账号强制重认证

### 源码事实（`DialerClient.c:611-630`）

```c
if (get_steady_tm_ms() - g_prog_status[i].auth_cfg.auth_time >= 172200000 && ...)
{
    LOG_WARN("认证时间超过 172200000 毫秒 ..., 正在重新进行认证");
    for (uint8_t j = 0; j < g_prog_cnt; j++)      // ← 遍历全部账号
    {
        g_prog_status[j].runtime_status.is_need_reauth = true;
        uint8_t retry_wte = 1;
        while (g_prog_status[j].runtime_status.is_authed)
        {
            if (retry_wte > 5) sim_thread_destroy(g_prog_status[j].thread);
            retry_wte++;
            sleep_ms(2000, true);
        }
    }
}
```

判断条件用 `g_prog_status[i]`（当前账号），动作却作用到所有账号。
`sim_thread_destroy()` 之后没有 `break`，会继续访问已销毁线程的状态。

### 触发条件与范围

只在**单进程多账号模式**下发作（`DialerClient.c:574` 按 `g_prog_cnt` 建线程）。
OpenWrt 上的 `--role auth --account N` 每进程只有一个账号（`g_prog_cnt == 1`），不受影响。

### 为什么值得修

一个账号到期会把所有账号一起标记重认证，造成集体退登；多账号同时重登又会撞上
服务端的登录频率限制（`11064000`）。

### 最小修复

删掉内层 `for`，只处理 `g_prog_status[i]`；`sim_thread_destroy()` 后加 `break`。

---

## 3. 重试计数器是函数内 `static`，多线程共享

### 源码事实（`DialerClient.c:124-127`）

```c
static RunStatus run(void)
{
    static uint8_t retry_timeout = 1;
    static uint8_t retry_auth = 1;
```

函数内 `static` 是**进程级**的。单进程多账号模式下所有线程共用同一份。

### 后果

账号 A 失败一次把 `retry_auth` 推到 2，账号 B 接着失败就直接进入第 2 档退避。
**一个账号的失败推进了别人的退避档位。**

### 触发条件与范围

同样只在单进程多账号模式；OpenWrt 每进程一账号时不受影响。

### 最小修复

移进 `g_prog_status[tl_thread_idx].runtime_status`。

---

## 4. 自动 mark 用 `valid_i`，跳过账号后整体错位

### 源码事实（`Config.c:519-566`）

```c
for (uint8_t i = 0, valid_i = 0; i < cnt; i++)
{
    ...
    const account_state_t state = fill_account(account, i + 1, login);
    if (state == ACCOUNT_SKIP) continue;          // ← 跳过时不增加 valid_i
    ...
    login->mark = 0x100 + valid_i * 0x100;        // ← 用的是槽位号
```

跳过一个账号后，后续账号的槽位前移，mark 随之前移：

```
账号1 无 mark  → 0x100
账号2 非法跳过
账号3 无 mark  → 0x200   （本该 0x300）
账号4 无 mark  → 0x300   （本该 0x400）
```

### 触发条件

只影响**没有显式写 mark**、且配置里存在会被跳过的账号的情况。
每个账号都显式写 `"mark"` 时不会发作。

### 对多拨的影响

mwan3 的约定是 `mark = iface_id << 8`，`iface_id` 取决于接口在 `/etc/config/mwan3` 里的顺序。
mark 前移意味着：**账号 3 的认证和流量走第 2 条线，账号 4 走第 3 条线**，
表现为"登录成功却上不了网"或"两条线互相抢会话"。

### 最小修复

```c
- login->mark = 0x100 + valid_i * 0x100;
+ login->mark = 0x100 + (uint32_t)(i + 1) * 0x100;
```

（`Config.c:685-686` 的注释已预见到风险，但只防住了 `--account` 裁剪。）

---

## 5. `mark` 未判类型就取 `valuestring` → 空指针

### 源码事实（`Config.c:546-562`）

```c
const cJSON* mark = cJSON_GetObjectItem(account, "mark");
...
if (use_cus_mark && mark->valuestring[0] == '\0')     // ← 未判 cJSON_IsString
...
if (mark->valuestring[0] != '\0')
{
    login->mark = strtoul(mark->valuestring, NULL, 16);   // ← 未传 end、未校验
```

### 三种输入

| 配置 | 结果 |
|---|---|
| `"mark": 512`（数字） | `valuestring == NULL` → **空指针崩溃** |
| `"mark": "0xGG"` | `strtoul` 返回 0 → 静默变成"不打标记" |
| `"mark": "-1"` | 变成 `0xFFFFFFFF` → 打到不存在的规则上 |

### 对多拨的影响

- 崩溃：整个认证进程退出
- 静默 0：这个账号的流量全部回退主路由表，**白开一条线**还占着会话

`mark` 只有多拨用户才会配，所以这条几乎只影响多拨。

### 最小修复

```c
if (!cJSON_IsString(mark)) { /* 记录并跳过该账号 */ }

char* end = NULL;
errno = 0;
const unsigned long v = strtoul(mark->valuestring, &end, 16);
if (end == mark->valuestring || *end != '\0' || errno == ERANGE ||
    v == 0 || v > UINT32_MAX) { /* 非法，跳过 */ }
```

---

## 6. `SO_MARK` 失败后仍然发请求

### 源码事实（`NetClientCurl.c:104-115`）

```c
if (setsockopt(sock_fd, SOL_SOCKET, SO_MARK, &mark, sizeof(mark)) == -1)
{
    LOG_ERROR("设置 SO_MARK 失败 ...");   // ← 只打日志
}
return sock_fd;                            // ← 照常返回
```

### 对多拨的影响

socket 没有 mark，这个请求就回退主路由表。**串线比失败更糟**：
失败还能重试，串线会让服务端从错误的线路 IP 收到心跳，可能判定会话异常，
而客户端还以为在线。

### 最小修复

失败时 `close(sock_fd)` 并返回 `CURL_SOCKET_BAD`。

---

## 7. HTTP 200 + `Error-Code` 头时错误分类丢失

### 源码事实

错误码来自**响应头**（`NetClientCurl.c:179-182`）：

```c
if (resp->error_code[0] == '\0' && header_name_is(header, "error-code"))
    header_copy_value(header, strlen("error-code"), resp->error_code, sizeof(resp->error_code));
```

而记录与分类只在这里发生（`DialerNet.c:67-73`）：

```c
if (resp.http_code != HTTP_OK || resp.body_size == 0 || resp.body_data == NULL)
{
    LOG_ERROR("%s响应失败", safe_str(what));
    error_code_record(&resp);      // ← 只有非 200 / 空 body 才分类
    curl_resp_free(&resp);
    return NULL;
}
```

如果服务端用 `HTTP 200` 返回 `Error-Code: 102` + `Sub-Error: 11064000`，
头部已经被抓到，但永远不会被分类。

### 后果

`heartbeat()` 与 `login()` 收到这种响应时会解密成功、但找不到 `<interval>` / `<keep-retry>`，
打印"解析失败"并返回 `false`。上层按**普通失败**处理（重试 5 次、间隔 1 秒、退出→重启→重新认证），
而不是按 `AUTH_ERROR_SLOW` 做长退避。

### 现状说明

真实日志里限流是**被正确分类**的（`102/11064000 登录过于频繁` 这行来自 `ErrorCode.c:29` 的错误表），
说明真实环境走的是非 200 分支。这条属于"服务端改用 200 返回错误才会发作"的隐患，
mock 里能稳定复现，真实环境未知。

### 最小修复

HTTP 200 时也检查一次 `resp.error_code`，非空则同样走 `error_code_record()` 再返回失败。
注意保留 `Error-Code=200 + ZSM` 的 algoid 更新特例。

---

## 8. `channel` 先截断成 `uint8_t` 再校验

### 源码事实（`ConfigAccounts.c:73-76`）

```c
const uint8_t value = (uint8_t)chn->valueint;   // 259 → 3
if (channel_find(value) != NULL) return value;  // 3 合法，于是接受
```

`259` 静默变成 Android 通道，`-1` 变成 `255`，`512` 变成 `0`。

### 最小修复

先判断 `cJSON_IsNumber` 且 `valueint` 在合法范围，再窄化。

---

## 复现环境

- OpenWrt 24.10.4 aarch64（QEMU + hvf），`kmod-macvlan` + `kmod-dummy`
- 一条 dummy WAN + 3 个 macvlan，4 条 fwmark 规则（0x100-0x400 / mask 0x3f00）
- 观测手段：`nft add rule ip cnt out tcp flags syn oifname <dev> counter`
- 协议层：Python + `pycryptodome` 加密 mock，12 个场景（限流、会话失效、门户重定向等）
- 全部使用假账号，未触碰真实校园网账号

需要的话可以把 VM 配置、mock 场景与证据日志整理成可复现脚本一并提供。
