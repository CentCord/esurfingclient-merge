# 第二轮规格：Python 版审计 + 协议模拟服务端 + 技能驱动代码审查

> 本轮换模型（gpt-6-sol / xhigh）。先读 HANDOFF.md 恢复上下文，再按本规格推进。

---

## 1. 用户的核心判断（必须认真对待，不要迎合也不要否定）

用户认为：**这个 C 语言的校园网客户端太复杂了，不如他的 Python 版 esurface。**

这是有价值的判断，需要**用证据回答**，而不是站队：

- Python 版（`/Users/orpheus/Desktop/天翼校园网/esurface`，约 5000 行）在协议细节、诊断能力、可读性上可能确实更好；
- C 版在 OpenWrt 上跑、多进程、低内存，是部署形态决定的；
- 但要指出具体在哪些方面 Python 更清晰、哪些方面 C 更合适。

**要求：先做对比审计，给出结论，再动手改。**

---

## 2. 任务 A：审计 esurface（Python）项目

范围：`/Users/orpheus/Desktop/天翼校园网/esurface`

重点文件：`esurface/auth.py`、`session.py`、`httpclient.py`、`portal.py`、`webportal.py`、`crypto.py`、`config.py`、`cli.py`

要查：

1. **认证流程的正确性**：state → auth → keep → term 的顺序、参数、重试与失败处理是否有漏洞
2. **会话与保活**：心跳周期、失败重试、会话失效判定是否合理；有没有和 C 版同样的问题（把"路径通"当成"会话有效"）
3. **Crypto**：密钥派生、加解密调用是否有误用（ECB 模式、IV、填充）
4. **HTTP 客户端**：超时、重试、重定向处理、连接复用
5. **并发/多账号**：有没有共享状态、竞态
6. **与 C 版的差异清单**：哪些 Python 做得更好、哪些 C 做得更好、哪些两边都不对

输出 `docs/ESURFACE-REVIEW.md`，每条结论带 `文件:行号` 证据。**不要改 Python 代码。**

---

## 3. 任务 B：协议模拟服务端 + 场景测试

目标：在没有真实校园网的情况下，能对 C 版和 Python 版做端到端测试。

### 3.1 服务端范围

在 `tests/mock/`（新建）实现一个可运行的模拟环境：

- **门户**：`generate_204` 在未认证时返回 302 到门户；已认证返回 204
- **认证服务**：`auth.cgi` / `keep.cgi` / `term.cgi`，按协议返回加密 XML
- **加解密**：复用 Python 版 `esurface/crypto.py` 的实现（这是关键，能省掉重写）
- **流量**：能给每条虚拟线路配置带宽上限，用来验证多拨叠加

### 3.2 场景（必须可配置、可复现）

| 场景 | 行为 |
|---|---|
| `normal` | 正常认证 + 心跳，长期在线 |
| `session_expire_after=N` | N 秒后会话失效，keep 返回失效码 |
| `portal_redirect_all` | 所有 HTTP 302（模拟未认证） |
| `rate_limit` | 认证返回 `102/11064000`，持续 N 秒 |
| `bad_credentials` | 返回 `102/13012000` |
| `anti_sharing` | 返回 `-600`（检测到共享） |
| `keep_timeout` | keep 请求超时 |
| `keep_500` | keep 返回 5xx |
| `slow` | 所有响应延迟 N 秒 |
| `flap` | 每 N 秒在正常/302 之间切换（模拟线路抖动） |
| `partial_loss` | 丢包率 N%（UDP/TCP 模拟） |
| `periodic_kick` | 每 N 秒主动踢一次（term 响应） |

### 3.3 验收

- 能在本机一条命令启动 mock，指定场景与参数
- C 版指向 mock（改 `AUTH_URL`/探测目标或 hosts）后，行为与场景一致
- 至少跑通 3 个场景并留下日志证据：`normal`、`session_expire_after`、`rate_limit`

**注意**：C 版的探测目标是写死的公网 IP。若无法改目标，可以通过 iptables/nft 重定向到 mock（在 VM 里做），并把步骤写成脚本。

---

## 4. 任务 C：技能驱动的代码审查（一半代码）

使用已安装的 skills：

1. **`code-review`**：对 C 仓库近期改动（`git log --oneline 8489c9d..HEAD`）做 Standards + Spec 双维度审查，同时审查 esurface 的核心模块。输出到 `docs/CODE-REVIEW.md`。
2. **`domain-modeling`**：整理术语表 `GLOSSARY.md`（账号/线路/会话/mark/通道/通道 ID/门户/保活/心跳/探测/隔离…），统一中英文对照。
3. **`writing-for-agents`**：编写 `AGENTS.md`，说明项目结构、构建方式、测试方式、代码约定、禁止事项。要求对"下一次接手的人或 agent"足够用。

**覆盖面要求**：C 版至少完整过一遍 `config` + `session` + `probe` 三个模块（约一半代码量）；esurface 至少过 `auth` + `session` + `crypto`。

---

## 5. 任务 D：基于审查结果的优化

只做**有证据支撑**的修改，每项都要能指回审查结论。优先级：

1. 正确性缺陷（会话判定、错误分类、竞态）
2. 内存与资源（长期运行的泄漏、无界缓冲）
3. 可读性与结构（过深的嵌套、重复逻辑、命名）
4. 性能（只在有实测证据时）

要求：每项独立提交，提交信息说明"对应审查条目编号"。

---

## 6. 约束

- 不改 Python 版的业务行为（只审计，除非用户明确要求修）
- mock 服务端只放在 `tests/`，不进入发布包
- 不引入新的运行时依赖（测试依赖可以用 Python 标准库）
- 默认行为继续保持与 r7 兼容
- 所有结论要有证据（文件:行号、日志、抓包、命令输出）

---

## 7. 交付顺序

1. `docs/ESURFACE-REVIEW.md`（Python 审计 + 与 C 的对比结论）
2. `tests/mock/`（可运行的模拟服务端 + 场景）
3. 3 个场景的实测日志
4. `docs/CODE-REVIEW.md` + `GLOSSARY.md` + `AGENTS.md`
5. 优化提交（按优先级）
