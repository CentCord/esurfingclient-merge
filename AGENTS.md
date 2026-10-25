# 接手指南

在本仓库工作时，先读 [GLOSSARY.md](GLOSSARY.md) 统一“线路、账号、会话、探测”等词；改模块边界时读 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)；改多拨行为时读 [SPEC-REFACTOR.md](SPEC-REFACTOR.md) 和 [DESIGN-PHILOSOPHY.md](DESIGN-PHILOSOPHY.md)（若工作树提供）。`HANDOFF.md` 是 2026-10-26 前一轮快照，需用当前源码和 `git log` 核对；不要把旧快照当现状。

## 工作顺序

1. `git status --short`、`git log -5 --oneline`；保留用户未跟踪文件和外部 Python 仓库的本地修改。
2. 找到变化所属模块，检查它的调用方、拥有的状态和错误路径。每项缺陷记录 `文件:行号` 或 mock/VM 日志证据。
3. 做能复现该缺陷的最小验证，再改代码；完成后用同一方法复验。需要 OpenWrt 现象的结论必须在 VM 用日志、计数器或抓包验证。
4. 每个独立修复单独提交，标题格式 `type(scope): 摘要`；提交前跑 `git diff --check`，记录执行过的构建和场景，明确未执行的 VM 项。

## 目录与职责

| 路径 | 主要职责 |
|---|---|
| `esurfingclient/app/src/config/` | JSON 配置、默认值、范围校验、账号与通道解析 |
| `esurfingclient/app/src/dialer/` | ticket、auth、keep、term、错误分类、账号状态和可选线路控制 |
| `esurfingclient/app/src/net/` | HTTP/CURL、路径探测、按账号 SO_MARK 选路 |
| `esurfingclient/app/src/cipher/` | 服务端协议指定的算法；变动需有密文向量证据 |
| `esurfingclient/app/src/{control,supervisor,utils/runtime,webserver}/` | 进程生命周期与 Web API |
| `luci-app-esurfingclient/` | LuCI 页面、ACL、包文件 |
| `tests/mock/` | 仅测试用的加密 CDC 模拟服务端、12 场景及证据日志 |

`docs/ARCHITECTURE.md:5-74` 说明现状与目标边界；其中的 `policy_*` 接口是设计建议，不能当作已经实现的函数。

## 构建与验证

桌面 C 构建（有 CMake、C 编译器和 libcurl 的环境）：

```sh
cmake -S esurfingclient/app -B /tmp/esurfingclient-build -D__OPENWRT__=0
cmake --build /tmp/esurfingclient-build -j2
```

OpenWrt 包由 `esurfingclient/Makefile` 在 SDK/源码树中调用 CMake；在本仓库单独执行该 Makefile 不是桌面构建。当前主机曾缺 CMake，阶段 2/3 只做过 OpenWrt 宏下语法检查，不能把它写成完整链接或 VM 验收。测试 mock：

```sh
/Users/orpheus/Desktop/天翼校园网/esurface/.venv/bin/python tests/mock/exercise.py --scenario all --log-dir tests/mock/evidence
/Users/orpheus/Desktop/天翼校园网/esurface/.venv/bin/python tests/mock/c_native_smoke.py --evidence-dir tests/mock/evidence
```

其他机器传 `--esurface-root` 并使用已具备 esurface 的 pycryptodome 测试环境。`tests/mock/README.md` 给出独立启动、OpenWrt 测试 VM 的 nft 重定向及证据解释。mock 与证据不会进入产品包：`esurfingclient/Makefile:27-35` 只打包 `app/*`。

## 多拨约束

- 一个虚拟接口对应一份独立客户端实例、账号会话、心跳与探测；探测必须携带本账号索引或 mark（`CheckNetwork.c:163-184,340-377`）。
- `generate_204` 的 204/302 是路径现象，服务端 keep 才是本账号会话信号（`DialerClient.c:196-222`）。TCP 握手和旧长连接均不能单独证明会话有效。
- 只有账号显式配置合法 `iface` 才允许控制接口；缺省配置保持 v2.2.0-r7 行为。接口命令前校验设备名，失败只记录，不使进程崩溃（`Config.c:281-311`、`LineControl.c:76-108`）。
- 账号级 11064000 隔离、fatal 暂停只影响本实例（`LineControl.c:134-153`）。不增加跨账号调度器，不修改 mwan3 配置、规则或 `track_ip`，不增加 C 运行时依赖。
- 处理阶段 4/5/6 时先核对 [SPEC-REFACTOR.md](SPEC-REFACTOR.md) 的范围；本文不是规格替代品。OpenWrt 线路 down/up、限流和 SIGTERM 恢复仍需目标 VM 复验，见 `HANDOFF.md`“未验证项与风险”。

## 外部 Python 参考

`/Users/orpheus/Desktop/天翼校园网/esurface` 是独立、已有未提交修改的 Python 项目。当前任务只读其 `auth.py`、`session.py`、`crypto.py` 等业务实现；对比结论在 [docs/ESURFACE-REVIEW.md](docs/ESURFACE-REVIEW.md)。真实登录或注销会影响校园网连接；本仓库测试应使用 `tests/mock/` 和假账号。
