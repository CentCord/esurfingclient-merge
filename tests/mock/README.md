# 本地 CDC 协议模拟服务端

所有文件均在 `tests/mock/`，不进入 OpenWrt 发布包（`esurfingclient/Makefile:27-35` 只复制 `app/*`）。服务端直接从外部 esurface 仓库导入 `esurface/crypto.py` 的 `StaticCipher`；选择 C `CipherFactory.c:589-592` 和 Python `crypto.py:884` 都支持的 Android `DEABB8C8-…`。**运行 mock 所需的 pycryptodome 是 Python 版已有的测试环境依赖，不增加 C 客户端运行时依赖。**

## 启动与验收

本机已有的 esurface 虚拟环境：

```sh
PY=/Users/orpheus/Desktop/天翼校园网/esurface/.venv/bin/python
$PY tests/mock/server.py --scenario normal --port 7001 --log-file /tmp/esurf-mock.jsonl
$PY tests/mock/exercise.py --scenario all --log-dir tests/mock/evidence
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

## C 版 OpenWrt VM 接法

C 的探测地址和认证服务地址固定在 `CheckNetwork.c:13-16,65-75` 和 `DialerServer.c`；`DialerAuth.c:47-55` 选服后拼 URL。把 mock 跑在 VM 能访问的测试主机上，监听其真实 IPv4（例如 `--host 192.0.2.20 --port 7001`），然后在 **测试 VM** 运行：

```sh
sh tests/mock/openwrt-redirect.sh apply 192.0.2.20 7001
nft list table ip esurf_mock
# 启动该 VM 上的 C 客户端，使用假账号/Android 通道并观察 auth/keep/状态日志
sh tests/mock/openwrt-redirect.sh remove
```

脚本只创建/删除独立的 `esurf_mock` nft 表，不触碰 mwan3 配置或 `track_ip`。必须保证 mock 主机经测试 VM 的策略路由可达；若只想验证协议而不验证 mark 选路，可将 mock 放在 VM 管理网。实际在目标 VM 对 C 的三场景登录/隔离/线路控制进行验收仍需用户的 VM 环境；本轮仓库本机只跑了 HTTP 协议链及 Python `AuthClient`。若 C 在 `rate_limit` 的 HTTP 403 下未记录错误头或未隔离，应将其作为 C 版审查缺陷，而非调整模拟服务端掩盖。
