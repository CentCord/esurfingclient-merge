# 持续 302 降级摘线：实现与测试记录

日期：2026-10-26 晚
分支：`test/line-control`
提交：`bb4b2b3`（实现）、`e7f46ff`（LuCI 参数）

## 一、要解决的问题

真实故障（2026-10-26 12:45）：账号被另一台设备抢占后，客户端探测持续收到 302，
但 keep（心跳）仍返回成功。原逻辑判定"会话有效"→保持在线→不摘线，
结果 mwan3 继续往这条线分流量，HTTP 全被门户拦截 → 周期性掉线。

## 二、实现

```
连续 degrade.portal_hits 次（默认 5 ≈ 10 秒）探测 302 且 keep 成功
   ↓
状态置为 DEGRADED
   ↓
mwan3 ifdown <mwan3_iface>（失败退回 ip link set down）
   ↓
line_degraded = true，但**不设置 line_force_auth**
   ↓
探测恢复 204 → 清空计数；tick 到点 ifup，且因 line_degraded 不触发重认证
```

新增配置：

```json
"runtime": {
  "degrade": { "enabled": true, "portal_hits": 5, "recover_hits": 2 }
}
```

LuCI 运行时面板新增「线路降级」分组三项。

## 三、虚拟机测试（两条线，一坏一好）

环境：
- mock A：`portal_redirect_all`（探测 302）→ 127.0.0.1:8082
- mock B：`normal`（认证后 204）→ 127.0.0.1:8083
- VM：mwan3 里 wan2=0x500、wan3=0x600，按 mark DNAT 到两个 mock
- 账号1 → mark 0x500（坏线），账号2 → mark 0x600（好线）

### 实测日志

```
09:04:04  auth#1  PORTAL_REDIRECT -> KEEP_OK
09:04:06  auth#1  KEEP_OK -> PORTAL_REDIRECT
09:04:12  auth#1  KEEP_OK -> DEGRADED
09:04:12  auth#1  mwan3 ifdown wan2 成功
09:04:12  auth#1  线路降级摘线 iface=wan2_dev 连续 5 次门户重定向，会话保持
09:04:12  auth#1  账号 1 线路降级，已摘出分流，会话保持
09:04:42  auth#1  mwan3 ifup wan2 成功        ← 30 秒后恢复尝试
```

### 判定

| 测试点 | 结果 |
|---|---|
| 连续 5 次 302（≈10 秒）判定降级 | ✅ |
| 该线被 `mwan3 ifdown`，`ip rule` 里表 5 消失 | ✅ |
| 另一条线不受影响（auth#2 保持 INTERNET_OK） | ✅ |
| 不触发登出（登出次数 = 0） | ✅ |
| 不触发重认证（auth#1 只在启动时认证一次） | ✅ |
| 恢复后 `mwan3 ifup` | ✅ |

## 四、测试中的两个干扰项（不是产品缺陷）

1. **`setsid` 启动的进程会被"守护进程已退出"检测带走**，导致一次正常登出。
   改用 `/etc/init.d/esurfingclient start`（procd 监管）后消失。

2. **ifup 之后探测变成 PATH_ERROR**：`mwan3 ifdown` 会清掉表 5 的默认路由，
   而实验里那条默认路由是手工加的，`mwan3 ifup` 不会恢复它。
   真机上 mwan3 会按接口配置重建，不属于本功能的问题。

## 五、待办

- 把降级场景并入 `tests/vm/smoke-line-control.sh`
- 真机验证：等某个账号再次被抢占时观察是否 10 秒内摘线
