#!/bin/sh
# 冒烟测试：会话失效 → 自动 mwan3 ifdown → 恢复 → mwan3 ifup
#
# 前置：VM 已建好 dummy0 + wan2_dev/wan3_dev、已装 mwan3 与 esurfingclient；
#      宿主机 lab/ 目录通过 8899 端口对 VM 可见（python3 -m http.server）。
#
# 用法：tests/vm/smoke-line-control.sh
#
# 通过标准：
#   1. 日志出现 "mwan3 ifdown wan2 成功"
#   2. 摘线后 `ip rule show | grep "lookup 5"` 归零
#   3. 设备地址仍在（摘线不碰网卡和 IP）
#   4. 日志出现 "mwan3 ifup wan2 成功"，且规则数恢复

set -e
HERE=$(cd "$(dirname "$0")" && pwd)
VM="$HERE/vm-exec"
MERGE=$(cd "$HERE/../.." && pwd)
ESF=$(cd "$MERGE/.." && pwd)
PORT=8082
LINE=wan2
DEV=wan2_dev
MARK=0x500
TABLE=5
TAG=smoke-$$

echo "== 1. 启动 mock（会话 10 秒后失效）=="
pkill -f "tests/mock/server.py" 2>/dev/null || true
sleep 1
mkdir -p /tmp/mock-evi
"$ESF/.venv/bin/python" "$MERGE/tests/mock/server.py" \
    --scenario session_expire_after --after 10 \
    --host 127.0.0.1 --port "$PORT" \
    --public-base "http://10.0.2.2:$PORT" \
    --log-file /tmp/mock-evi/$TAG.jsonl > /tmp/mock-evi/$TAG.out 2>&1 &
sleep 2

echo "== 2. VM 侧：DNAT / 路由 / 客户端配置 =="
"$VM" "sh -c '
IPS=\"183.61.185.93, 125.88.255.53, 202.69.4.32, 220.181.104.183, 14.146.227.141, 121.8.177.212\"
nft delete table ip nat 2>/dev/null
nft add table ip nat
nft \"add chain ip nat out { type nat hook output priority -100; }\"
nft \"add chain ip nat postr { type nat hook postrouting priority 100; }\"
nft add rule ip nat out meta mark $MARK ip daddr { \$IPS } tcp dport { 80, 7001 } dnat to 10.0.2.2:$PORT
nft add rule ip nat postr oifname \"eth0\" masquerade
ip route replace default via 10.0.2.2 dev eth0
ip route replace default via 10.0.2.2 dev eth0 table $TABLE
mwan3 ifup $LINE >/dev/null 2>&1
cat > /etc/config/esurfingclient <<JSON
{
  \"enabled\": true, \"log_lv\": 5, \"log_dir\": \"/var/log/esurfing\",
  \"conn_timeout\": 3, \"op_timeout\": 5, \"web_port\": 8888,
  \"runtime\": { \"probe\": { \"portal_confirmations\": 1, \"line_error_tolerance\": 1 } },
  \"accounts\": [
    { \"username\": \"u1\", \"password\": \"p1\", \"channel\": \"android\", \"mark\": \"$MARK\",
      \"iface\": \"$DEV\", \"mwan3_iface\": \"$LINE\", \"time_windows\": [] }
  ]
}
JSON
rm -f /var/log/esurfing/logs/run.log
killall esurfingclient 2>/dev/null
sleep 1
setsid /usr/bin/esurfingclient --role auth --account 1 >/tmp/smoke.log 2>&1 </dev/null &
'" >/dev/null

echo "== 3. 等待失效与摘线 =="
sleep 45
DOWN_LOG=$("$VM" "grep -c 'mwan3 ifdown $LINE 成功' /var/log/esurfing/logs/run.log 2>/dev/null || true")
RULES_DOWN=$("$VM" "ip rule show | grep -c 'lookup $TABLE' || true")
HAS_IP=$("$VM" "ip -4 addr show $DEV | grep -c inet || true")
echo "  ifdown 日志=$DOWN_LOG  摘线后规则数=$RULES_DOWN  设备地址=$HAS_IP"

echo "== 4. 等待自动恢复 =="
sleep 45
UP_LOG=$("$VM" "grep -c 'mwan3 ifup $LINE 成功' /var/log/esurfing/logs/run.log 2>/dev/null || true")
RULES_UP=$("$VM" "ip rule show | grep -c 'lookup $TABLE' || true")
echo "  ifup 日志=$UP_LOG  检查时刻规则数=$RULES_UP (永久失效场景允许再次被摘)"

echo "== 5. 判定 =="
# 本场景的会话是永久失效的，恢复后会立刻再次失败并重新摘线，
# 所以不要求检查时刻的规则数 > 0，只要求 ifup 确实被尝试过。
if [ "$DOWN_LOG" -ge 1 ] && [ "$RULES_DOWN" -eq 0 ] && [ "$HAS_IP" -ge 1 ] && [ "$UP_LOG" -ge 1 ]; then
    echo "SMOKE_PASS"
    exit 0
fi
echo "SMOKE_FAIL"
exit 1
