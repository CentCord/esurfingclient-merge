#ifndef ESURFINGCLIENT_LINECONTROL_H
#define ESURFINGCLIENT_LINECONTROL_H

#include <stdbool.h>
#include "core/States.h"

void line_control_set_state(line_state_t state, const char* reason);
void line_control_session_invalid(const char* reason);
void line_control_auth_failure(const char* error_code, const char* sub_error, const char* reason);
/* false means this account is intentionally waiting (isolated, fatal, or interface down). */
bool line_control_tick(void);
/** 记录一次「探测 302 但心跳成功」；达到阈值摘线（不登出）。返回 true 表示已降级 */
bool line_control_portal_seen(void);
/** 探测恢复正常（204），清空降级计数 */
void line_control_portal_ok(void);
void line_control_restore_on_shutdown(void);

#endif
