# ESurfingClient 缺陷报告（含修改理由与多拨相关性）

基线：`v2.2.0-r4`（`8489c9d`），并对照了 `5005d2e`（探测绑定 mark）。
验证方式：本机编译 + 加密 mock 服务端 + OpenWrt 24.10 aarch64 虚拟机（假账号、4 条 macvlan、nft 计数）。

---

## 先说清楚：多拨场景下客户端和 mwan3 是怎么配合的

不理解这一层，就看不出下面很多 bug 为什么严重。

```
一个物理 WAN
   │
macvlan 拆成 wan / wan2_dev / wan3_dev / wan4_dev（各自独立 MAC、独立 DHCP）
   │
每个账号跑一份客户端，配置里带 mark（0x100 / 0x200 / 0x300 / 0x400）
   │
客户端给"本账号发出的每个 socket"设置 SO_MARK
   │
mwan3 的 ip rule 按 mark 选路由表：0x100→表1、0x200→表2 …
   │
每张表里有该线路的默认路由 → 流量从对应虚拟网卡出去
```

**结论：客户端打的 mark 是整条链路的唯一开关。**

- mark 打对了 → 账号的认证、心跳、探测、业务流量都走自己那条线
- mark 打丢了 → 全部回退到主路由表，也就是**全挤到第一条线**
- mark 打错了（数值不对） → 走到**别人的线**

所以下面标 ⭐ 的条目，都是"多拨能不能正常工作的前提"。

---

## 摘要

| # | 严重度 | 问题 | 多拨相关 | 最小修复量 |
|---|---|---|---|---|
| 1 | 高 | 探测的 mark 在认证启动路径上丢失 | ⭐ 核心 | 3 个调用点传参 |
| 2 | 高 | HTTP 200 里的 `Error-Code` 不分类 | ⭐ 加剧 | 1 个判断 |
| 3 | 高 | 认证超约 2 天遍历**所有**账号重认证 | ⭐ 核心 | 删内层循环 |
| 4 | 高 | 重试计数器函数内 `static` 共享 | ⭐ 核心 | 移进账号状态 |
| 5 | 高 | 自动 mark 用 `valid_i`，跳过账号即错位 | ⭐ 核心 | 1 行 |
| 6 | 高 | `mark` 解析未判类型 → 空指针/静默 0 | ⭐ 核心 | 3 行 |
| 7 | 中 | `SO_MARK` 失败后仍然发请求 | ⭐ 核心 | 2 行 |
| 8 | 中 | `channel` 先窄化再校验（259 变 3） | 通用 | 1 行 |
| 9 | 中 | 关闭自动重连后仍会重连 | 通用 | 1 个判断 |
| 10 | 低 | 一次 204 覆盖刚记录的 `KEEP_OK` | 通用 | 1 个条件 |

---

# 一、多拨相关（⭐ 不改就无法可靠多拨）

## 1. 探测的 mark 在"认证启动"路径上丢失

**位置**：`CheckNetwork.c` 的 `probe_add()`；调用方 `DialerNet.c` 的 `check_network_status()`

### 现象

`5005d2e` 已经给探测句柄挂上了 `OPENSOCKETFUNCTION`，并在回调里加了保护：

```c
if (tl_thread_idx >= 0 && g_prog_status[tl_thread_idx].login_cfg.mark != 0)
```

但认证进程启动时的**第一次探测发生在设置线程索引之前**，此时 `tl_thread_idx == -1`，
保护条件直接跳过 mark —— 探测仍然走主路由表。

### 为什么要改

探测的返回值决定客户端接下来做什么：204 → 认为在线；302 → 认为需要认证，
**随后会执行 `reset() → clean() → term()`，主动把这个账号的会话登出**。

所以探测绑错线路，不只是"看到了别人的状态"，而是**会触发破坏性动作**：
把一个其实健康的会话登出掉。

### 对多拨的影响（这是最严重的一条）

设当前有 4 个账号，探测全走第一条线：

- **第 1 条线会话失效** → 4 个账号的探测全部收到 302 → 4 个账号同时判定"需要认证"
  → 同时发 `term()` 登出 → 同时重新登录 → 触发服务端 IP 级登录频率限制（`11064000`）。
  这是"集体退登 + 登录风暴"的直接来源。
- **第 2/3/4 条线会话失效** → 它探测到的却是第 1 条线的 204 → 误以为在线
  → 永远不会重新认证 → 这条线实际没有会话，mwan3 仍然把流量分给它 → **黑洞**。

两种情况都在真实日志里出现过：4 个账号同一秒进入"需要认证"，随后全部被限流。

### 最小修复

让探测显式携带账号索引，不要从线程局部变量取：

