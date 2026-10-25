#include "utils/text/TextUtils.h"

#include "core/States.h"

#include "ConfigInternal.h"

#include "utils/log/Logger.h"

#include "cJSON/cJSON.h"

#include <stdio.h>
#include <string.h>

typedef enum
{
    CFG_DEF_BOOL = 0,
    CFG_DEF_NUM,
    CFG_DEF_STR,
    CFG_DEF_ARR,
    CFG_DEF_OBJ
} cfg_def_type_t;

typedef struct
{
    const char* name;
    cfg_def_type_t type;
    bool boolean;
    int number;
    const char* str;
    const char* unit;
} cfg_def_t;

static const cfg_def_t s_cfg_defaults[] = {
    {"enabled",          CFG_DEF_BOOL, false, 0, NULL, NULL},
    {"web_external_acc", CFG_DEF_BOOL, DEFAULT_WEB_EXTERNAL_ACC, 0, NULL, NULL},
    {"log_lv",           CFG_DEF_NUM, false, LOG_LEVEL_INFO, NULL, NULL},
    {"log_dir",          CFG_DEF_STR, false, 0, DEFAULT_LOG_DIR, NULL},
    {"conn_timeout",     CFG_DEF_NUM, false, DEFAULT_CONN_TIMEOUT, NULL, "秒"},
    {"op_timeout",       CFG_DEF_NUM, false, DEFAULT_OP_TIMEOUT, NULL, "秒"},
    {"web_port",         CFG_DEF_NUM, false, DEFAULT_WEB_PORT, NULL, NULL},
    {"runtime",          CFG_DEF_OBJ, false, 0,
     "{\"reconnect\":{\"enabled\":true,\"max_attempts\":0,\"line_error_max_attempts\":5,\"line_error_interval_sec\":10,\"backoff\":\"sequence\",\"intervals_sec\":[60,180,300,600,1200,1800],\"fixed_interval_sec\":300,\"base_sec\":60,\"multiplier\":2.0,\"max_sec\":1800,\"jitter_sec\":0},\"auth\":{\"connect_timeout_sec\":10,\"request_timeout_sec\":15,\"stagger_sec\":0,\"session_invalid_wait_sec\":0},\"keepalive\":{\"interval_mode\":\"server\",\"interval_sec\":300,\"min_interval_sec\":30,\"max_interval_sec\":1800,\"failure_retries\":5,\"retry_interval_sec\":1,\"retry_jitter_sec\":0},\"probe\":{\"interval_sec\":2,\"connect_timeout_ms\":3000,\"request_timeout_ms\":4000,\"line_error_tolerance\":1,\"portal_confirmations\":1}}", NULL},
};

static uint8_t complete_object_from_template(cJSON* object, const cJSON* defaults, const char* prefix)
{
    uint8_t added = 0;
    for (const cJSON* def = defaults != NULL ? defaults->child : NULL; def != NULL; def = def->next)
    {
        cJSON* item = (cJSON*)cJSON_GetObjectItem(object, def->string);
        char key[96];
        snprintf(key, sizeof(key), "%s.%s", prefix, def->string);
        if (item == NULL)
        {
            cJSON* copy = cJSON_Duplicate(def, true);
            if (copy != NULL && cJSON_AddItemToObject(object, def->string, copy))
            {
                LOG_DEBUG("缺少 %s 参数, 已按默认值补上", key);
                added++;
            }
            else if (copy != NULL) cJSON_Delete(copy);
        }
        else if (cJSON_IsObject(item) && cJSON_IsObject(def))
        {
            added += complete_object_from_template(item, def, key);
        }
    }
    return added;
}

static const cfg_def_t s_account_defaults[] = {
    {"username",     CFG_DEF_STR, false, 0, "", NULL},
    {"password",     CFG_DEF_STR, false, 0, "", NULL},
    {"channel",      CFG_DEF_NUM, false, DEFAULT_CHANNEL, NULL, NULL},
    {"mark",         CFG_DEF_STR, false, 0, "", NULL},
    {"time_windows", CFG_DEF_ARR, false, 0, NULL, NULL},
};

