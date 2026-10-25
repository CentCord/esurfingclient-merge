# 接手指南与上游缺陷清单

在本仓库工作时，先读这份文档。相关材料：

| 文档 | 用途 |
|---|---|
| [GLOSSARY.md](GLOSSARY.md) | 统一“线路、账号、会话、探测、mark”等词 |
| [DESIGN-PHILOSOPHY.md](DESIGN-PHILOSOPHY.md) | 判断设计对错的依据 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 模块边界与接口 |
| [docs/CODE-REVIEW.md](docs/CODE-REVIEW.md) | 双轴审查记录与修复映射 |
| [docs/ESURFACE-REVIEW.md](docs/ESURFACE-REVIEW.md) | Python 参考实现审计 |
| [SPEC-REFACTOR.md](SPEC-REFACTOR.md) | 各阶段规格 |
| [HANDOFF.md](HANDOFF.md) | 2026-10-26 前的快照，**需用 git log 核对，不要当现状** |

---

## 一、上游缺陷清单（基线 v2.2.0-r4 / `8489c9d`）

每条都在上游原始代码里确认过，并给出**最小修复**。✅ 表示本 fork 已修，⬜ 表示尚未修。

### 会导致“跨线路串流”的

**U-01 探测请求不绑 mark** ✅

- 位置：`CheckNetwork.c:131` `probe_add()`
- 现象：所有账号的探测都走主路由表；主线路会话一死，全部账号看到 302，于是全部退登重登。
- 最小修复：

```c
#ifdef __OPENWRT__
    curl_easy_setopt(handle, CURLOPT_OPENSOCKETFUNCTION, open_socket_callback);
#endif
```

- **只加这一行不够。** 认证进程启动时的探测（`DialerNet.c` 的 `wait_need_auth` 路径）运行在
  `dialer_app()` 设置线程索引之前，此时 `tl_thread_idx == -1`，回调里的保护会跳过 mark。
  实测证据：VM 里 4 个账号的探测仍全部走主线路。
- 完整修复：`check_network_status(connect_only, account_index)` 显式传账号索引，`probe_add` 把 mark
  存进 `probe_t.mark_ctx`，用 `CURLOPT_OPENSOCKETDATA` 传给回调（本 fork 的 `94e64ef`）。
- 上游 `5005d2e` 也修了这条，但用的是“回调里判断 `tl_thread_idx >= 0`”的简化版，**大概率仍解决不了启动探测**。

**U-02 SO_MARK 设置失败后仍然发请求** ✅

- 位置：`NetClientCurl.c:101-113`
- 现象：`setsockopt` 失败只打日志，socket 照常返回 → 该请求沿未标记路由发出，**串到别的线**。
- 最小修复：失败时 `close(sock_fd)` 并返回 `CURL_SOCKET_BAD`（宁可请求失败，也不要串线）。

**U-03 回调里没有边界检查** ✅

- 位置：`NetClientCurl.c:101`
- 现象：`g_prog_status[tl_thread_idx]` 在 `tl_thread_idx == -1` 时越界读。
- 最小修复：`if (tl_thread_idx >= 0 && tl_thread_idx < g_prog_cnt && mark != 0)`。

### 会导致“误退登 / 登录风暴”的

**U-04 HTTP 200 里的错误体不分类** ✅

- 位置：`DialerNet.c:67`
- 现象：`if (http_code != 200 || body 空)` 才解析服务端错误头。服务端若用 `HTTP 200` 返回
  `Error-Code=102 / Sub-Error=11064000`（限流），客户端认不出来，当成普通失败每秒重试 → 越试越限流。
- 最小修复：HTTP 200 时也解析响应体里的 `Error-Code` / `Sub-Error`，再决定是否成功。

**U-05 认证超过约 2 天会遍历所有账号强制重认证** ✅

- 位置：`DialerClient.c:611-630`
- 现象：任何账号运行超过 `172200000ms`，内层 `for (j = 0; j < g_prog_cnt; j++)` 把所有账号标记
  `is_need_reauth`，重试超限时还会 `sim_thread_destroy()` —— 一个账号到期，全家登出。
- 最小修复：删掉内层循环，只处理当前账号 `g_prog_status[i]`。

**U-06 重试计数器是函数内 static** ✅

- 位置：`DialerClient.c:126-127`（`retry_timeout` / `retry_auth`）
- 现象：单进程多账号模式下多个线程共享同一份计数器，一个账号的失败推进了别人的退避档位。
- 最小修复：移进 `g_prog_status[tl_thread_idx].runtime_status`，按账号隔离。

**U-07 关闭自动重连后仍会重连** ✅

