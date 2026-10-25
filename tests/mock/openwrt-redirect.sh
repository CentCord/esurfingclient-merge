#!/bin/sh
# Test VM only: redirect this project's fixed public probe/auth addresses to a mock host.
set -eu

action=${1:-}
case "$action" in
    remove)
        nft delete table ip esurf_mock
        exit 0
        ;;
    apply)
        mock_ip=${2:-}
        mock_port=${3:-7001}
        ;;
    *)
        echo "usage: $0 apply MOCK_IPV4 [MOCK_PORT] | remove" >&2
        exit 2
        ;;
esac

case "$mock_ip" in
    *[!0-9.]*|.*|*.|*..*|'') echo "invalid IPv4" >&2; exit 2 ;;
esac
old_ifs=$IFS
IFS=.
set -- $mock_ip
IFS=$old_ifs
if [ "$#" -ne 4 ]; then echo "invalid IPv4" >&2; exit 2; fi
for octet do
    if [ "$octet" -gt 255 ] || [ "$octet" -lt 0 ]; then
        echo "invalid IPv4" >&2
        exit 2
    fi
done
case "$mock_port" in
    *[!0-9]*|'') echo "invalid port" >&2; exit 2 ;;
esac
if [ "$mock_port" -lt 1 ] || [ "$mock_port" -gt 65535 ]; then
    echo "invalid port" >&2
    exit 2
fi

if nft list table ip esurf_mock >/dev/null 2>&1; then
    echo "esurf_mock table already exists; run '$0 remove' before reapplying" >&2
    exit 1
fi

nft -f - <<EOF
add table ip esurf_mock
add chain ip esurf_mock output { type nat hook output priority dstnat; policy accept; }
add rule ip esurf_mock output ip daddr { 183.61.185.93, 125.88.255.53, 202.69.4.32, 220.181.104.183, 14.146.227.141, 121.8.177.212, 125.88.59.131, 61.140.12.23 } tcp dport { 80, 7001 } dnat to $mock_ip:$mock_port
EOF
echo "installed esurf_mock output NAT -> $mock_ip:$mock_port"
