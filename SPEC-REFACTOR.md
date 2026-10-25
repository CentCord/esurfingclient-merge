# 重构规格：多拨架构抽象 + 参数化 + 内存/日志/进程/LuCI 优化

> 本文是交给 Codex 的执行规格。分阶段推进，每阶段结束都要能编译、能回滚、并输出摘要。

---

## 0. 多拨原理（重新申明，作为一切设计的前提）

我们做的不是"一个客户端连多条线"，而是：

```
宿舍墙口（一根网线，一个物理 WAN）
        │
   虚拟交换机（macvlan）
   ┌────┼────┬────┐
 wan  wan2  wan3  wan4      ← 4 个虚拟网卡，各自独立 MAC
   │    │    │    │
  每个网卡上跑一份完整的天翼校园网客户端
  （独立账号、独立会话、独立会话 ID、独立心跳、独立探测）
        │
   mwan3 按 fwmark 把不同账号的流量绑到各自的虚拟网卡
        │
   局域网设备（多连接下载时自然叠加）
```

现实类比：宿舍网口接一个交换机，交换机接 4 台路由器，每台路由器单独跑校园网插件，
最后一台有多网卡的电脑用 HypoMux 做多网卡叠加。

**由此推出的设计约束：**

1. **每条线必须完全独立**：独立 MAC、独立 DHCP、独立认证、独立心跳、独立探测。
   任何"共享状态"（比如共用一个探测结果）都是 bug。
2. **探测必须按账号选路**：每个账号的探测只能反映它自己那条线。
3. **失败必须隔离**：一个账号被限流/踢下线，不能影响其它账号。
4. **不做调度层**：不做按需扩缩容、不做重设备独占（已明确排除）。

---

## 1. 当前进度与已知问题

### 已完成
- `4be78f5` 探测按账号 mark 选路（CheckNetwork.c / DialerServer.c / NetClientCurl.c）
- `c972582` v2.2.0-r6
- `6f1ee17` v2.2.0-r7：启动加速、日志页优化、临时调试、/api/logs?tail=
- `3a6271d` ANALYSIS.md + LOG_ANALYSIS.md

### 实验室发现的关键问题（必须优先解决）

在 OpenWrt 24.10 aarch64 虚拟机里（dummy WAN + 3 个 macvlan + 4 条 fwmark 规则）：

```
对照组：主路由指向 wan2_dev  → 探测包走 wan2_dev（16 包）   ✅ 计数器可靠
实验组：账号 2 的 mark=0x200 → 探测包全部走 dummy0（主线路）  ❌ mark 没生效
日志：log_lv=6（VERBOSE）下 "SO_MARK" 出现 0 次
```

**即：探测按 mark 选路的修复在实机上没有生效。** 需要定位是：
- (A) 补丁没编进二进制；或
- (B) 回调被调用但 `tl_thread_idx` 为 -1 / mark 为 0，被我后来加的 `tl_thread_idx >= 0` 保护挡掉。

要求：在回调里加**临时诊断日志**（打印 tl_thread_idx、mark、调用点），构建后在 VM 里验证，
确认原因后给出正确修复（如果是 B，保护逻辑要改成"用调用方显式传入的账号索引"，而不是依赖线程局部变量）。

### 校园网实测的检测坑（来自用户 esurface 文档，必须遵守）

| 检测方式 | 未认证时 | 已认证时 |
|---|---|---|
| generate_204 | 302 → 门户 | 204 |
| 公网 HTTP | 全部 302 被拦 | 真实状态码 |
| **公网 TCP 握手** | **全部能通** | 通 |
| UDP:53 直查 | 超时/被接管 | 正常 |
| 系统 DNS | 正常 | 正常 |
| CDC 141:7001 | 可达 | 可达 |
| keep 142:7001 | 不可达 | 可达 |

**结论：**
- **TCP 握手通 ≠ 能上网**，不能只做 connect 检查
- **不跟随 302 跳转**（门户域名对非浏览器超时）
- **探测用固定 IP**，不走 DNS
- **ICMP 不可靠** → 不要把 mwan3 的 track_ip 改成公网地址
- 会话被踢后**已有 TCP 长连接仍可续播**，所以"长连接活着"也不代表会话有效

---

## 2. 阶段划分

### 阶段 1：架构抽象 + 缺陷定位（不改变外部行为）

**产出 `docs/ARCHITECTURE.md`**，包含：

1. 模块边界图与职责：
   - `config`（解析、校验、默认值、迁移）
   - `session`（登录/保活/登出/会话状态机）
   - `probe`（线路级探测，按账号选路）
   - `policy`（重试、退避、错峰、隔离）
   - `netio`（HTTP 客户端、mark 绑定）
   - `control`（进程管理、看门狗、信号）
   - `web`（REST API + LuCI 数据面）
