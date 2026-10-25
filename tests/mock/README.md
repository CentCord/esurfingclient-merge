# 本地 CDC 协议模拟服务端

所有文件均在 `tests/mock/`，不进入 OpenWrt 发布包（`esurfingclient/Makefile:27-35` 只复制 `app/*`）。服务端直接从外部 esurface 仓库导入 `esurface/crypto.py` 的 `StaticCipher`；选择 C `CipherFactory.c:589-592` 和 Python `crypto.py:884` 都支持的 Android `DEABB8C8-…`。**运行 mock 所需的 pycryptodome 是 Python 版已有的测试环境依赖，不增加 C 客户端运行时依赖。**

## 启动与验收

本机已有的 esurface 虚拟环境：

```sh
PY=/Users/orpheus/Desktop/天翼校园网/esurface/.venv/bin/python
$PY tests/mock/server.py --scenario normal --port 7001 --log-file /tmp/esurf-mock.jsonl
$PY tests/mock/exercise.py --scenario all --log-dir tests/mock/evidence
$PY tests/mock/c_native_smoke.py --evidence-dir tests/mock/evidence
```

其他环境传 `--esurface-root /path/to/esurface`，并使用已安装该项目测试依赖的 Python。`server.py --help` 列出 `--after`、`--duration`、`--delay`、`--loss-percent`、`--seed`、`--keep-interval`、`--bandwidth LINE=Mbps`、`--udp-port`。单条线路带宽按**该线路所有 `/traffic/LINE?bytes=N` 连接合计**节流，不模拟真实路由或 macvlan。`partial_loss` 对 TCP 以连接中止模拟，对可选 UDP echo 端口以不应答模拟；不是内核级丢包。`periodic_kick` 在下一次请求时发现会话已踢，并对随后 term 返回 302；HTTP 服务端不能主动向客户端推送 term 响应。`portal_redirect_all` 针对探测 GET；`/portal` 和 `/healthz` 为引导/管理例外。

| 场景 | 参数与断言 |
|---|---|
| `normal` | 模块→ticket→auth→204→keep→term；另运行真实 Python `AuthClient` 同链路 |
| `session_expire_after` | `--after N` 后 keep 回 HTTP 403、`Error-Code: 300` |
| `portal_redirect_all` | 已认证仍返回探测 302，而 keep 保持成功 |
| `rate_limit` | `--duration N` 内 auth 回 403 + `102/11064000`，过期后同 ticket 可登录 |
| `bad_credentials` | auth 回 `102/13012000` |
| `anti_sharing` | auth 回 `-600` |
| `keep_timeout` | keep 延迟 `--delay N`，客户端小于 N 的超时可复现 |
| `keep_500` | keep 回 HTTP 500 |
| `slow` | 所有 HTTP 响应延迟 `--delay N` |
| `flap` | 认证后每 `--after N` 秒在探测 204/302 间切换，keep 不受影响 |
| `partial_loss` | 按 `--loss-percent N --seed S` 可复现的 TCP/UDP 模拟丢包 |
| `periodic_kick` | 每次认证后 `--after N` 失效；keep 300，过期会话 term 302 |

`tests/mock/evidence/normal.jsonl`、`session_expire_after.jsonl`、`rate_limit.jsonl` 是本轮三项验收日志；`assertion_passed` 表示 HTTP 断言通过。日志不含真实账号密码，时间戳按运行时记录。其余九个场景也由同一命令跑过，见同目录日志。

`c_native_smoke.py` 将 `esurfingclient/app` 复制到临时目录，**只在临时副本中**把三个固定探测 URL、认证服务地址和门户触发 URL 改为本机 mock 端口；然后用 C 编译器与已有 libcurl 编译 CMake 所列的 85 个 C 源文件，并启动真正的 `--role auth --account 1` 二进制（假账号，`iface` 留空）。环境代理被清除，所有测试网络目标在临时副本中都指向 127.0.0.1。`tests/mock/evidence/c-native-{normal,session_expire_after,rate_limit}.jsonl` 是服务端事件，同行 `.log` 是 C INFO/ERROR 摘要；程序源文件未修改。`--scenario rate_limit_http_200` 额外验证 HTTP 200 携带 `102/11064000` 错误头时仍进入 `RATE_LIMITED`；`c-native-rate_limit_http_200-before-h03.log` 保存旧代码每秒重试的复现，修复后的同名前缀 `.log/.jsonl` 保存隔离结果。此项验证覆盖本机 C 协议链、重认证、限流状态，**不覆盖 OpenWrt 的 SO_MARK、接口 down/up 和 mwan3**。

## C 版 OpenWrt VM 接法

C 的探测地址和认证服务地址固定在 `CheckNetwork.c:13-16,65-75` 和 `DialerServer.c`；`DialerAuth.c:47-55` 选服后拼 URL。把 mock 跑在 VM 能访问的测试主机上，监听其真实 IPv4（例如 `--host 192.0.2.20 --port 7001`），然后在 **测试 VM** 运行：

```sh
sh tests/mock/openwrt-redirect.sh apply 192.0.2.20 7001
nft list table ip esurf_mock
# 启动该 VM 上的 C 客户端，使用假账号/Android 通道并观察 auth/keep/状态日志
sh tests/mock/openwrt-redirect.sh remove
```

脚本只创建/删除独立的 `esurf_mock` nft 表，不触碰 mwan3 配置或 `track_ip`。必须保证 mock 主机经测试 VM 的策略路由可达；若只想验证协议而不验证 mark 选路，可将 mock 放在 VM 管理网。实际在目标 VM 对 C 的三场景 mark、线路 down/up、mwan3 联动进行验收仍需用户的 VM 环境；本轮已跑本机 C 可执行文件和 Python `AuthClient`，没有把本机结果冒充 OpenWrt 实机结果。
