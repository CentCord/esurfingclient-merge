#include "dialer/LineControl.h"

#include "core/States.h"
#include "utils/log/Logger.h"
#include "utils/time/TimeUtils.h"
#include "dialer/ErrorCode.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#ifdef __OPENWRT__
#include <errno.h>
#include <sys/stat.h>
#endif

static const char* state_name(const line_state_t state)
{
    switch (state)
    {
    case LINE_STATE_INTERNET_OK: return "INTERNET_OK";
    case LINE_STATE_PORTAL_REDIRECT: return "PORTAL_REDIRECT";
    case LINE_STATE_PATH_ERROR: return "PATH_ERROR";
    case LINE_STATE_KEEP_OK: return "KEEP_OK";
    case LINE_STATE_SESSION_INVALID: return "SESSION_INVALID";
    case LINE_STATE_RATE_LIMITED: return "RATE_LIMITED";
    case LINE_STATE_FATAL: return "FATAL";
    case LINE_STATE_DEGRADED: return "DEGRADED";
    default: return "UNKNOWN";
    }
}

static uint64_t now_ms(void) { return get_steady_tm_ms(); }

static void persist_state(void)
{
#ifdef __OPENWRT__
    if (g_prog_status == NULL || tl_thread_idx < 0 || tl_thread_idx >= g_prog_cnt) return;
    const uint8_t account = g_prog_status[tl_thread_idx].login_cfg.idx;
    if (mkdir("/var/run/esurfing", 0755) != 0 && errno != EEXIST)
    {
        LOG_WARN("创建线路状态目录失败: %s", strerror(errno));
        return;
    }
    char path[96];
    snprintf(path, sizeof(path), "/var/run/esurfing/%u.state", account);
    FILE* f = fopen(path, "w");
    if (f == NULL)
    {
        LOG_WARN("写入线路状态文件失败: %s", path);
        return;
    }
    const runtime_status_t* s = &g_prog_status[tl_thread_idx].runtime_status;
    fprintf(f, "state=%s\nmark=0x%x\niface=%s\nline_down=%s\nrate_limited_until_ms=%llu\n",
            state_name(s->line_state), g_prog_status[tl_thread_idx].login_cfg.mark,
            g_prog_status[tl_thread_idx].login_cfg.iface, s->line_is_down ? "true" : "false",
            (unsigned long long)s->rate_limited_until_ms);
    fclose(f);
#endif
}

void line_control_set_state(const line_state_t state, const char* reason)
{
    if (g_prog_status == NULL || tl_thread_idx < 0 || tl_thread_idx >= g_prog_cnt) return;
    runtime_status_t* s = &g_prog_status[tl_thread_idx].runtime_status;
    if (s->line_state == state) return;
    const line_state_t old = s->line_state;
    s->line_state = state;
    LOG_INFO("线路状态账号=%u mark=0x%x iface=%s %s -> %s 原因=%s",
             g_prog_status[tl_thread_idx].login_cfg.idx,
             g_prog_status[tl_thread_idx].login_cfg.mark,
             g_prog_status[tl_thread_idx].login_cfg.iface[0] ? g_prog_status[tl_thread_idx].login_cfg.iface : "-",
             state_name(old), state_name(state), reason ? reason : "-");
    persist_state();
}

/**
 * @brief 调 mwan3 把某条线从分流里装载/卸载
 *
 * mwan3 的接口名（wan2）和设备名（wan2_dev）不是一回事：
 *   mwan3 ifdown wan2   —— 卸载该接口的 iptables 标记/ip rule/路由表，不动网卡和 IP
 *   ip link set dev wan2_dev down —— 只把网卡置 down，mwan3 要靠探测才发现（15-70 秒）
 *
 * 优先用前者：瞬时生效、恢复只要 ifup、IP 和 DHCP 都还在。
 *
 * @param action "ifup" 或 "ifdown"
 * @return 是否执行成功
 */
static bool run_mwan3(const char* action)
{
#ifdef __OPENWRT__
    const char* name = g_prog_status[tl_thread_idx].login_cfg.mwan3_iface;
    if (name == NULL || name[0] == '\0') return false;

    const size_t len = strlen(name);
    if (len == 0 || len >= IF_LEN) return false;
    for (size_t i = 0; i < len; i++)
    {
        const char c = name[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '.' || c == '-'))
            return false;
    }

    char command[96];
    const int n = snprintf(command, sizeof(command), "/usr/sbin/mwan3 %s %s", action, name);
    if (n <= 0 || (size_t)n >= sizeof(command)) return false;

    const int rc = system(command);
    if (rc != 0)
    {
        LOG_WARN("mwan3 %s %s 执行失败 (status=%d), 退回 ip link", action, name, rc);
        return false;
    }
    LOG_INFO("mwan3 %s %s 成功", action, name);
    return true;