2. 每个模块的对外接口（函数签名级别）与依赖方向（禁止反向依赖）
3. 现有代码到目标模块的映射表（文件 → 模块）
4. 明确"哪些是共享全局状态、为什么要共享、如何收敛"

**同时完成缺陷定位：**
- 按 §1 的方法定位 SO_MARK 问题并修复
- 修复后给出可在 VM 里复现的验证步骤

### 阶段 2：参数化（第一批，认证/重试）

把写死的值全部提到配置，默认值保持与当前行为一致。

根级新增 `runtime` 对象（不放进账号里，避免多份漂移）：

```json
"runtime": {
  "reconnect": {
    "enabled": true,              // false = 关闭自动重连（只尝试一次）
    "max_attempts": 0,            // 0 = 不设上限
    "backoff": "sequence",        // fixed | exponential | sequence
    "intervals_sec": [60,180,300,600,1200,1800],
    "fixed_interval_sec": 300,
    "base_sec": 60,
    "multiplier": 2.0,
    "max_sec": 1800,
    "jitter_sec": 30
  },
  "auth": {
    "connect_timeout_sec": 10,
    "request_timeout_sec": 15,
    "stagger_sec": 20,            // 多账号首次认证错开（0=关闭）
    "session_invalid_wait_sec": 60
  },
  "keepalive": {
    "interval_mode": "server",    // server | fixed | clamped
    "interval_sec": 300,
    "min_interval_sec": 30,
    "max_interval_sec": 1800,
    "failure_retries": 5,
    "retry_interval_sec": 1,
    "retry_jitter_sec": 1
  },
  "probe": {
    "interval_sec": 2,
    "connect_timeout_ms": 3000,
    "request_timeout_ms": 4000,
    "line_error_tolerance": 3,     // 连续 N 次探测失败才算线路故障
    "portal_confirmations": 2      // 连续 N 次 302 才认定需要认证
  }
}
```

要求：
- 所有值做范围校验，非法值回退默认并告警
- `reconnect.enabled=false` 时只尝试一次，失败后进入停止状态（不退出进程，可由用户手动重试）
- 配置缺失时行为与 v2.2.0-r7 完全一致
- 每项都要在 ANALYSIS.md 的基础参数表里补上"对应代码位置"

### 阶段 3：多拨健壮性

1. **会话级健康分离**：探测结果只回答"路径是否通"，会话是否有效必须由心跳/认证响应判断，
   两者不能互相替代（见 §1 的实测）。
2. **失败隔离**：某账号进入限流（`11064000`）或致命错误（`13012000`）时，
   该账号进入"隔离"状态（默认 30 分钟），期间不重试、不影响其它账号。
3. **错峰**：首次认证按账号序号 × `stagger_sec` 错开；所有退避都加 `jitter`。
4. **状态机**：明确输出 `INTERNET_OK / PORTAL_REDIRECT / PATH_ERROR / KEEP_OK / SESSION_INVALID / RATE_LIMITED / FATAL`
   七种状态，并在日志里分别记录。

### 阶段 4：内存与日志

1. **内存**：
   - 审计启动路径与探测路径的临时分配，避免大缓冲常驻
   - 日志行缓冲上限、XML 缓冲复用
   - 提供 `/api/status/mem`（自身 RSS、堆用量估算）
   - LuCI 页面不加载全量日志（已做 1MB 限制，继续优化为 tail 拉取）
2. **日志持久化**：
   - 现状态：`run.log` 在 tmpfs，重启即丢；停止时归档到时间戳文件（仍在 tmpfs）
   - 目标：可选持久化目录（如 `/etc/esurfing/logs` 或 U 盘），带大小上限与轮转
   - 新增 `runtime.log`：`persist`（bool）、`persist_dir`、`max_files`、`max_file_kb`
   - 掉电安全：写入用 O_APPEND + 定期 fsync，避免半行
3. **进程安全**：
   - 单实例锁（`/var/run/esurfingclient.lock`），防止重复启动
   - 崩溃循环保护：短时间内多次崩溃时退避启动
   - 信号处理：SIGTERM 优雅退出（登出/保存现场）、SIGHUP 重载配置

### 阶段 5：LuCI 可视化

新增一个独立页面（或标签页）`多拨状态`：

- **每条线一张卡片**：账号、通道、mark、虚拟网卡、IP、会话状态、最近一次认证/心跳时间、
  当前速率（rx/tx）、错误计数
- **汇总**：总速率、在线线路数、被隔离账号
- **配置展示**：只读展示 runtime 关键参数，附"去修改"链接
- **内存友好**：数据全部来自 `/api/status/multidial`（一次请求返回全部），
  前端 2 秒轮询，只更新变化的节点；不使用大表格
