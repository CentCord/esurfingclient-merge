# 校园网多拨与会话术语

本词表约定客户端、校园网认证服务与多线路路由之间的领域语言。同一词在诊断、配置和界面中应保持同一含义。

## 身份与拓扑

**账号（Account）**：一组向校园网服务端证明身份的凭据；每个账号拥有独立的认证生命周期。
_避免_：把账号称为线路或客户端进程。

**客户端实例（Client instance）**：负责一个账号的完整认证、保活和失效处理的一份运行实体。
_避免_：把多个实例称为一个共享会话。

**物理 WAN（Physical WAN）**：连接校园网入口的真实物理端口；一根物理链路可以承载多个虚拟接口。

**虚拟接口（Virtual interface / iface）**：从物理链路派生、拥有独立链路身份和地址的网络接口。
_避免_：把 iface 当作账号 ID 或服务端会话。

**线路（Line）**：账号实例经其虚拟接口、地址与路由形成的一条独立出站路径。
_避免_：用“网卡在线”代指线路已认证。

**路由标记（Routing mark / fwmark）**：为出站流量选择线路的整数标签；它表示路由选择，不表示服务端会话状态。
_避免_：把 mark 称为 ticket 或通道 ID。

**通道（Channel）**：客户端向服务端呈现的平台身份与协议族，例如 Android、Linux 或 macOS。
_避免_：用通道表示虚拟接口。

**通道 ID（Channel ID）**：本项目配置中通道的数字标识；同一通道也可以由名称表示。
_避免_：与服务端下发的 Algo-ID 混用。

**客户端 ID（Client-ID）**：协议请求中识别客户端身份的一段标识；同一账号会话的相关请求应带一致的值。
_避免_：与账号名或通道 ID 混用。

## 协议与观察值

**门户（Captive portal）**：未认证流量可能被重定向到的登录页面或网关入口。

**算法 ID（Algo-ID）**：服务端选择报文密码算法或模块的标识；它不等于通道 ID。

**票据（Ticket）**：认证流程中取得、随后由认证和保活请求引用的短期凭据。
_避免_：将 ticket 等同密码或长期会话状态。

**会话（Session）**：服务端接受账号认证后维持的状态；“路径可达”不蕴含会话有效。

**认证（Authentication / auth）**：用账号凭据和票据建立服务端会话的协议动作。

**保活／心跳（Keepalive / heartbeat / keep）**：携带当前会话信息向服务端续期或确认状态的协议动作。两词在本项目指同一类请求。
_避免_：将网络路径探测称为心跳。

**登出（Termination / term）**：请求服务端结束当前会话的协议动作。

**路径探测（Path probe）**：观察某条线路的 HTTP 可达、门户重定向或路径错误；它不是服务端会话确认。
_避免_：将 TCP 握手成功称为“认证在线”。

**外网可达（Internet reachable）**：在所测线路上取得预期的外网 HTTP 响应；只描述路径现象。

**门户拦截（Portal redirect）**：探测请求被重定向到门户的现象；与本账号 ticket 失效是不同命题。

**会话失效（Session invalid）**：本账号的服务端会话不能继续使用的状态；须结合服务端回复与探测现象判定。

## 恢复与隔离

**退避（Backoff）**：认证失败后延长或安排下一次尝试的等待策略。

**限流隔离（Rate-limit isolation）**：服务端明确拒绝高频登录后，只暂停受影响账号尝试认证的状态。
_避免_：把线路超时猜成服务端限流。

**致命暂停（Fatal pause）**：凭据等需要人工处理的错误使当前账号停止自动重试的状态。

**摘线（Line withdrawal）**：使失效线路退出新连接的转发候选集合；既有连接可能继续保持旧路径。

## 术语核对来源

账号、通道和 mark 见 `esurfingclient/app/include/core/States.h:152-160`、`esurfingclient/app/src/config/ConfigAccounts.c:24-40`；会话协议阶段见 `esurfingclient/app/include/core/States.h:96-102`、`esurfingclient/app/src/dialer/DialerNet.c:226-289`；路径探测与会话确认分离见 `esurfingclient/app/src/dialer/DialerClient.c:196-222`；隔离和摘线见 `esurfingclient/app/src/dialer/LineControl.c:102-153`。多实例的领域边界由 `DESIGN-PHILOSOPHY.md:7-34` 约定。