```c
// CheckNetwork.c
network_status_t check_network_status(const bool connect_only, const int account_index);

// probe_add 里把 mark 存进探测自己的上下文
probes[*count].mark_ctx.mark = (account_index >= 0 && account_index < g_prog_cnt)
    ? g_prog_status[account_index].login_cfg.mark : 0;
curl_easy_setopt(handle, CURLOPT_OPENSOCKETDATA, &probes[*count].mark_ctx);
```

调用点从 `g_prog_status[0]` 取（认证进程只加载一个账号），不要依赖 `tl_thread_idx`。

### 证据

OpenWrt VM，4 条 macvlan，账号 mark 0x100/0x200/0x300/0x400：

```
# 只有 OPENSOCKETFUNCTION + tl_thread_idx 保护（即 5005d2e 的做法）
tcp flags syn oifname "dummy0"   packets 34
tcp flags syn oifname "wan2_dev" packets 0
tcp flags syn oifname "wan3_dev" packets 0
tcp flags syn oifname "wan4_dev" packets 0

SO_MARK诊断 caller=CheckNetwork.probe_add tl_thread_idx=-1 account_index=0 mark=0x200
```

```
# 改成显式传索引后
tcp flags syn oifname "dummy0"   packets 34   ← 账号1（0x100→表1→主线路，符合预期）
tcp flags syn oifname "wan2_dev" packets 18   ← 账号2
tcp flags syn oifname "wan3_dev" packets 18   ← 账号3
tcp flags syn oifname "wan4_dev" packets 18   ← 账号4
```

---

## 2. 认证超过约 2 天会遍历**所有**账号强制重认证

**位置**：`DialerClient.c:611-630`

### 现象

```c
if (get_steady_tm_ms() - g_prog_status[i].auth_cfg.auth_time >= 172200000 && ...)
{
    LOG_WARN("认证时间超过 ... 正在重新进行认证");
    for (uint8_t j = 0; j < g_prog_cnt; j++)     // ← 遍历全部账号
    {
        g_prog_status[j].runtime_status.is_need_reauth = true;
        uint8_t retry_wte = 1;
        while (g_prog_status[j].runtime_status.is_authed)
        {
            if (retry_wte > 5) sim_thread_destroy(g_prog_status[j].thread);  // ← 销毁线程
            retry_wte++;
            sleep_ms(2000, true);
        }
    }
}
```

### 为什么要改

`auth_time` 是**每个账号自己的**，判断条件也用的是 `g_prog_status[i]`，
但动作却作用到所有账号。这明显是从"单账号时代"遗留下来的作用域错误。

另外 `sim_thread_destroy()` 之后没有 `break`，循环还会继续访问该线程状态，存在重复销毁的风险。

### 对多拨的影响

- **单进程多账号模式**（不带 `--role` 直接运行，所有账号在一个进程里开线程）：
  任何一个账号跑满约 2 天，会把**全部账号**一起标记重认证 → 集体退登 → 登录风暴。
- **每账号一进程模式**（OpenWrt 上 `--role auth --account N`）：该进程里通常只有一个账号，
  影响较小，但代码本身的错误作用域仍然是隐患。

### 最小修复

```c
- for (uint8_t j = 0; j < g_prog_cnt; j++)
- {
-     g_prog_status[j].runtime_status.is_need_reauth = true;
-     ...
- }
+ g_prog_status[i].runtime_status.is_need_reauth = true;
+ // 只等待并重认证当前账号
```

同时在 `sim_thread_destroy()` 之后 `break`，避免继续访问已销毁的线程。

---

## 3. 重试计数器是函数内 `static`，多线程共享

**位置**：`DialerClient.c:126-127`

```c
static uint8_t retry_timeout = 1;
static uint8_t retry_auth = 1;
```

### 为什么要改

函数内的 `static` 是**进程级**变量，不是线程级的。单进程多账号模式下，
所有账号线程共用同一份 `retry_auth` / `retry_timeout`。

于是：

- 账号 A 认证失败一次，`retry_auth` 变成 2
- 账号 B 紧接着失败，直接跳到第 2 档退避（1 分钟变 3 分钟）
- 账号 C 再失败，继续累加

**一个账号的失败推进了别人的退避档位**，而且完全不可预期。

### 对多拨的影响

多账号同时出问题时，退避会互相踩踏：有的账号过早重试（加剧限流），
有的账号过晚重试（长时间离线）。日志上表现为"重试间隔看起来毫无规律"。

### 最小修复

把计数器移进账号状态：

```c
g_prog_status[tl_thread_idx].runtime_status.retry_auth
g_prog_status[tl_thread_idx].runtime_status.retry_timeout
```

---

## 4. 自动 mark 用 `valid_i` 计算，跳过账号后整体错位

**位置**：`Config.c:542`、`Config.c:564`

```c
login->mark = 0x100 + valid_i * 0x100;   // valid_i 是"有效账号"的序号
```