#else
    (void)action;
    return false;
#endif
}

static bool run_ip(const char* action)
{
#ifdef __OPENWRT__
    const char* iface = g_prog_status[tl_thread_idx].login_cfg.iface;
    /* Defense in depth: config parsing also enforces these characters and Linux IFNAMSIZ. */
    const size_t len = strlen(iface);
    if (len == 0 || len >= IF_LEN) return false;
    for (size_t i = 0; i < len; i++)
    {
        const char c = iface[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '.' || c == '-'))
            return false;
    }
    char command[96];
    const int n = snprintf(command, sizeof(command), "/sbin/ip link set dev %s %s", iface, action);
    if (n <= 0 || (size_t)n >= sizeof(command)) return false;
    const int rc = system(command);
    if (rc != 0) LOG_WARN("接口操作失败 iface=%s action=%s status=%d", iface, action, rc);
    else LOG_INFO("接口操作成功 iface=%s action=%s", iface, action);
    return rc == 0;
#else
    (void)action;
    return false;
#endif
}

static bool iface_control_allowed(void)
{
#ifdef __OPENWRT__
    return g_prog_status != NULL && tl_thread_idx >= 0 && tl_thread_idx < g_prog_cnt &&
           g_runtime_cfg.line_control_enabled && g_prog_status[tl_thread_idx].login_cfg.iface[0] != '\0';
#else
    return false;
#endif
}

static void take_line_down(const bool auth_failure)
{
    if (!iface_control_allowed() || (auth_failure ? !g_runtime_cfg.line_down_on_auth_failed : !g_runtime_cfg.line_down_on_session_invalid)) return;
    runtime_status_t* s = &g_prog_status[tl_thread_idx].runtime_status;
    if (s->line_is_down) return;
    if (!run_mwan3("ifdown") && !run_ip("down"))
    {
        LOG_WARN("接口未成功 down，保持 line_down=false，按现有认证失败路径处理");
        return;
    }
    s->line_is_down = true;
    s->line_down_at_ms = now_ms();
    s->line_restore_backoff_sec = g_runtime_cfg.line_restore_delay_sec;
    s->line_next_restore_ms = s->line_down_at_ms + (uint64_t)s->line_restore_backoff_sec * 1000;
    s->line_force_auth = true;
    persist_state();
}

void line_control_session_invalid(const char* reason)
{
    if (g_prog_status == NULL || tl_thread_idx < 0 || tl_thread_idx >= g_prog_cnt) return;
    line_control_set_state(LINE_STATE_SESSION_INVALID, reason);
    take_line_down(false);
}

void line_control_auth_failure(const char* error_code, const char* sub_error, const char* reason)
{
    if (g_prog_status == NULL || tl_thread_idx < 0 || tl_thread_idx >= g_prog_cnt) return;
    const bool rate_limited = (error_code != NULL && strcmp(error_code, "11064000") == 0) ||
                              (sub_error != NULL && strcmp(sub_error, "11064000") == 0);
    runtime_status_t* s = &g_prog_status[tl_thread_idx].runtime_status;
    if (rate_limited)
    {
        s->rate_limited_until_ms = now_ms() + (uint64_t)g_runtime_cfg.isolation_rate_limit_sec * 1000;
        line_control_set_state(LINE_STATE_RATE_LIMITED, reason ? reason : "11064000");
        take_line_down(true);
        return;
    }
    if (error_code_lookup(error_code, sub_error, NULL, 0) == AUTH_ERROR_FATAL)
    {
        s->is_retry_paused = true;
        line_control_set_state(LINE_STATE_FATAL, reason ? reason : "13012000");
        take_line_down(true);
        return;
    }
    if (g_runtime_cfg.line_down_on_auth_failed)
    {
        line_control_set_state(LINE_STATE_SESSION_INVALID, reason ? reason : "认证失败");
        take_line_down(true);
    }
}

/**
 * @brief 记录一次「探测 302 但心跳成功」
 *
 * 这个组合说明：会话在服务端仍然有效（keep 返回 200），但这台设备的 HTTP 被门户拦截。
 * 最常见的成因是账号被别的设备抢占——服务端把会话绑在对方 IP/MAC 上，我们的 keep 还能过，
 * 但出网流量被当成本认证处理。
 *
 * 连续达到阈值就摘线，但**不登出、不重认证**：会话是好的，重认证反而可能把对方的会话踢掉。
 *
 * @return true 表示本次已判定降级（或此前已降级）
 */