void runtime_config_defaults(runtime_config_t* cfg)
{
    if (cfg == NULL) return;
    memset(cfg, 0, sizeof(*cfg));
    cfg->reconnect_enabled = true;
    cfg->reconnect_backoff = RETRY_SEQUENCE;
    cfg->reconnect_line_error_max_attempts = 5;
    cfg->reconnect_line_error_interval_sec = 10;
    const uint32_t seq[] = {60, 180, 300, 600, 1200, 1800};
    memcpy(cfg->reconnect_intervals_sec, seq, sizeof(seq));
    cfg->reconnect_interval_count = (uint8_t)(sizeof(seq) / sizeof(seq[0]));
    cfg->reconnect_fixed_interval_sec = 300;
    cfg->reconnect_base_sec = 60;
    cfg->reconnect_multiplier = 2.0;
    cfg->reconnect_max_sec = 1800;
    cfg->auth_connect_timeout_sec = 10;
    cfg->auth_request_timeout_sec = 15;
    cfg->keepalive_interval_mode = KEEP_INTERVAL_SERVER;
    cfg->keepalive_interval_sec = 300;
    cfg->keepalive_min_interval_sec = 30;
    cfg->keepalive_max_interval_sec = 1800;
    cfg->keepalive_failure_retries = 5;
    cfg->keepalive_retry_interval_sec = 1;
    cfg->probe_interval_sec = 2;
    cfg->probe_connect_timeout_ms = 3000;
    cfg->probe_request_timeout_ms = 4000;
    /* r7 每次探测直接采用结果，不做连续次数确认，缺省保持兼容。 */
    cfg->probe_line_error_tolerance = 1;
    cfg->probe_portal_confirmations = 1;
}

static uint32_t runtime_uint(const cJSON* obj, const char* key, const uint32_t fallback,
                             const uint32_t min, const uint32_t max)
{
    const cJSON* item = cJSON_GetObjectItem(obj, key);
    if (item == NULL) return fallback;
    if (!cJSON_IsNumber(item) || item->valuedouble < min || item->valuedouble > max ||
        (double)(uint32_t)item->valuedouble != item->valuedouble)
    {
        LOG_WARN("runtime.%s 参数非法，回退默认值 %u", key, fallback);
        return fallback;
    }
    return (uint32_t)item->valuedouble;
}

static double runtime_double(const cJSON* obj, const char* key, const double fallback,
                             const double min, const double max)
{
    const cJSON* item = cJSON_GetObjectItem(obj, key);
    if (item == NULL) return fallback;
    if (!cJSON_IsNumber(item) || item->valuedouble < min || item->valuedouble > max)
    {
        LOG_WARN("runtime.%s 参数非法，回退默认值 %.2f", key, fallback);
        return fallback;
    }
    return item->valuedouble;
}