### 为什么要改

mwan3 的约定是 `mark = iface_id << 8`，而 `iface_id` 取决于接口在
`/etc/config/mwan3` 里的**声明顺序**，也就是账号在配置里的位置。

但这里用的是 `valid_i`（有效账号的序号）。一旦前面有账号因非法被 `continue` 跳过，
后面的 mark 就会整体前移：

```
账号1 无 mark  → 0x100
账号2 mark 非法 → 跳过
账号3 无 mark  → 0x200   （本该 0x300）
账号4 无 mark  → 0x300   （本该 0x400）
```

### 对多拨的影响

**账号 3 的认证和流量会走第 2 条线，账号 4 走第 3 条线。**

更麻烦的是：认证是在"自己的线"上做的（如果探测 mark 是对的），
但业务流量按错误的 mark 走另一条线，服务端看到的 IP 和会话不匹配，
会出现"明明登录成功却上不了网"这种极难排查的现象。

### 最小修复

```c
- login->mark = 0x100 + valid_i * 0x100;
+ login->mark = 0x100 + (uint32_t)(i + 1) * 0x100;   // 用配置原始下标
```

（`Config.c:685-686` 的注释已经预见到这个风险，但只防住了 `--account` 裁剪。）

---

## 5. `mark` 解析未判类型、未校验

**位置**：`Config.c:548-556`

```c
const cJSON* mark = cJSON_GetObjectItem(account, "mark");
...
if (mark->valuestring[0] != '\0')                    // ← 没先判 cJSON_IsString
{
    login->mark = strtoul(mark->valuestring, NULL, 16);  // ← 没传 end、没校验
```

### 为什么要改

三种输入都会出问题：

| 配置 | 结果 |
|---|---|
| `"mark": 512`（数字） | `valuestring` 为 NULL → **空指针崩溃** |
| `"mark": "0xGG"` | `strtoul` 返回 0 → 静默变成"不打标记" |
| `"mark": "-1"` | 变成 `0xFFFFFFFF` → 打到未定义的规则上 |

### 对多拨的影响

- **崩溃**：整个认证进程退出，账号离线
- **静默 0**：这个账号的所有流量回退主路由表，等于**白开一条线**，还占着会话
- **非法大值**：mwan3 没有对应规则，流量行为不可预期

而 `mark` 恰恰只有多拨用户才会配，所以这条几乎只影响多拨场景。

### 最小修复

```c
if (!cJSON_IsString(mark)) { /* 记录并跳过 */ }

char* end = NULL;
errno = 0;
const unsigned long v = strtoul(mark->valuestring, &end, 16);
if (end == mark->valuestring || *end != '\0' || errno == ERANGE ||
    v == 0 || v > UINT32_MAX) { /* 非法，跳过该账号 */ }
```

---

## 6. `SO_MARK` 设置失败后仍然发请求

**位置**：`NetClientCurl.c:101-113`

```c
if (setsockopt(sock_fd, SOL_SOCKET, SO_MARK, &mark, sizeof(mark)) == -1)
{
    LOG_ERROR("设置 SO_MARK 失败 ...");   // ← 只打日志
}
return sock_fd;                            // ← 照常返回
```

### 为什么要改

`setsockopt` 失败的原因可能是权限、内核限制、或 mark 值非法。
无论哪种，**这个 socket 一定不会按预期线路走**，而是回退主路由表。

请求照常发出，但走错了线，而且日志里只有一行 ERROR，很容易被忽略。

### 对多拨的影响

典型场景：账号 3 的心跳请求因为 mark 失败走了第 1 条线。
服务端从第 1 条线的 IP 收到"账号 3 的心跳"，可能判定异常、失效该会话；
而客户端还以为账号 3 在线。接下来就是那条线的流量黑洞。

**串线比失败更糟**：失败至少能重试，串线会产生错误的服务端状态。

### 最小修复

```c
if (setsockopt(...) == -1)
{
    LOG_ERROR("设置 SO_MARK 失败, 关闭该 socket");
    close(sock_fd);
    return CURL_SOCKET_BAD;    // 宁可这次请求失败，也不要串线
}
```

---

# 二、通用缺陷（单线也受益，多拨下更严重）

## 7. HTTP 200 里的 `Error-Code` 不分类 → 限流漏判

**位置**：`DialerNet.c:67`

```c
if (resp.http_code != HTTP_OK || resp.body_size == 0 || resp.body_data == NULL)
{
    LOG_ERROR("%s响应失败", safe_str(what));
    error_code_record(&resp);          // ← 只有非 200 或空 body 才记录错误码
    return NULL;
}
```

### 为什么要改

这个协议的错误是**放在响应体里的**（`Error-Code` / `Sub-Error`），HTTP 状态码不一定是 4xx/5xx。
服务端完全可能返回：

