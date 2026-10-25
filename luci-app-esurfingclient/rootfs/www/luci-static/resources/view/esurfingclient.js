'use strict';
'require view';
'require fs';
'require ui';

return view.extend({
    load: function() {
        var self = this;

        return self.loadConfig();
    },
    
    render: function() {
        var self = this;

        var style = document.createElement('style');
        style.textContent = `
            .desc {
                font-size: 13px;
                margin-bottom: 5px;
            }

            .modal {
                min-width: 90% !important;
            }
        `;
        document.head.appendChild(style);
        
        var tabBar = E('ul', { class: 'cbi-tabmenu' }, [
            E('li', { class: 'cbi-tab', click: function() { self.switchTab('tab1'); } }, 
                E('a', {}, '常规')
            ),
            E('li', { class: 'cbi-tab-disabled', click: function() { self.switchTab('tab2'); } }, 
                E('a', {}, '账号')
            ),
            E('li', { class: 'cbi-tab-disabled', click: function() { self.switchTab('tab3'); } }, 
                E('a', {}, '日志')
            ),
            E('li', { class: 'cbi-tab-disabled', click: function() { self.switchTab('tab4'); } },
                E('a', {}, '运行时')
            )
        ]);

        self.logs = self.logs || [];

        self.config = self.config || {
            enabled: false,
            web_external_acc: false,
            log_lv: 4,
            log_dir: '/var/log/esurfing',
            conn_timeout: 3,
            op_timeout: 5,
            web_port: 8888,
            accounts: []
        };
        
        self.currentTab = 'tab1';

        self.basic_panel = E('div', { id: 'basic_panel', style: 'width: 100%' }, [
            E('h3', { style: 'margin-top: 0;' }, '基本设置'),
            E('div', { class: 'cbi-value' }, [
                E('label', { class: 'cbi-value-title' }, '启用'),
                E('div', { class: 'cbi-value-field' }, [
                    E('input', {
                        type: 'checkbox',
                        id: 'enabled',
                        checked: self.config.enabled ? true : undefined,
                        change: function(ev) {
                            self.config.enabled = ev.target.checked;
                        }
                    }),
                    E('div', { class: 'cbi-value-description' }, '启用 ESurfing 客户端')
                ])
            ]),
            E('div', { class: 'cbi-value' }, [
                E('label', { class: 'cbi-value-title' }, '日志等级'),
                E('div', { class: 'cbi-value-field' }, [
                    E('select', {
                        id: 'log_lv',
                        class: 'cbi-input-select',
                        change: function(ev) {
                            self.config.log_lv = parseInt(ev.target.value);
                        }
                    }, [
                        E('option', { value: 0, selected: self.config.log_lv === 0 ? true : undefined }, '0 - 关闭'),
                        E('option', { value: 1, selected: self.config.log_lv === 1 ? true : undefined }, '1 - 致命'),
                        E('option', { value: 2, selected: self.config.log_lv === 2 ? true : undefined }, '2 - 错误'),
                        E('option', { value: 3, selected: self.config.log_lv === 3 ? true : undefined }, '3 - 警告'),
                        E('option', { value: 4, selected: self.config.log_lv === 4 ? true : undefined }, '4 - 信息'),
                        E('option', { value: 5, selected: self.config.log_lv === 5 ? true : undefined }, '5 - 调试'),
                        E('option', { value: 6, selected: self.config.log_lv === 6 ? true : undefined }, '6 - 全部')
                    ]),
                    E('div', { class: 'cbi-value-description' }, '日志的详细程度: 3=警告, 4=信息, 5=调试, 6=全部'),
                    E('button', {
                        class: 'cbi-button cbi-button-action',
                        style: 'margin-top: 6px;',
                        click: function() {
                            self.enableTempDebug();
                        }
                    }, '临时调试 30 分钟'),
                    E('button', {
                        class: 'cbi-button',
                        style: 'margin-top: 6px; margin-left: 6px;',
                        click: function() {
                            self.saveConfig().then(function() {
                                self.showNotification('已保存，运行中的进程约 5 秒内热切换日志等级', 'info');
                            });
                        }
                    }, '热切换日志等级（不重启）')
                ])
            ]),
            E('div', { class: 'cbi-value' }, [
                E('label', { class: 'cbi-value-title' }, '连接超时'),
                E('div', { class: 'cbi-value-field' }, [
                    E('input', {
                        type: 'number',
                        id: 'conn_timeout',
                        class: 'cbi-input-text',
                        min: 1,
                        step: 1,
                        value: self.config.conn_timeout,
                        change: function(ev) {
                            self.config.conn_timeout = self.normalizeTimeout(ev.target.value, 3);
                            self.config.runtime = self.config.runtime || {};
                            self.config.runtime.auth = self.config.runtime.auth || {};
                            self.config.runtime.auth.connect_timeout_sec = self.config.conn_timeout;
                            ev.target.value = self.config.conn_timeout;
                        }
                    }),
                    E('div', { class: 'cbi-value-description' }, '连接阶段的超时时长 (单位: 秒)')
                ])
            ]),
            E('div', { class: 'cbi-value' }, [
                E('label', { class: 'cbi-value-title' }, '操作超时'),
                E('div', { class: 'cbi-value-field' }, [
                    E('input', {
                        type: 'number',
                        id: 'op_timeout',
                        class: 'cbi-input-text',
                        min: 1,
                        step: 1,
                        value: self.config.op_timeout,
                        change: function(ev) {
                            self.config.op_timeout = self.normalizeTimeout(ev.target.value, 5);
                            self.config.runtime = self.config.runtime || {};
                            self.config.runtime.auth = self.config.runtime.auth || {};
                            self.config.runtime.auth.request_timeout_sec = self.config.op_timeout;
                            ev.target.value = self.config.op_timeout;
                        }
                    }),
                    E('div', { class: 'cbi-value-description' }, '单次请求的总超时时长 (单位: 秒)')
                ])
            ]),
            E('div', { class: 'cbi-value' }, [
                E('label', { class: 'cbi-value-title' }, '日志目录'),
                E('div', { class: 'cbi-value-field' }, [
                    E('input', {
                        type: 'text',
                        id: 'log_dir',
                        class: 'cbi-input-text',
                        placeholder: '/var/log/esurfing',
                        value: self.config.log_dir,
                        change: function(ev) {
                            self.config.log_dir = ev.target.value;
                        }
                    }),
                    E('div', { class: 'cbi-value-description' }, [
                        '日志会存放在这个目录下的 logs 里; 默认 ',
                        E('code', {}, '/var/log/esurfing'),
                        ' (也就是日志在 ', E('code', {}, '/var/log/esurfing/logs'), ' 下, tmpfs, 重启即清)',
                        E('br'),
                        '相对路径按 ', E('code', {}, '/var/log/esurfing'), ' 解析; 改成闪存上的目录前请想清楚容量与写次数'
                    ])
                ])
            ])
        ]);

        self.tableBody = self.renderTable();

        /* 配置里没有 runtime 时，用默认值填充面板，让用户看到实际生效的数值 */
        if (self.config.runtime === null || typeof self.config.runtime !== 'object')
            self.config.runtime = self.runtimeDefaults();

        self.runtime_panel = self.renderRuntimePanel();

        self.accounts_panel = E('div', { id: 'accounts_panel', style: 'display: none' }, [
            E('h3', { style: 'margin-top: 0;' }, '账号设置'),
            E('table', { class: 'table cbi-section-table' }, [
                E('thead', { class: 'thead' }, [
                    E('tr', { class: 'tr cbi-section-table-titles' }, [
                        E('th', { class: 'th' }, '账号'),
                        E('th', { class: 'th' }, '密码'),
                        E('th', { class: 'th' }, '通道'),
                        E('th', { class: 'th' }, '标记值'),
                        E('th', { class: 'th' }, '时间控制'),
                        E('th', { class: 'th', style: 'width: 135px' }, '')
                    ])
                ]),
                self.tableBody
            ]),
            E('button', { class: 'cbi-button cbi-button-add', style: 'margin: 10px; margin-left: 15px;', click: function() {
                self.showModal(self.config.accounts.length);
            } }, '添加')
        ]);
        
        self.logs_selected = self.renderLogs();

        // 日志是否始终置底, 默认开启
        self.log_autoscroll = true;

        self.log_panel = E('div', { id: 'log_panel', style: 'display: none' }, [
            E('h3', { style: 'margin-top: 0;' }, '日志查看'),

            E('div', { class: 'cbi-value' }, [
                E('label', { class: 'cbi-value-title' }, '日志文件'),
                E('div', { class: 'cbi-value-field' }, [
                    self.logs_selected,
                    ' ',
                    E('button', {
                        class: 'cbi-button cbi-button-action',
                        click: function() { self.downloadLog(); }
                    }, '下载'),
                    E('div', { class: 'cbi-value-description' }, '日志目录按配置里的 log_dir 取 (默认 /var/log/esurfing/logs)')
                ])
            ]),

            E('div', { class: 'cbi-value' }, [
                E('label', { class: 'cbi-value-title' }, '日志置底'),
                E('div', { class: 'cbi-value-field' }, [
                    E('input', {
                        type: 'checkbox',
                        id: 'log-autoscroll-check',
                        checked: self.log_autoscroll ? true : undefined,
                        change: function(ev) {
                            self.log_autoscroll = ev.target.checked;
                            if (self.log_autoscroll) {
                                self.scrollLogToBottom();
                            }
                        }
                    }),
                    E('div', { class: 'cbi-value-description' }, '开启后日志有更新将会自动置底')
                ])
            ]),

            // 日志内容区域
            E('div', { class: 'cbi-section', style: 'margin-top: 10px;' }, [
                E('textarea', {
                    id: 'log_content',
                    class: 'cbi-input-textarea',
                    readonly: true,
                    rows: 20,
                    style: 'font-family: monospace; width: 100%;'
                }, '暂无日志, 或客户端未启动')
            ])
        ]);
        
        return [
            E('div', { class: 'cbi-section' }, [
                E('h2', 'ESurfing 客户端')
            ]),
            E('div', { style: 'margin-left: 25px;'}, [
                E('p', { class: 'desc' }, '用于方便地调整 ESurfing 程序的配置文件'),
                E('p', { class: 'desc' }, '账号密码与原电信认证程序的账号密码相同'),
                E('p', { class: 'desc' }, 'LuCI 版本: 2.2.0-r7'),
                E('p', { class: 'desc' }, '> Powered by BadGhost')
            ]),
            E('div', { class: 'cbi-section' }, [
                tabBar,
                self.basic_panel,
                self.accounts_panel,
                self.runtime_panel,
                self.log_panel
            ])
        ];
    },

    // 超时参数兜底, 非正数或非法值一律用默认值 (与后端 g_conn_timeout / g_op_timeout 一致)
    normalizeTimeout: function(value, fallback) {
        var timeout = parseInt(value, 10);
        return (isNaN(timeout) || timeout <= 0) ? fallback : timeout;
    },

    loadConfig: function() {
        var self = this;

        self.showNotification('正在读取配置文件', 'info');

        return fs.read('/etc/config/esurfingclient')
            .then(function(data) {
                try {
                    self.config = JSON.parse(data);
                } catch(e) {
                    self.config = {};
                }
                // 旧配置文件可能缺少超时参数, 这里补齐默认值, 避免输入框显示空值
                self.config.conn_timeout = self.normalizeTimeout(self.config.conn_timeout, 3);
                self.config.op_timeout = self.normalizeTimeout(self.config.op_timeout, 5);
                var runtimeAuth = self.config.runtime && self.config.runtime.auth;
                if (runtimeAuth) {
                    self.config.conn_timeout = self.normalizeTimeout(runtimeAuth.connect_timeout_sec, self.config.conn_timeout);
                    self.config.op_timeout = self.normalizeTimeout(runtimeAuth.request_timeout_sec, self.config.op_timeout);
                }
                self.showNotification('读取配置文件成功', 'success');
                return self.config;
            })
            .catch(function() {
                self.config = {
                    enabled: false,
                    web_external_acc: false,
                    log_lv: 0,
                    log_dir: '/var/log/esurfing',
                    conn_timeout: 3,
                    op_timeout: 5,
                    web_port: 8888,
                    accounts: [
                        {
                            username: '加载失败',
                            password: '加载失败',
                            channel: '加载失败',
                            mark: '加载失败',
                            time_windows: []
                        }
                    ]
                };
                self.showNotification('配置文件读取失败', 'error');
                return self.config;
            });
    },

    saveConfig: function() {
        var self = this;

        return fs.write('/etc/config/esurfingclient', JSON.stringify(self.config, null, 2));
    },

    /*
     * 临时调试: 把日志级别调到 DEBUG(5), 并记一个到期时间。
     * init 脚本启动时会检查 /tmp/esf_debug_until, 过期就自动降回 INFO(4),
     * 免得忘记关掉把 tmpfs 写满。
     */
    enableTempDebug: function() {
        var self = this;
        var until = Math.floor(Date.now() / 1000) + 1800;

        self.config.log_lv = 5;
        return fs.write('/tmp/esf_debug_until', String(until))
        .then(function() {
            return self.saveConfig();
        })
        .then(function() {
            return self.applyConfig();
        })
        .then(function() {
            self.showNotification('已开启调试日志, 30 分钟后自动恢复', 'info');
        })
        .catch(function() {
            self.showNotification('开启调试失败', 'error');
        });
    },

    applyConfig: function() {
        var restartCommand = L.rpc.declare({
            object: 'file',
            method: 'exec',
            params: ['command', 'params']
        });
        return restartCommand('/etc/init.d/esurfingclient', ['restart']);
    },

    refreshAccounts: function() {
        var self = this;

        if (self.config.enabled) {
            document.getElementById('enabled').setAttribute('checked', 'checked');
        } else {
            document.getElementById('enabled').removeAttribute('checked');
        }
        var newBody = self.renderTable();
        self.tableBody.parentNode.replaceChild(newBody, self.tableBody);
        self.tableBody = newBody;
    },

    /* ---------------- 运行时参数面板 ---------------- */

    /** 运行时参数的默认值（与 ConfigDefaults.c 保持一致） */
    runtimeDefaults: function() {
        return {
            reconnect: {
                enabled: true, max_attempts: 0, backoff: 'sequence',
                intervals_sec: [60, 180, 300, 600, 1200, 1800],
                fixed_interval_sec: 300, base_sec: 60, multiplier: 2.0,
                max_sec: 1800, jitter_sec: 15,
                line_error_max_attempts: 5, line_error_interval_sec: 10
            },
            auth: {
                connect_timeout_sec: 10, request_timeout_sec: 15, stagger_sec: 20, session_invalid_wait_sec: 0,
                portal_storm: { window_sec: 120, max_events: 3, backoff_sec: 30, backoff_max_sec: 600 }
            },
            keepalive: {
                interval_mode: 'server', interval_sec: 300, min_interval_sec: 30, max_interval_sec: 1800,
                failure_retries: 5, retry_interval_sec: 3, retry_jitter_sec: 1
            },
            probe: {
                interval_sec: 2, connect_timeout_ms: 3000, request_timeout_ms: 4000,
                line_error_tolerance: 3, portal_confirmations: 2
            },
            line_control: {
                enabled: true, down_on_session_invalid: true, down_on_auth_failed: false,
                restore_delay_sec: 30, restore_max_backoff_sec: 600
            },
            isolation: { rate_limit_sec: 1800 },
            log: { hot_reload: false }
        };
    },

    rtGet: function(path) {
        var parts = path.split('.'), o = this.config, i;
        for (i = 0; i < parts.length; i++) {
            if (o === null || typeof o !== 'object') return undefined;
            o = o[parts[i]];
        }
        return o;
    },

    rtSet: function(path, value) {
        var parts = path.split('.'), o = this.config, i;
        for (i = 0; i < parts.length - 1; i++) {
            if (o[parts[i]] === null || typeof o[parts[i]] !== 'object') o[parts[i]] = {};
            o = o[parts[i]];
        }
        o[parts[parts.length - 1]] = value;
    },

    rtNum: function(title, path, desc, step) {
        var self = this, v = self.rtGet(path);
        return E('div', { class: 'cbi-value' }, [
            E('label', { class: 'cbi-value-title' }, title),
            E('div', { class: 'cbi-value-field' }, [
                E('input', {
                    type: 'number', class: 'cbi-input-text', step: step || 1,
                    value: (v === undefined || v === null) ? '' : v,
                    change: function(ev) { self.rtSet(path, parseInt(ev.target.value, 10) || 0); }
                }),
                desc ? E('div', { class: 'cbi-value-description' }, desc) : ''
            ])
        ]);
    },

    rtBool: function(title, path, desc) {
        var self = this, v = self.rtGet(path);
        return E('div', { class: 'cbi-value' }, [
            E('label', { class: 'cbi-value-title' }, title),
            E('div', { class: 'cbi-value-field' }, [
                E('input', {
                    type: 'checkbox', checked: v ? true : undefined,
                    change: function(ev) { self.rtSet(path, ev.target.checked); }
                }),
                desc ? E('div', { class: 'cbi-value-description' }, desc) : ''
            ])
        ]);
    },

    rtSelect: function(title, path, options, desc) {
        var self = this, v = self.rtGet(path);
        return E('div', { class: 'cbi-value' }, [
            E('label', { class: 'cbi-value-title' }, title),
            E('div', { class: 'cbi-value-field' }, [
                E('select', { class: 'cbi-input-select', change: function(ev) { self.rtSet(path, ev.target.value); } },
                    options.map(function(o) {
                        return E('option', { value: o.value, selected: v === o.value ? true : undefined }, o.label);
                    })),
                desc ? E('div', { class: 'cbi-value-description' }, desc) : ''
            ])
        ]);
    },

    rtList: function(title, path, desc) {
        var self = this, v = self.rtGet(path);
        return E('div', { class: 'cbi-value' }, [
            E('label', { class: 'cbi-value-title' }, title),
            E('div', { class: 'cbi-value-field' }, [
                E('input', {
                    type: 'text', class: 'cbi-input-text',
                    value: Array.isArray(v) ? v.join(',') : '',
                    change: function(ev) {
                        var arr = ev.target.value.split(',')
                            .map(function(x) { return parseInt(x.trim(), 10); })
                            .filter(function(n) { return n > 0; });
                        self.rtSet(path, arr);
                    }
                }),
                desc ? E('div', { class: 'cbi-value-description' }, desc) : ''
            ])
        ]);
    },

    renderRuntimePanel: function() {
        var self = this;
        return E('div', { id: 'runtime_panel', style: 'display: none' }, [
            E('h3', { style: 'margin-top: 0;' }, '运行时参数'),
            E('div', { class: 'cbi-value-description', style: 'margin-bottom: 10px' },
                '这些参数作用于本机所有账号；留空或非法值会回退到默认值。'),

            E('h4', {}, '认证'),
            self.rtNum('首次认证错峰', 'runtime.auth.stagger_sec', '账号 N 延迟 (N-1)×该秒数后开始认证；默认 20 秒，避免同时登录触发限流'),
            self.rtNum('会话失效后等待', 'runtime.auth.session_invalid_wait_sec', '秒；0 表示不额外等待'),
            self.rtNum('连接超时', 'runtime.auth.connect_timeout_sec', '秒'),
            self.rtNum('请求超时', 'runtime.auth.request_timeout_sec', '秒'),
            self.rtNum('302 统计窗口', 'runtime.auth.portal_storm.window_sec', '秒；偶发一次门户重定向仍立即重登，窗口内反复出现才退避'),
            self.rtNum('窗口内允许次数', 'runtime.auth.portal_storm.max_events', '超过这个次数开始退避；默认 3'),
            self.rtNum('退避起始时长', 'runtime.auth.portal_storm.backoff_sec', '秒；首次超限后等待多久，默认 30'),
            self.rtNum('退避上限', 'runtime.auth.portal_storm.backoff_max_sec', '秒；指数增长的上限，默认 600'),

            E('h4', {}, '重连'),
            self.rtBool('启用自动重连', 'runtime.reconnect.enabled', '关闭后认证失败只尝试一次，进入停止状态'),
            self.rtSelect('退避策略', 'runtime.reconnect.backoff', [
                { value: 'sequence', label: '自定义序列' },
                { value: 'fixed', label: '固定间隔' },
                { value: 'exponential', label: '指数退避' }
            ]),
            self.rtList('自定义序列', 'runtime.reconnect.intervals_sec', '逗号分隔的秒数，如 60,180,300,600,1200,1800'),
            self.rtNum('固定间隔', 'runtime.reconnect.fixed_interval_sec', '秒；仅固定间隔策略使用'),
            self.rtNum('初始间隔', 'runtime.reconnect.base_sec', '秒；仅指数退避使用'),
            self.rtNum('退避倍数', 'runtime.reconnect.multiplier', '仅指数退避使用', 0.1),
            self.rtNum('最大间隔', 'runtime.reconnect.max_sec', '秒'),
            self.rtNum('随机抖动', 'runtime.reconnect.jitter_sec', '秒；每次重试额外随机等待，避免多账号同步；默认 15'),
            self.rtNum('最大重试次数', 'runtime.reconnect.max_attempts', '0 表示不限'),
            self.rtNum('线路错误重试上限', 'runtime.reconnect.line_error_max_attempts', '0 表示不限'),
            self.rtNum('线路错误间隔', 'runtime.reconnect.line_error_interval_sec', '秒'),

            E('h4', {}, '心跳'),
            self.rtSelect('周期模式', 'runtime.keepalive.interval_mode', [
                { value: 'server', label: '跟随服务端' },
                { value: 'fixed', label: '固定周期' },
                { value: 'clamped', label: '服务端值夹取' }
            ]),
            self.rtNum('固定周期', 'runtime.keepalive.interval_sec', '秒；仅固定周期使用'),
            self.rtNum('周期下限', 'runtime.keepalive.min_interval_sec', '秒'),
            self.rtNum('周期上限', 'runtime.keepalive.max_interval_sec', '秒'),
            self.rtNum('失败重试次数', 'runtime.keepalive.failure_retries', '连续失败多少次判定会话失效'),
            self.rtNum('重试间隔', 'runtime.keepalive.retry_interval_sec', '秒；默认 3，给网络抖动留窗口'),
            self.rtNum('重试抖动', 'runtime.keepalive.retry_jitter_sec', '秒'),

            E('h4', {}, '探测'),
            self.rtNum('探测间隔', 'runtime.probe.interval_sec', '秒'),
            self.rtNum('连接超时', 'runtime.probe.connect_timeout_ms', '毫秒'),
            self.rtNum('请求超时', 'runtime.probe.request_timeout_ms', '毫秒'),
            self.rtNum('连续失败容忍', 'runtime.probe.line_error_tolerance', '连续多少次失败才算线路故障；默认 3'),
            self.rtNum('门户确认次数', 'runtime.probe.portal_confirmations', '连续多少次 302 才认定需要认证；默认 2'),

            E('h4', {}, '线路控制'),
            self.rtBool('启用线路控制', 'runtime.line_control.enabled', '需要给账号配置 iface 才会生效'),
            self.rtBool('会话失效时下线', 'runtime.line_control.down_on_session_invalid', '把接口 down 掉，让 mwan3 自动摘线'),
            self.rtBool('认证失败时下线', 'runtime.line_control.down_on_auth_failed'),
            self.rtNum('恢复延迟', 'runtime.line_control.restore_delay_sec', '秒'),
            self.rtNum('恢复退避上限', 'runtime.line_control.restore_max_backoff_sec', '秒'),

            E('h4', {}, '隔离'),
            self.rtNum('限流隔离时长', 'runtime.isolation.rate_limit_sec', '秒；被 11064000 限流后暂停该账号多久'),

            E('h4', {}, '日志'),
            self.rtBool('启用日志等级热切换', 'runtime.log.hot_reload',
                '开启后每个进程每秒检查一次配置文件，改了日志等级立即生效、不用重启；关闭则完全不读配置')
        ]);
    },

    renderTable: function() {
        var self = this;
        
        self.config.accounts = self.config.accounts || [];
        var rows = self.config.accounts.map(function(account, index) {
            return E('tr', { class: 'tr', 'data-index': index }, [
                E('td', { class: 'td' }, account.username || '(无)'),
                E('td', { class: 'td' }, account.password ? '******' : '(无)'),
                E('td', { class: 'td' }, account.channel),
                E('td', { class: 'td' }, account.mark || '(无)'),
                E('td', { class: 'td' }, (account.time_windows && account.time_windows.length) ? account.time_windows.length + ' 个时段' : '(不限)'),
                E('td', { class: 'td' }, [
                    E('button', { class: 'cbi-button cbi-button-edit', click: function() {
                        self.showModal(index);
                    } }, '编辑'),
                    E('button', { class: 'cbi-button cbi-button-remove', click: function() {
                        self.config.accounts.splice(index, 1);
                        self.refreshAccounts();
                    } }, '删除')
                ])
            ]);
        });
        
        return E('tbody', { class: 'tbody' }, rows);
    },

    defaultEditTimeWindow: function() {
        return { startDay: '', startTime: '', endDay: '', endTime: '' };
    },

    timeWindowsToEdit: function(windows) {
        var self = this;
        return (windows || []).map(function(window) {
            var startParts = (window.start || '').split(' ');
            var endParts = (window.end || '').split(' ');
            return {
                startDay: startParts[0] || '',
                startTime: startParts[1] || '',
                endDay: endParts[0] || '',
                endTime: endParts[1] || ''
            };
        });
    },

    editToTimeWindows: function(editWindows) {
        var windows = [];
        for (var i = 0; i < editWindows.length; i++) {
            var edit = editWindows[i];
            if (!edit.startDay || !edit.startTime || !edit.endDay || !edit.endTime) {
                throw new Error('存在未填写完整的时间段，请补全或删除该时间段');
            }

            var start = edit.startDay.toLowerCase() + ' ' + edit.startTime;
            var end = edit.endDay.toLowerCase() + ' ' + edit.endTime;
            if (start === end) {
                throw new Error('时间段开始和结束不能相同：' + start);
            }

            windows.push({ start: start, end: end });
        }
        return windows;
    },

    createDaySelect: function(id, selected) {
        var days = [
            { value: '', label: '星期' },
            { value: 'mon', label: '周一' },
            { value: 'tue', label: '周二' },
            { value: 'wed', label: '周三' },
            { value: 'thu', label: '周四' },
            { value: 'fri', label: '周五' },
            { value: 'sat', label: '周六' },
            { value: 'sun', label: '周日' }
        ];
        var options = days.map(function(day) {
            return E('option', { value: day.value, selected: selected === day.value ? true : undefined }, day.label);
        });
        return E('select', { id: id, class: 'cbi-input-select' }, options);
    },

    renderTimeWindows: function() {
        var self = this;
        var container = document.getElementById('time-windows-container');
        if (!container) return;
        container.innerHTML = '';

        self.currentTimeWindows.forEach(function(edit, index) {
            var row = E('div', { style: 'display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:8px;' }, [
                E('span', {}, '开始'),
                self.createDaySelect('tw_start_day_' + index, edit.startDay),
                E('input', { type: 'time', id: 'tw_start_time_' + index, class: 'cbi-input-text', value: edit.startTime }),
                E('span', {}, '结束'),
                self.createDaySelect('tw_end_day_' + index, edit.endDay),
                E('input', { type: 'time', id: 'tw_end_time_' + index, class: 'cbi-input-text', value: edit.endTime }),
                E('button', { class: 'cbi-button cbi-button-remove', click: function() { self.removeTimeWindow(index); } }, '删除')
            ]);
            container.appendChild(row);
        });
    },

    addTimeWindow: function() {
        var self = this;
        if (self.currentTimeWindows.length >= 16) {
            self.showNotification('最多支持 16 个时间段', 'error');
            return;
        }
        self.currentTimeWindows.push(self.defaultEditTimeWindow());
        self.renderTimeWindows();
    },

    removeTimeWindow: function(index) {
        var self = this;
        self.currentTimeWindows.splice(index, 1);
        self.renderTimeWindows();
    },

    showModal: function(index) {
        var self = this;

        var account = {};
        var add_mode = false;
        if (self.config.accounts[index]) {
            account = self.config.accounts[index];
        } else {
            add_mode = true;
            account = {
                username: '',
                password: '',
                channel: 'phone',
                mark: '',
                time_windows: []
            };
        }

        self.currentTimeWindows = self.timeWindowsToEdit(account.time_windows);

        var modal = L.showModal('编辑账号', [
            E('div', { class: 'cbi-value', style: 'margin-top: 25px;' }, [
                E('label', { class: 'cbi-value-title', style: 'margin-top: 10px;' }, '*账号'),
                E('div', { class: 'cbi-value-field' }, [
                    E('input', { type: 'text', class: 'cbi-input-text', value: account.username, placeholder: '请输入账号', id: 'edit_account' }),
                    E('div', { class: 'cbi-value-description' }, '和官方认证程序的账号相同')
                ])
            ]),
            E('div', { class: 'cbi-value' }, [
                E('label', { class: 'cbi-value-title', style: 'margin-top: 10px;' }, '*密码'),
                E('div', { class: 'cbi-value-field' }, [
                    E('input', { type: 'password', class: 'cbi-input-text', value: account.password, placeholder: '请输入密码', id: 'edit_password' }),
                    E('div', { class: 'cbi-value-description' }, '和官方认证程序的密码相同')
                ])
            ]),
            E('div', { class: 'cbi-value' }, [
                E('label', { class: 'cbi-value-title', style: 'margin-top: 10px;' }, '*通道'),
                E('div', { class: 'cbi-value-field' }, [
                    E('select', { id: 'edit_channel', class: 'cbi-input-select' }, [
                        E('option', { value: 'windows', selected: (account.channel === 1 || account.channel === '1' || account.channel === 'windows') ? true : undefined }, 'Windows'),
                        E('option', { value: 'linux', selected: (account.channel === 2 || account.channel === '2' || account.channel === 'linux') ? true : undefined }, 'Linux'),
                        E('option', { value: 'android', selected: (account.channel === 3 || account.channel === '3' || account.channel === 'android') ? true : undefined }, 'Android'),
                        E('option', { value: 'ios', selected: (account.channel === 4 || account.channel === '4' || account.channel === 'ios' || account.channel === 'iphone') ? true : undefined }, 'iOS'),
                        E('option', { value: 'macos', selected: (account.channel === 5 || account.channel === '5' || account.channel === 'macos' || account.channel === 'mac' || account.channel === 'osx') ? true : undefined }, 'MacOS')
                    ]),
                    E('div', { class: 'cbi-value-description' }, '选择账号的认证通道')
                ])
            ]),
            E('div', { class: 'cbi-value' }, [
                E('label', { class: 'cbi-value-title', style: 'margin-top: 10px;' }, '标记值'),
                E('div', { class: 'cbi-value-field' }, [
                    E('input', { type: 'text', class: 'cbi-input-text', value: account.mark, placeholder: '请输入标记值', id: 'edit_mark' }),
                    E('div', { class: 'cbi-value-description' }, [
                        '可选项, 用于 MWAN 多路区分',
                        E('br'),
                        '十六进制数, 以 0x 开头, 例如 0x100'
                    ])
                ])
            ]),
            E('div', { class: 'cbi-value' }, [
                E('label', { class: 'cbi-value-title', style: 'margin-top: 10px;' }, '时间控制'),
                E('div', { class: 'cbi-value-field' }, [
                    E('div', { id: 'time-windows-container' }),
                    E('button', { class: 'cbi-button cbi-button-add', click: function() { self.addTimeWindow(); } }, '添加时间段'),
                    E('div', { class: 'cbi-value-description' }, '未设置时间段 = 不限制')
                ])
            ]),
            E('div', { style: 'text-align: right; margin-top: 20px; padding-top: 10px;' }, [
                E('button', { class: 'cbi-button cbi-button-neutral', click: function() {
                    L.hideModal(modal);
                } }, '关闭'),
                ' ',
                E('button', { class: 'cbi-button cbi-button-apply', click: function() {
                    var editWindows = [];
                    for (var i = 0; i < self.currentTimeWindows.length; i++) {
                        editWindows.push({
                            startDay: document.getElementById('tw_start_day_' + i).value,
                            startTime: document.getElementById('tw_start_time_' + i).value,
                            endDay: document.getElementById('tw_end_day_' + i).value,
                            endTime: document.getElementById('tw_end_time_' + i).value
                        });
                    }

                    var timeWindows;
                    try {
                        timeWindows = self.editToTimeWindows(editWindows);
                    } catch (error) {
                        self.showNotification(error.message, 'error');
                        return;
                    }

                    account.username = document.getElementById('edit_account').value;
                    account.password = document.getElementById('edit_password').value;
                    account.channel = document.getElementById('edit_channel').value;
                    account.mark = document.getElementById('edit_mark').value;
                    account.time_windows = timeWindows;
                    
                    if (add_mode) {
                        self.config.accounts.push(account);
                    }

                    self.refreshAccounts();
                    L.hideModal(modal);
                } }, '保存')
            ])
        ]);

        setTimeout(function() { self.renderTimeWindows(); }, 0);
    },

    scrollLogToBottom: function() {
        var log_area = document.getElementById('log_content');
        if (log_area) {
            log_area.scrollTop = log_area.scrollHeight;
        }
    },

    downloadLog: function() {
        var log_area = document.getElementById('log_content');
        if (!log_area) return;
        var select = document.getElementById('log_file');
        var filename = (select && select.value) ? select.value : 'run.log';

        var blob = new Blob([log_area.value || ''], { type: 'text/plain;charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    },

    startLogAutoRefresh: function() {
        var self = this;

        if (self.logTimer) clearInterval(self.logTimer);
        self.logTimer = setInterval(function() {
            if (self.currentTab === 'tab3') {
                self.refreshLogs();
                self.loadLogContent();
            }
        }, 5000);
    },

    stopLogAutoRefresh: function() {
        var self = this;

        if (self.logTimer) {
            clearInterval(self.logTimer);
            self.logTimer = null;
        }
    },
    
    /**
     * 实际使用的日志目录
     *
     * 与程序里的规则一致 (Logger.c 的 resolve_log_dir + get_log_dir):
     * 配置里的 log_dir 是【基目录】, 日志放在它下面的 logs 里;
     * 没写 / 写成 "." / "./" 时用默认的 /var/log/esurfing; 相对路径也按它解析。
     * OpenWrt 上不按程序所在目录解析 —— 那边程序装在只读的 /usr/bin 里。
     */
    logDirPath: function() {
        var base = (this.config && typeof this.config.log_dir === 'string') ? this.config.log_dir.trim() : '';

        if (base === '' || base === '.' || base === './') {
            base = '/var/log/esurfing';
        } else if (base.charAt(0) !== '/') {
            base = '/var/log/esurfing/' + base.replace(/^\.\//, '');
        }

        return base.replace(/\/+$/, '') + '/logs';
    },

    loadLogContent: function() {
        var self = this;

        var textarea = document.getElementById('log_content');
        if (!textarea) return;
        var log_select = document.getElementById('log_file');
        if (!log_select || !log_select.value) {
            textarea.value = '请选择一个日志文件';
            return;
        }
        var path = self.logDirPath() + '/' + log_select.value;

        /*
         * 先看大小再决定读不读。
         *
         * LuCI 的 fs.read_direct 会把整个文件读出来再经 rpcd 传过来，
         * 日志开到调试级别时单个文件可能上 MB，直接读会让页面卡很久。
         */
        fs.stat(path).then(function(st) {
            var size = (st && st.size) ? st.size : 0;
            if (size > 1024 * 1024) {
                textarea.value = '日志文件较大（' + (size / 1048576).toFixed(1) + ' MB），' +
                    '为避免界面卡顿没有自动加载。\n\n' +
                    '在路由器上查看尾部内容：\n  tail -c 262144 ' + path + '\n\n' +
                    '或者把日志级别调低、清理旧日志后重试。';
                return null;
            }
            return fs.read_direct(path);
        })
        .then(function(data) {
            if (data === null || typeof data === 'undefined') return;
            textarea.value = data || '暂无日志, 或客户端未启动';
            if (self.log_autoscroll) {
                self.scrollLogToBottom();
            }
        })
        .catch(function() {
            textarea.value = '无法读取日志文件';
            self.showNotification('日志文件读取失败', 'error');
        });
    },

    refreshLogs: function() {
        var self = this;

        fs.list(self.logDirPath())
        .then(function(entries) {
            var new_logs = [];
            for (var i = 0; i < entries.length; i++) {
                if (entries[i].type === 'file') {
                    new_logs.push(entries[i].name);
                }
            }
            new_logs.sort((a, b) => b.localeCompare(a));
            if (!self.arraysEqual(self.logs, new_logs)) {
                self.logs = new_logs;
                var new_logs_selected = self.renderLogs();
                self.logs_selected.parentNode.replaceChild(new_logs_selected, self.logs_selected);
                self.logs_selected = new_logs_selected;
                document.getElementById('log_file').value = self.logs[0];
            }
        });
    },

    renderLogs: function() {
        var self = this;

        self.logs = self.logs || [];
        /*
         * 默认选最新的那个文件。
         *
         * 服务每次停止都会把 run.log 归档成时间戳文件，再重启时 run.log 是空的；
         * 固定选 run.log 会让日志页看起来"没有输出"，其实内容都在归档文件里。
         */
        var current = self.selected_log || self.logs[0] || 'run.log';
        var rows = self.logs.map(function(log) {
            return E('option', { value: log, selected: log === current ? true : undefined }, log);
        });
        if (self.logs.indexOf(current) < 0) current = self.logs[0] || 'run.log';

        return E('select', {
            id: 'log_file',
            class: 'cbi-input-select',
            style: 'width: auto; min-width: 200px; max-width: 400px;',
            value: current,
            change: function(ev) {
                self.selected_log = ev.target.value;
                self.loadLogContent();
            }
        }, rows);
    },

    arraysEqual: function(a, b) {
        if (!a || !b) return a === b;
        if (a.length !== b.length) return false;
        for (var i = 0; i < a.length; i++) {
            if (a[i] !== b[i]) return false;
        }
        return true;
    },

    switchTab: function(tabName) {
        var self = this;

        var names = ['tab1', 'tab2', 'tab3', 'tab4'];
        var panels = [self.basic_panel, self.accounts_panel, self.log_panel, self.runtime_panel];
        var idx = names.indexOf(tabName);
        if (idx < 0) idx = 0;

        self.stopLogAutoRefresh();

        var tabs = document.querySelectorAll('.cbi-tab, .cbi-tab-disabled');
        for (var i = 0; i < tabs.length && i < names.length; i++) {
            if (i === idx) {
                tabs[i].classList.remove('cbi-tab-disabled');
                tabs[i].classList.add('cbi-tab');
            } else {
                tabs[i].classList.remove('cbi-tab');
                tabs[i].classList.add('cbi-tab-disabled');
            }
        }

        for (var j = 0; j < panels.length; j++) {
            if (panels[j]) panels[j].style.display = (j === idx) ? 'block' : 'none';
        }
        self.currentTab = names[idx];

        if (idx === 2) {
            var check = document.getElementById('log-autoscroll-check');
            if (check) check.checked = self.log_autoscroll;
            self.showNotification('正在读取日志', 'info');
            self.refreshLogs();
            self.startLogAutoRefresh();
        }
    },

    showNotification: function(message, type) {
        if (typeof ui.showNotification === 'function') {
            ui.showNotification(message, type || 'info');
            return;
        }
        var container = document.getElementById('notification-area');
        if (!container) {
            container = document.createElement('div');
            container.id = 'notification-area';
            container.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999;';
            document.body.appendChild(container);
        }
        var note = document.createElement('div');
        note.className = 'alert-message ' + (type === 'success' ? 'success' : type === 'error' ? 'error' : 'info');
        note.style.cssText = 'margin-bottom:8px;padding:10px 15px;min-width:200px;opacity:0;transition:opacity 0.3s;';
        if (type === 'info') {
            note.textContent = message + ' ~(￣▽￣~)~';
        } else if (type === 'success') {
            note.textContent = message + ' ≧∇≦';
        } else if (type === 'error') {
            note.textContent = message + ' >_<';
        }
        container.appendChild(note);
        setTimeout(function() { note.style.opacity = '1'; }, 10);
        setTimeout(function() {
            note.style.opacity = '0';
            setTimeout(function() { if (note.parentNode) note.parentNode.removeChild(note); }, 300);
        }, 3000);
    },
    
    handleSaveApply: function(ev) {
        var self = this;

        self.showNotification('保存并应用配置中', 'info');
        return self.saveConfig()
            .then(function() {
                return self.applyConfig();
            })
            .then(function() {
                self.showNotification('配置已保存并应用，服务已重启 ', 'success');
                setTimeout(function() {
                    location.reload();
                }, 2000);
            })
            .catch(function(e) {
                self.showNotification('操作失败: ' + e.message, 'error');
                setTimeout(function() {
                    location.reload();
                }, 2000);
            });
        
        return false;
    },

    handleSave: function(ev) {
        var self = this;

        self.showNotification('保存配置中', 'info');
        return self.saveConfig()
            .then(function() {
                self.showNotification('配置已保存', 'success');
                setTimeout(function() {
                    location.reload();
                }, 2000);
            })
            .catch(function(e) {
                self.showNotification('配置保存失败: ' + e.message, 'error');
                setTimeout(function() {
                    location.reload();
                }, 2000);
            });

        return false;
    },

    handleReset: function(ev) {
        var self = this;

        var modal = L.showModal('重置配置', [
            E('p', {
                style: 'color: red; margin-top: 30px; font-size: 20px; font-weight: bold; text-align: center;'
            }, '确定要重置配置到默认值吗? ∑(°Д°) 此操作不可逆!'),
            E('div', { style: 'text-align: right; margin-top: 20px; padding-top: 10px;' }, [
                E('button', { class: 'cbi-button cbi-button-neutral', click: function() {
                    L.hideModal(modal);
                } }, '取消'),
                ' ',
                E('button', { class: 'cbi-button cbi-button-reset', click: function() {
                    L.hideModal(modal);
                    self.showNotification('重置配置中', 'info');
                    self.config = {
                        enabled: false,
                        web_external_acc: false,
                        log_lv: 4,
                        log_dir: '/var/log/esurfing',
                        conn_timeout: 3,
                        op_timeout: 5,
                        web_port: 8888,
                        accounts: [
                            {
                                username: '',
                                password: '',
                                channel: 'phone',
                                mark: '',
                                time_windows: []
                            }
                        ]
                    };

                    self.refreshAccounts();
                    
                    return self.saveConfig()
                        .then(function() {
                            self.showNotification('配置重置成功, 已重置到默认值', 'success');
                            setTimeout(function() {
                                location.reload();
                            }, 2000);
                        })
                        .catch(function(e) {
                            self.showNotification('配置重置失败: ' + e.message, 'error');
                            setTimeout(function() {
                                location.reload();
                            }, 2000);
                        });
                } }, '重置'),
                E('div', { style: 'text-align: right; font-size: 14px; color: #969696;' }, '真的不可逆喔...')
            ])
        ]);

        return false;
    },
});