- **日志页**：改为 tail 拉取（每次只取增量），不再整文件读

### 阶段 6：文档与验收

- 更新 `ANALYSIS.md`、新增 `docs/ARCHITECTURE.md`、`docs/MULTIDIAL.md`
- 每个阶段的验收标准写清楚（能在 VM 里跑什么命令、看什么结果）

---

## 3. 代码规范

- 保持现有风格：中文注释、错误分类、单调钟
- 新增配置一律走 `ConfigDefaults.c` 的默认表，禁止散落的 magic number
- 每个阶段独立提交，提交信息用 `type(scope): 摘要`
- 不破坏上游已有行为：配置缺省时行为必须与 v2.2.0-r7 一致
- 只做单实例兼容：`--role auth --account N` 的多进程模型保持不变（这是多拨的基础）

## 4. 明确不做

- 不做按需扩缩容 / 重设备独占（调度层）
- 不改 mwan3 的 track_ip 为公网 ICMP（实测不可靠）
- 不引入新的第三方依赖
- 不改动 mwan3 本身的规则结构

## 5. 交付方式

按阶段提交。每阶段结束输出：
1. 改了哪些文件（含行号）
2. 如何验证（可复制粘贴的命令）
3. 已知限制

---

## 7. 阶段 3 细化：多拨健壮性 + 线路控制（补充需求）

### 7.1 背景：一条线崩会拖垮整网

实测与官方文档确认了 mwan3 的行为：

- mwan3 只在**接口 down** 时摘除该接口的规则和路由；**会话失效但链路 up** 时它一无所知，继续往死线送流量。
- 未匹配规则的流量走主路由表；主表默认路由若指向死线，会一起黑洞。
- DNS 若被分到死线，表现为"整个网络都上不了网"。
- 已有连接被按连接钉死，需要 `flush_conntrack` 才会重新选路。

**因此客户端必须主动把"会话死亡"翻译成"接口 down"。**

### 7.2 账号新增字段

```json
"accounts": [
  {
    "username": "...", "password": "...", "channel": "android",
    "mark": "0x200",
    "iface": "wan2_dev"          // 新增：该账号绑定的虚拟网卡，可留空表示不控制
  }
]
```

### 7.3 runtime 新增

```json
"line_control": {
  "enabled": true,                     // 总开关
  "down_on_session_invalid": true,     // 会话明确失效 -> ip link set <iface> down
  "down_on_auth_failed": false,        // 认证失败是否也算（默认关，避免误伤）
  "restore_delay_sec": 30,             // down 之后至少等多久才允许 up
  "restore_max_backoff_sec": 600       // up 失败时的退避上限
}
```

行为：

1. 会话判定为 `SESSION_INVALID`（服务端明确会话失效码，或心跳连续失败达到 `keepalive.failure_retries`）→ 把 `iface` down。
2. 线路恢复尝试：按 `restore_delay_sec` 起指数退避，每次 up 后重新认证；成功则清空退避。
3. 只操作 `accounts[].iface` 指定的设备；未配置则完全不动网络。
4. 所有 ip 操作通过 `system()` 调 `/sbin/ip`，失败只记日志、不崩溃；执行前校验设备名只含 `[a-z0-9_.-]`。
5. 停止服务（SIGTERM）时，把本次 down 过的设备恢复 up，避免留下断网现场。

### 7.4 失败隔离

- `RATE_LIMITED`（11064000）：该账号进入隔离，默认 30 分钟（`runtime.isolation.rate_limit_sec`），期间不重试、不参与错峰、不影响其它账号。
- `FATAL`（13012000 等）：永久暂停该账号，直到手动重启。
- 隔离状态写入状态文件（`/var/run/esurfing/<account>.state`），供 LuCI 展示。

### 7.5 七种状态

`INTERNET_OK / PORTAL_REDIRECT / PATH_ERROR / KEEP_OK / SESSION_INVALID / RATE_LIMITED / FATAL`

要求：状态变化时打印一行 INFO 日志（含账号、mark、iface、旧状态、新状态、原因），供日志分析。

### 7.6 验收（在 OpenWrt VM 里）

```sh
# 1. 建 dummy0 + 3 macvlan + 表 2/3/4 默认路由（见 lab/setup-vm.sh 的思路）
# 2. 账号 2 配 iface=wan2_dev，mark=0x200
# 3. 启动后确认探测 SYN 走 wan2_dev（nft 计数器）
# 4. 手动把 wan2_dev 的会话判定为失效（可临时把 keep_url 指向不可达地址）
# 5. 预期：wan2_dev 被 down，日志出现 SESSION_INVALID 与 line down 记录
# 6. 恢复后预期：先 up、再认证、成功后状态回到 INTERNET_OK
```