void runtime_config_load(const cJSON* cfg_json, runtime_config_t* out)
{
    if (out == NULL) return;
    runtime_config_defaults(out);
    const cJSON* root = cJSON_GetObjectItem(cfg_json, "runtime");
    if (root != NULL && !cJSON_IsObject(root)) LOG_WARN("runtime 必须是对象，所有 runtime 参数回退默认值");
    const cJSON* reconnect = cJSON_IsObject(root) ? cJSON_GetObjectItem(root, "reconnect") : NULL;
    const cJSON* auth = cJSON_IsObject(root) ? cJSON_GetObjectItem(root, "auth") : NULL;
    const cJSON* keep = cJSON_IsObject(root) ? cJSON_GetObjectItem(root, "keepalive") : NULL;
    const cJSON* probe = cJSON_IsObject(root) ? cJSON_GetObjectItem(root, "probe") : NULL;
    if (reconnect != NULL && !cJSON_IsObject(reconnect)) LOG_WARN("runtime.reconnect 必须是对象，回退该组默认值");
    if (auth != NULL && !cJSON_IsObject(auth)) LOG_WARN("runtime.auth 必须是对象，回退该组默认值");
    if (keep != NULL && !cJSON_IsObject(keep)) LOG_WARN("runtime.keepalive 必须是对象，回退该组默认值");
    if (probe != NULL && !cJSON_IsObject(probe)) LOG_WARN("runtime.probe 必须是对象，回退该组默认值");
    if (!cJSON_IsObject(reconnect)) reconnect = NULL;
    if (!cJSON_IsObject(auth)) auth = NULL;
    if (!cJSON_IsObject(keep)) keep = NULL;
    if (!cJSON_IsObject(probe)) probe = NULL;

    const cJSON* enabled = cJSON_GetObjectItem(reconnect, "enabled");
    if (enabled != NULL)
    {
        if (cJSON_IsBool(enabled)) out->reconnect_enabled = cJSON_IsTrue(enabled);
        else LOG_WARN("runtime.reconnect.enabled 类型非法，回退默认值 true");
    }
    out->reconnect_max_attempts = runtime_uint(reconnect, "max_attempts", 0, 0, 1000000);
    out->reconnect_line_error_max_attempts = runtime_uint(reconnect, "line_error_max_attempts", 5, 0, 1000000);
    out->reconnect_line_error_interval_sec = runtime_uint(reconnect, "line_error_interval_sec", 10, 1, 3600);
    const cJSON* backoff = cJSON_GetObjectItem(reconnect, "backoff");
    if (backoff != NULL)
    {
        if (!cJSON_IsString(backoff)) LOG_WARN("runtime.reconnect.backoff 类型非法，回退 sequence");
        else if (strcmp(backoff->valuestring, "fixed") == 0) out->reconnect_backoff = RETRY_FIXED;
        else if (strcmp(backoff->valuestring, "exponential") == 0) out->reconnect_backoff = RETRY_EXPONENTIAL;
        else if (strcmp(backoff->valuestring, "sequence") != 0) LOG_WARN("runtime.reconnect.backoff 值非法，回退 sequence");
    }
    const cJSON* intervals = cJSON_GetObjectItem(reconnect, "intervals_sec");
    if (intervals != NULL)
    {
        uint32_t parsed[32];
        const int n = cJSON_IsArray(intervals) ? cJSON_GetArraySize(intervals) : 0;
        bool valid = n > 0 && n <= 32;
        for (int i = 0; valid && i < n; i++)
        {
            const cJSON* v = cJSON_GetArrayItem(intervals, i);
            if (!cJSON_IsNumber(v) || v->valuedouble < 1 || v->valuedouble > 86400 ||
                (double)(uint32_t)v->valuedouble != v->valuedouble) valid = false;
            else parsed[i] = (uint32_t)v->valuedouble;
        }
        if (valid)
        {
            memcpy(out->reconnect_intervals_sec, parsed, (size_t)n * sizeof(parsed[0]));
            out->reconnect_interval_count = (uint8_t)n;
        }
        else LOG_WARN("runtime.reconnect.intervals_sec 非法，回退默认序列");
    }
    out->reconnect_fixed_interval_sec = runtime_uint(reconnect, "fixed_interval_sec", 300, 1, 86400);
    out->reconnect_base_sec = runtime_uint(reconnect, "base_sec", 60, 1, 3600);
    out->reconnect_multiplier = runtime_double(reconnect, "multiplier", 2.0, 1.1, 5.0);
    out->reconnect_max_sec = runtime_uint(reconnect, "max_sec", 1800, 1, 86400);
    out->reconnect_jitter_sec = runtime_uint(reconnect, "jitter_sec", 0, 0, 3600);
    out->auth_connect_timeout_sec = runtime_uint(auth, "connect_timeout_sec", 10, 1, 120);
    out->auth_request_timeout_sec = runtime_uint(auth, "request_timeout_sec", 15, 1, 300);
    out->auth_stagger_sec = runtime_uint(auth, "stagger_sec", 0, 0, 600);
    out->session_invalid_wait_sec = runtime_uint(auth, "session_invalid_wait_sec", 0, 0, 86400);

    const cJSON* mode = cJSON_GetObjectItem(keep, "interval_mode");
    if (mode != NULL)
    {
        if (!cJSON_IsString(mode)) LOG_WARN("runtime.keepalive.interval_mode 类型非法，回退 server");
        else if (strcmp(mode->valuestring, "fixed") == 0) out->keepalive_interval_mode = KEEP_INTERVAL_FIXED;
        else if (strcmp(mode->valuestring, "clamped") == 0) out->keepalive_interval_mode = KEEP_INTERVAL_CLAMPED;
        else if (strcmp(mode->valuestring, "server") != 0) LOG_WARN("runtime.keepalive.interval_mode 值非法，回退 server");
    }
    out->keepalive_interval_sec = runtime_uint(keep, "interval_sec", 300, 30, 3600);
    out->keepalive_min_interval_sec = runtime_uint(keep, "min_interval_sec", 30, 10, 3600);
    out->keepalive_max_interval_sec = runtime_uint(keep, "max_interval_sec", 1800, 60, 7200);
    if (out->keepalive_min_interval_sec > out->keepalive_max_interval_sec)
    {
        LOG_WARN("runtime.keepalive.min_interval_sec 大于 max_interval_sec，回退默认边界");
        out->keepalive_min_interval_sec = 30;
        out->keepalive_max_interval_sec = 1800;
    }
    out->keepalive_failure_retries = runtime_uint(keep, "failure_retries", 5, 0, 20);
    out->keepalive_retry_interval_sec = runtime_uint(keep, "retry_interval_sec", 1, 1, 300);
    out->keepalive_retry_jitter_sec = runtime_uint(keep, "retry_jitter_sec", 0, 0, 60);
    out->probe_interval_sec = runtime_uint(probe, "interval_sec", 2, 1, 3600);
    out->probe_connect_timeout_ms = runtime_uint(probe, "connect_timeout_ms", 3000, 250, 30000);
    out->probe_request_timeout_ms = runtime_uint(probe, "request_timeout_ms", 4000, 500, 60000);
    if (out->probe_connect_timeout_ms > out->probe_request_timeout_ms)
    {
        LOG_WARN("probe.connect_timeout_ms 大于 request_timeout_ms，回退默认探测超时");
        out->probe_connect_timeout_ms = 3000;
        out->probe_request_timeout_ms = 4000;
    }
    out->probe_line_error_tolerance = runtime_uint(probe, "line_error_tolerance", 1, 1, 20);
    out->probe_portal_confirmations = runtime_uint(probe, "portal_confirmations", 1, 1, 10);
}