- 位置：`DialerClient.c` 重试分支
- 现象：配置了不重连，失败后仍进入重试。
- 最小修复：进入重试前检查 `reconnect_enabled`，为 false 时置暂停态并返回。

**U-08 一次探测结果覆盖会话状态** ✅

- 位置：`DialerClient.c` 的 `run()` 状态切换
- 现象：探测返回 204（路径通）就把状态置为“在线”，覆盖刚记录的 `KEEP_OK`（会话有效），造成抖动误判。
- 最小修复：204 只更新“路径可达”，不要清掉会话侧的 `KEEP_OK`。

### 配置解析类

**U-09 mark 解析不校验** ✅

- 位置：`Config.c:548-556`
- 现象：`mark->valuestring[0]` 未先判 `cJSON_IsString` → **空指针崩溃**；`strtoul` 不校验 →
  `"0xGG"` 变成 0、负数变成巨大值。
- 最小修复：先 `cJSON_IsString`；再 `strtoul` 并检查 `end`、`ERANGE`、`parsed != 0`、`<= UINT32_MAX`。

**U-10 自动 mark 用 valid_i 计算会错位** ✅

- 位置：`Config.c:542`、`Config.c:564`（`login->mark = 0x100 + valid_i * 0x100`）
- 现象：跳过非法账号后 `valid_i` 不再对齐配置下标：
  账号1→0x100、账号2（非法跳过）、账号3→**0x200**（本该 0x300）、账号4→**0x300**（本该 0x400）。
  结果账号 3/4 的流量走错线。
- 最小修复：自动 mark 改用配置原始下标（`i + 1`）计算，不要用有效账号序号。
- 上游 `Config.c:685-686` 的注释预见过这个风险，但只防住了 `--account` 裁剪。

**U-11 channel 先窄化再校验** ✅

- 位置：`ConfigAccounts.c:73-76`
- 现象：`(uint8_t)chn->valueint` 先把 259 变成 3，再判断“3 是合法通道” → 配置写错却静默生效。
- 最小修复：先校验 `valueint` 在合法范围且为整数，再窄化。

---

## 二、设计层缺陷（不是改一两行能解决的）

| 缺陷 | 说明 | 影响 |
|---|---|---|
| 心跳失败不区分原因 | 网络超时与服务端明确会话失效走同一套重试 | 几秒抖动就可能退登 |
| 重启补发 term | `logout_previous_session()` 会踢掉可能仍健康的会话 | 每次重启多一次登出 |
| 状态是单枚举 | 路径状态与会话状态挤在一个字段 | 无法并列表达 |
| 探测目标写死 | 两个 `generate_204` 地址与固定 IP 硬编码在 `CheckNetwork.c:13,67` | 换校园网要改代码 |
| Web 聚合探测用 `-1` 索引 | 不代表任何账号的线路 | 展示误导 |

改这些之前先读 [DESIGN-PHILOSOPHY.md](DESIGN-PHILOSOPHY.md)，不要为“看起来更干净”做大重构。

### 明确不是缺陷（上游作者已说明，不要再报）

| 项 | 为什么不是缺陷 |
|---|---|
| 日志默认放 tmpfs | `log_dir` 是可配置项；默认 tmpfs 是**有意**省路由器闪存。要持久化改 `log_dir` 即可 |
| 崩溃后自动重启 | init 脚本有 `procd_set_param respawn 60 5 5`，procd 本身会退避重启 |
| 每个账号一个进程 | 这是多拨的部署形态要求（独立会话、独立 mark、独立崩溃域），不是冗余 |

---

## 三、实测得出的三条铁律

来自真实校园网实测（详见 `docs/ESURFACE-REVIEW.md`），**违反必错**：

1. **TCP 握手通 ≠ 能上网** —— 未认证时网关放行握手，但 HTTP 全部 302 到门户。不能只做 connect 检查。
2. **长连接活着 ≠ 会话有效** —— 会话被踢后已建立的 TCP 流仍可续播，新连接才被拦。
3. **ICMP 不可靠** —— 不要把 mwan3 的 `track_ip` 改成公网地址，会误判掉线。

另外：探测**不要跟随 302**（门户域名对非浏览器请求超时），**要用固定 IP** 而不是 DNS。

---

## 四、工作顺序

1. `git status --short`、`git log -5 --oneline`；保留用户未跟踪文件和外部 Python 仓库的本地修改。
2. 找到变化所属模块，检查调用方、拥有的状态和错误路径。每项缺陷记录 `文件:行号` 或 mock/VM 日志证据。
3. 做能复现该缺陷的最小验证，再改代码；完成后用同一方法复验。
   需要 OpenWrt 现象的结论**必须在 VM 用日志、计数器或抓包验证**。