```
HTTP/1.1 200 OK
<Error-Code>102</Error-Code>
<Sub-Error>11064000</Sub-Error>
```

现在的代码只在"非 200"时才去解析错误码，遇到上面这种响应就把限流当成普通失败处理。

### 为什么要改（后果）

`11064000` 是"登录过于频繁"。限流期间重试**只会延长惩罚**：

```
被限流 → 认不出 → 1 秒后重试 → 再被限流 → 再重试 …
```

日志上表现为"限流之后还在疯狂重登"。

### 对多拨的影响

单线用户也会遇到，但多拨是 N 个账号一起撞，**放大速度是 N 倍**。
如果一个账号被限流后还在每秒重试，会持续占用 IP 级配额，把其他账号也拖下水。

### 最小修复

HTTP 200 时也解析响应体里的 `Error-Code` / `Sub-Error`，再决定是否成功。
注意保留 `Error-Code=200 + ZSM` 的 algoid 更新特例。

### 证据

用 mock 专门返回 `HTTP 200 + Error-Code=102/Sub-Error=11064000`：

- 修复前：每秒一次重登，没有进入限流状态
- 修复后：正确进入 `RATE_LIMITED`，按隔离策略等待

---

## 8. `channel` 先窄化再校验

**位置**：`ConfigAccounts.c:73-76`

```c
const uint8_t value = (uint8_t)chn->valueint;    // 259 → 3
if (channel_find(value) != NULL) return value;   // 3 是合法通道，于是接受
```

### 为什么要改

先把整数截断成 `uint8_t`，再拿截断后的值去校验。于是 `259` 变成 `3`，
恰好是合法的 Android 通道，配置写错却**静默生效**。

同理 `-1` 会变成 `255`，`512` 会变成 `0`。

### 对多拨的影响

不同通道对应不同的 UA 和协议变体。通道错了可能导致认证失败，
而多拨时每个账号的通道是分别配的，错一个就少一条线。

### 最小修复

```c
if (!cJSON_IsNumber(chn) || chn->valueint < 1 || chn->valueint > CHANNEL_COUNT) { /* 报错 */ }
const uint8_t value = (uint8_t)chn->valueint;
```

---

## 9. 关闭自动重连后仍会重连

**位置**：`DialerClient.c` 的重试分支

### 为什么要改

用户显式关掉了自动重连，代码却没有在进入重试前检查这个开关，仍然按退避重试。

配置项存在但不生效，比没有这个配置更糟——用户以为关掉了。

### 对多拨的影响

多拨场景下，某个账号被判定为"不要重连"（比如账号密码错了、或者被限流中），
它仍然持续重试，会产生无谓的登录请求，**占用 IP 级配额**，拖累其他账号。

### 最小修复

进入重试前判断一次开关，关闭时置暂停态并返回。

---

## 10. 一次 204 覆盖刚记录的 `KEEP_OK`

**位置**：`DialerClient.c` 的 `run()` 状态切换

### 为什么要改

`HTTP 204`（路径可达）和 `keep` 成功（会话有效）是**两个不同的观察值**：

- 204 只说明"能访问外网"
- keep 成功才说明"这个账号的 ticket 还有效"

现在探测返回 204 会把状态直接置为"在线"，覆盖掉刚由心跳确认的 `KEEP_OK`，
造成状态抖动，进而可能触发不必要的重新认证。

### 对多拨的影响

多拨时每个账号都有一条独立的线路和会话，状态抖动会让各账号的判定互相错位，
放大"看起来在线、实际没会话"的问题。

### 最小修复

204 只更新"路径可达"，不要清掉会话侧的 `KEEP_OK`。

---

# 三、附：以下**不是**缺陷

已核实，避免重复报告：

| 项 | 说明 |
|---|---|
| 日志默认放 tmpfs | `log_dir` 本来就可配置；默认 tmpfs 是有意省路由器闪存。要持久化改 `log_dir` 即可 |
| 崩溃后自动重启 | `procd_set_param respawn 60 5 5` 已经提供退避重启，不是缺失 |
| 每个账号一个进程 | 多拨的部署形态要求（独立会话、独立 mark、独立崩溃域），不是冗余 |

---

# 四、复现环境

- OpenWrt 24.10.4 aarch64（QEMU + hvf），`kmod-macvlan` + `kmod-dummy`
- 一条 dummy WAN + 3 个 macvlan，4 条 fwmark 规则（0x100-0x400 / mask 0x3f00）
- 观测手段：`nft add rule ip cnt out tcp flags syn oifname <dev> counter`
- 协议层：Python + `pycryptodome` 实现的加密 mock，12 个场景（限流、会话失效、门户重定向等）
- 全部使用假账号，未触碰真实校园网账号

需要的话可以把 VM 配置、mock 场景与证据日志整理成可复现脚本一并提供。