/**
 * @brief 把默认值渲染成日志里显示的文本
 * @param def 默认参数
 * @param out 输出缓冲
 * @param len 缓冲长度
 */
static void cfg_def_value_str(const cfg_def_t* def, char* out, const size_t len)
{
    switch (def->type)
    {
    case CFG_DEF_BOOL:
        snprintf(out, len, "%s", def->boolean ? "true" : "false");
        break;
    case CFG_DEF_NUM:
        if (def->unit != NULL) snprintf(out, len, "%d %s", def->number, def->unit);
        else snprintf(out, len, "%d", def->number);
        break;
    case CFG_DEF_STR:
        snprintf(out, len, "\"%s\"", safe_str(def->str));
        break;
    case CFG_DEF_OBJ:
        snprintf(out, len, "对象");
        break;
    default:
        snprintf(out, len, "[]");
        break;
    }
}

/**
 * @brief 把缺的参数补进一个配置对象里
 *
 * 只补【缺的键】: 已经写过的 (哪怕写的是空值或者不合法的值) 一律不动 ——
 * 那是用户自己的选择, 该报错就报错, 不该被程序悄悄改成"看起来正常"的样子
 * @param obj 配置对象 (原地补全)
 * @param defs 默认值表
 * @param def_cnt 默认值数量
 * @param who 日志前缀 (如 "配置里" / "配置 2 ")
 * @return 补上的参数个数
 */