4. 每个独立修复单独提交，标题格式 `type(scope): 摘要`；提交前跑 `git diff --check`，记录执行过的构建和场景，明确未执行的 VM 项。

---

## 五、目录与职责

| 路径 | 主要职责 |
|---|---|
| `esurfingclient/app/src/config/` | JSON 配置、默认值、范围校验、账号与通道解析 |
| `esurfingclient/app/src/dialer/` | ticket、auth、keep、term、错误分类、账号状态和可选线路控制 |
| `esurfingclient/app/src/net/` | HTTP/CURL、路径探测、按账号 SO_MARK 选路 |
| `esurfingclient/app/src/cipher/` | 服务端协议指定的算法；变动需有密文向量证据 |
| `esurfingclient/app/src/{control,supervisor,utils/runtime,webserver}/` | 进程生命周期与 Web API |
| `luci-app-esurfingclient/` | LuCI 页面、ACL、包文件 |
| `tests/mock/` | 仅测试用的加密 CDC 模拟服务端、场景及证据日志 |
| `tests/vm/` | OpenWrt 虚拟机控制工具 |

`docs/ARCHITECTURE.md:5-74` 说明现状与目标边界；其中的 `policy_*` 接口是设计建议，不能当作已经实现的函数。

---

## 六、构建与验证

桌面 C 构建（有 CMake、C 编译器和 libcurl 的环境）：

```sh
cmake -S esurfingclient/app -B /tmp/esurfingclient-build -D__OPENWRT__=0
cmake --build /tmp/esurfingclient-build -j2
```

OpenWrt 包由 `esurfingclient/Makefile` 在 SDK/源码树中调用 CMake；在本仓库单独执行该 Makefile 不是桌面构建。
当前主机曾缺 CMake，阶段 2/3 只做过 OpenWrt 宏下语法检查，不能把它写成完整链接或 VM 验收。

mock 测试：

```sh
/Users/orpheus/Desktop/天翼校园网/esurface/.venv/bin/python tests/mock/exercise.py --scenario all --log-dir tests/mock/evidence
/Users/orpheus/Desktop/天翼校园网/esurface/.venv/bin/python tests/mock/c_native_smoke.py --evidence-dir tests/mock/evidence
```

其他机器传 `--esurface-root` 并使用已具备 esurface 的 pycryptodome 测试环境。`tests/mock/README.md` 给出
独立启动、OpenWrt 测试 VM 的 nft 重定向及证据解释。mock 与证据不会进入产品包：`esurfingclient/Makefile:27-35`
只打包 `app/*`。

VM 测试：

```sh
tests/vm/vm-exec 'nft list chain ip cnt out'
tests/vm/vm-push <文件>
tests/vm/vm-install <ipk>
```

**测试只用假账号（u1..u4），绝不使用真实校园网账号，绝不触碰真路由器。**

---

## 七、多拨约束

- 一个虚拟接口对应一份独立客户端实例、账号会话、心跳与探测；探测必须携带本账号索引或 mark
  （`CheckNetwork.c:163-184,340-377`）。
- `generate_204` 的 204/302 是路径现象，服务端 keep 才是本账号会话信号（`DialerClient.c:196-222`）。
- 只有账号显式配置合法 `iface` 才允许控制接口；缺省配置保持 v2.2.0-r7 行为。接口命令前校验设备名，
  失败只记录，不使进程崩溃（`Config.c:281-311`、`LineControl.c:76-108`）。
- 账号级 11064000 隔离、fatal 暂停只影响本实例（`LineControl.c:134-153`）。
  不增加跨账号调度器，不修改 mwan3 配置、规则或 `track_ip`，不增加 C 运行时依赖。
- 处理阶段 4/5/6 时先核对 [SPEC-REFACTOR.md](SPEC-REFACTOR.md) 的范围；本文不是规格替代品。

---

## 八、禁止事项

- 不做按需扩缩容 / 调度层
- 不做“重设备独占”
- 不把 mwan3 的 `track_ip` 改成公网 ICMP
- 不引入新的运行时依赖
- 不让客户端修改 mwan3 配置文件（只允许操作接口 up/down）
- 不为“看起来高级”增加复杂度
- 不用真实账号做测试

---

## 九、外部 Python 参考

`/Users/orpheus/Desktop/天翼校园网/esurface` 是独立、已有未提交修改的 Python 项目。只读其
`auth.py`、`session.py`、`crypto.py` 等业务实现；对比结论在
[docs/ESURFACE-REVIEW.md](docs/ESURFACE-REVIEW.md)。真实登录或注销会影响校园网连接；
本仓库测试应使用 `tests/mock/` 和假账号。