bool line_control_portal_seen(void)
{
    if (g_prog_status == NULL || tl_thread_idx < 0 || tl_thread_idx >= g_prog_cnt) return false;
    if (!g_runtime_cfg.degrade_enabled || !iface_control_allowed()) return false;

    runtime_status_t* s = &g_prog_status[tl_thread_idx].runtime_status;
    if (s->line_degraded) return true;

    s->degrade_hits++;
    if (s->degrade_hits < g_runtime_cfg.degrade_portal_hits) return false;

    line_control_set_state(LINE_STATE_DEGRADED, "持续门户重定向但会话有效");
    if (!s->line_is_down)
    {
        if (!run_mwan3("ifdown") && !run_ip("down"))
        {
            LOG_WARN("降级摘线失败 iface=%s", g_prog_status[tl_thread_idx].login_cfg.iface);
            return false;
        }
        s->line_is_down = true;
        s->line_down_at_ms = now_ms();
        s->line_restore_backoff_sec = g_runtime_cfg.line_restore_delay_sec;
        s->line_next_restore_ms = s->line_down_at_ms + (uint64_t)s->line_restore_backoff_sec * 1000;
    }
    s->line_degraded = true;
    /* 关键：不设置 line_force_auth —— 会话有效，不重新认证 */
    persist_state();
    LOG_INFO("线路降级摘线 iface=%s 连续 %u 次门户重定向，会话保持",
             g_prog_status[tl_thread_idx].login_cfg.iface, s->degrade_hits);
    return true;
}

void line_control_portal_ok(void)
{
    if (g_prog_status == NULL || tl_thread_idx < 0 || tl_thread_idx >= g_prog_cnt) return;
    g_prog_status[tl_thread_idx].runtime_status.degrade_hits = 0;
}

bool line_control_tick(void)
{
    if (g_prog_status == NULL || tl_thread_idx < 0 || tl_thread_idx >= g_prog_cnt) return true;
    runtime_status_t* s = &g_prog_status[tl_thread_idx].runtime_status;
    const uint64_t now = now_ms();
    if (s->line_state == LINE_STATE_FATAL || s->is_retry_paused)
    {
        sleep_ms(1000, false);
        return false;
    }
    if (s->rate_limited_until_ms > now)
    {
        sleep_ms(s->rate_limited_until_ms - now > 5000 ? 5000 : s->rate_limited_until_ms - now, false);
        return false;
    }
    if (s->line_state == LINE_STATE_RATE_LIMITED && s->rate_limited_until_ms != 0)
    {
        s->rate_limited_until_ms = 0;
        line_control_set_state(LINE_STATE_SESSION_INVALID, "限流隔离期结束");
        s->line_force_auth = true;
        persist_state();
    }
    if (s->line_is_down)
    {
        if (now < s->line_next_restore_ms)
        {
            uint64_t wait = s->line_next_restore_ms - now;
            sleep_ms(wait > 5000 ? 5000 : wait, false);
            return false;
        }
        if (run_mwan3("ifup") || run_ip("up"))
        {
            s->line_is_down = false;
            /* 降级恢复：会话一直有效，不需要重新认证；会话失效恢复才需要 */
            s->line_force_auth = !s->line_degraded;
            s->line_degraded = false;
            s->line_restore_backoff_sec = 0;
            s->line_next_restore_ms = 0;
            persist_state();
            return true;
        }
        uint32_t next = s->line_restore_backoff_sec ? s->line_restore_backoff_sec * 2 : g_runtime_cfg.line_restore_delay_sec;
        if (next == 0) next = 1;
        if (next > g_runtime_cfg.line_restore_max_backoff_sec) next = g_runtime_cfg.line_restore_max_backoff_sec;
        s->line_restore_backoff_sec = next;
        s->line_next_restore_ms = now + (uint64_t)next * 1000;
        LOG_WARN("接口恢复退避 iface=%s wait=%u 秒", g_prog_status[tl_thread_idx].login_cfg.iface, next);
        return false;
    }
    return true;
}

void line_control_restore_on_shutdown(void)
{
    if (g_prog_status == NULL) return;
    for (uint8_t i = 0; i < (uint8_t)g_prog_cnt; i++)
    {
        prog_status_t* p = &g_prog_status[i];
        if (!p->runtime_status.line_is_down || !g_runtime_cfg.line_control_enabled || p->login_cfg.iface[0] == '\0') continue;
        const int8_t old_idx = tl_thread_idx;
        tl_thread_idx = (int8_t)i;
        if (run_mwan3("ifup") || run_ip("up"))
        {
            p->runtime_status.line_is_down = false;
            persist_state();
        }
        tl_thread_idx = old_idx;
    }
}