static uint8_t complete_cfg_obj(cJSON* obj, const cfg_def_t* defs, const size_t def_cnt, const char* who)
{
    uint8_t added = 0;

    for (size_t i = 0; i < def_cnt; i++)
    {
        const cfg_def_t* def = &defs[i];

        if (cJSON_GetObjectItem(obj, def->name) != NULL) continue;

        bool ok = false;
        switch (def->type)
        {
        case CFG_DEF_BOOL: ok = cJSON_AddBoolToObject(obj, def->name, def->boolean) != NULL; break;
        case CFG_DEF_NUM:  ok = cJSON_AddNumberToObject(obj, def->name, def->number) != NULL; break;
        case CFG_DEF_STR:  ok = cJSON_AddStringToObject(obj, def->name, safe_str(def->str)) != NULL; break;
        case CFG_DEF_ARR:  ok = cJSON_AddArrayToObject(obj, def->name) != NULL; break;
        case CFG_DEF_OBJ:
        {
            cJSON* parsed = cJSON_Parse(def->str);
            if (parsed != NULL && cJSON_AddItemToObject(obj, def->name, parsed)) ok = true;
            else if (parsed != NULL) cJSON_Delete(parsed);
            break;
        }
        }

        // 补不上 (内存不够之类) 就按老路子走, 后面会打"参数不存在"的日志
        if (ok == false) continue;

        char value[TIME_WINDOW_STR_LEN];
        cfg_def_value_str(def, value, sizeof(value));
        LOG_DEBUG("%s缺少 %s 参数, 已按默认值 %s 补上", who, def->name, value);
        added++;
    }

    return added;
}

/**
 * @brief 补全配置文件里缺失的参数
 *
 * 手写的配置常常漏参数, 尤其是新版本加上去的 —— 不翻更新日志根本不知道有这回事。
 * 这里在读取配置的时候把缺的按默认值补上, 用户下次打开配置文件就能看见自己漏了什么。
 *
 * 补完由调用方写回文件; 写不回去也不影响本次运行 (值已经在内存里了)
 * @param cfg_json 配置对象 (原地补全)
 * @return 补上的参数个数
 */
uint8_t complete_cfg(cJSON* cfg_json)
{
    uint8_t added = complete_cfg_obj(cfg_json, s_cfg_defaults,
        sizeof(s_cfg_defaults) / sizeof(s_cfg_defaults[0]), "配置里");

    const cJSON* runtime = cJSON_GetObjectItem(cfg_json, "runtime");
    if (cJSON_IsObject(runtime))
    {
        cJSON* runtime_defaults = cJSON_Parse(s_cfg_defaults[7].str);
        if (runtime_defaults != NULL)
        {
            added += complete_object_from_template((cJSON*)runtime, runtime_defaults, "runtime");
            cJSON_Delete(runtime_defaults);
        }
    }

    const cJSON* accounts = cJSON_GetObjectItem(cfg_json, "accounts");
    if (accounts != NULL && cJSON_IsArray(accounts))
    {
        const int cnt = cJSON_GetArraySize(accounts);
        for (int i = 0; i < cnt; i++)
        {
            cJSON* account = cJSON_GetArrayItem(accounts, i);
            if (account == NULL || cJSON_IsObject(account) == false) continue;

            char who[32];
            snprintf(who, sizeof(who), "配置 %d ", i + 1);
            added += complete_cfg_obj(account, s_account_defaults,
                sizeof(s_account_defaults) / sizeof(s_account_defaults[0]), who);
        }
    }
    else if (accounts == NULL)
    {
        /**
         * accounts 整个缺了: 补一份账号模板, 用户把账号密码填上就能跑
         *
         * 模板里的字段不再逐个打"补上了", 一次刷五行日志没人爱看
         */
        cJSON* new_accounts = cJSON_AddArrayToObject(cfg_json, "accounts");
        cJSON* account = cJSON_CreateObject();
        if (new_accounts != NULL && account != NULL)
        {
            cJSON_AddStringToObject(account, "username", "");
            cJSON_AddStringToObject(account, "password", "");
            cJSON_AddNumberToObject(account, "channel", DEFAULT_CHANNEL);
            cJSON_AddStringToObject(account, "mark", "");
            cJSON_AddArrayToObject(account, "time_windows");
            cJSON_AddItemToArray(new_accounts, account);

            LOG_INFO("配置里缺少 accounts 参数, 已补上一份账号模板 (把 username 与 password 填上)");
            added++;
        }
        else if (account != NULL)
        {
            cJSON_Delete(account);
        }
    }

    return added;
}
