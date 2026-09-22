# 原客户端票据适配 DLL（候选）

不创建网络连接或监听器、不代理游戏TCP/UDP、不更改战斗/角色/碰撞。
SDK现有socket首次发送前增加36字节认证前导；SDK、游戏TCP和UDP业务格式在加密记录内部保留。
目前由同一DLL在原套接字上加解密，不新增连接桥或监听器。
它不是启动器，也不是绕过密码的免认证工具；服务器仍验证票据和原账号身份。
开发版登录器已接入，未注入原客户端/VM，不代表完整云端登录已验证。

## 来源与范围

- 主程序`910510`在`9106ED`调用`913880(buffer,32)`，将返回字节写入登录包+16，
  buffer位于登录包+17。`913880`经SDLogin对象vtable+0x24调用方法。
- 当前SDLogin的该槽为RVA`0x2CE0`、stdcall(self,buffer,length)，原实现最多写24字节；
  本地适配在明确length32时填入32字节票据并返回32，属于自建接口政策，不是原服算法。
- SDP2P公开vtable槽6（RVA`0x6AD0`，thiscall）同步转入`10003650`→`100035F0`。
  参数的+12为21字节账号，+33为129字节用户数据。适配保留前12字节，复制参数后替换
  账号及用户数据为`KKN1:`+64位小写十六进制UDP票据+60个零字节；不改变原参数内存。
- 服务端对应`NativeAdmission.resolve_udp`。票据负责身份，外层`kk-aesgcm-v1`负责加密、防篡改与重放拒绝；两者不可互相替代。

只接受本批核对的SDLogin/SDP2P文件身份；Install对vtable和当前客户端目录下已加载模块的Winsock导入指针做原子替换，不修改机器指令。
DLL加载时无自动动作。安装前固定模块身份并pin自身，避免卸载后留悬空函数指针。
网络适配未配置、过期或撤销时拒绝发送，不回退明文；业务凭据接口在未配置时仍保留原接口返回。
一次进程只接受一次有效配置，重登使用新进程、新授权，不重置同一密钥的序号。
Remove撤销凭据、停止并等待发送/扫描线程退出，保留已pin的挂钩拒绝层到进程退出，不恢复明文导入。

## 导出接口

所有导出均为32位`WINAPI`，每次一个指针参数，返回Win32错误码。

- `KkNativeCloudSetTicket(TicketConfig*)`：当前version3，pack(1)共224字节；
  size/version/PID/有效毫秒数各DWORD，ASCII账号21字节、TCP原始32字节、UDP十六进制65字节、
  SDK原始票据32字节、网络序IPv4四字节、SDK端口u16、transport ID16字节、transport key32字节、game/UDP端口各u16。
  使用认证返回的`session_expires_at`计算剩余时间，不能把首次连接120秒截止当作整个会话寿命。
- `KkNativeCloudInstall(void*)`：先安装SDLogin三项和加密网络层（状态mask29），再在SDP模块自然加载后安装其登录槽（mask31）。
  启动器先要求mask29再触发初始化，要求mask31和角色表Ready后才放行人物包。
- `KkNativeCloudGetStatus(AdapterStatus*)`：仅返回安装位、是否配置和活动调用数，不输出票据。
- `KkNativeCloudGetStatusV2(AdapterStatusV2*)`：44字节、11个DWORD：size/version/phase/installed/configured/active_calls/coverage_generation/verified_generation/unsupported/last_error/rollback_failed。调用前填size=44、version=2。phase为Cold=0、Preparing=1、Ready=2、Failed=3、Retired=4。启动器只以V2的Ready、安装位、相等覆盖代次及零未支持/回滚错误共同放行；V1仅诊断。
- `KkNativeCloudClearTicket(void*)` / `KkNativeCloudRemove(void*)`：退出/重登时撤销和恢复。

SDK首包前导为`KKS1`+独立SDK票据32字节。它不把密码放入旧SDK包；服务端验证票据后才处理原
SDK1001/1011和返回实际账号/UID。send适配处理短写及WSAEWOULDBLOCK，只将原始字节数返回SDK；
closesocket清理对应socket状态，避免句柄复用漏发前导。只允许发送到配置的SDK端点。

`NativeCloudHandoff.cs`使用校验服务器名称/证书链的TLS客户端，经目标PID、路径、创建时间核对后，
在进程内存投递配置；不把票据写入参数或文件。远程调用超时时不释放仍可能被使用的参数内存。
复用已存在的初始化DLL，不新增强写人物/战斗状态。该旧初始化器仍含原先的12秒延后初始化兼容逻辑；
新放行条件是读到角色表、钩子就绪及服务器实际收到2250，而不是等固定时间就宣称成功。

正常`910510`路径已定位；另一`822CE0`是既有网络注册表1190的处理器。当前服务不发送1190，
因此不主动进入这个分支；未宣称支持所有旧服务端重试控制，也未放宽缺票据拒绝。

## 开发入口

先按`server/configs/native.example.json`准备独立开发库和TLS证书，再用统一`--mode native`启动。
复制并填写[launcher.example.json](launcher.example.json)，只能填写工具路径和端点，不能放密码或票据。
证书必须受测试系统信任且名称匹配；不要禁用证书校验。运行：

```powershell
powershell -NoProfile -STA -File tools/client-lab/Run-KkLocalAccountWindow.ps1 -NativeCloudSettings <配置路径> -ClientRoot <已准备的C:\KK-Lab客户端副本>
```

此入口使用下述实际防火墙检查替代“无默认路由”推断，SDK地址仍限定回环，主EXE按已确认版本核对。
会备份副本的config.xml，再替换登录端点并禁用该文件里的旧更新/注册/支付URL；不改原安装树。
这不是可直接用于公网的发布配置。加密及出站限制已有实现和模型测试，但原客户端套接字调用覆盖、异步通知方式和VM防火墙实效尚未验收，公网保护未解除。

## 加密记录 v1（自建设计，不是原服务端算法）

TLS认证后每次授权随机签发256位master和128位公开transport ID，只驻留内存。
HMAC-SHA256按固定域`KK-native-record-v1\0`、通道、方向、transport ID及连接ID分离AES-256-GCM密钥。
TCP每连接随机128位ID；SDK、游戏和上下行不共用key。UDP每次授权只有一个序列空间，支持1024包乱序窗口。

记录头52字节：`KKE1 / channel:u8 / direction:u8 / reserved:u16 / transportID:16 / connectionID:16 / seq:u64 / size:u32`，整数均网络序。
头整体参与AAD；nonce为4零字节加序号u64，随后密文和16字节tag。单记录1–32768字节、每方向少于2^32记录。
TCP严格顺序，UDP认证后才更新重放窗口；TCP连接ID用过即保留到授权撤销，最多32个。
SDK的`KKS1`前导也必须位于密文内。没有自动格式检测或明文降级。
依赖：服务端`server/requirements-native.txt`；DLL使用Windows CNG，不手写密码算法。

发送数据复制到每socket上限256 KiB队列后才返回接收的明文字节数。后台线程用同一密文处理短写，不再要求调用方原样重发；队列满的非阻塞调用不消费数据，阻塞调用按SO_SNDTIMEO等待容量。就绪检查不等待持有网络I/O的锁。
普通关闭先排空队列（默认最多2秒，显式linger沿用其期限）；超时显式失败，abortive close及撤销取消积压。先停线程再释放socket，禁止旧队列跟随句柄复用。
UDP只发往配置服务器端点，禁止原直连打洞；原1008中继仍由服务校验同房和租约后转发。
同步WSASend/WSARecv及UDP同类接口支持最多64个缓冲区；仍拒绝overlapped/完成例程/IOCP，不假装这些接口已兼容。
动态GetProcAddress在已适配模块内也经过同一表；`select`、WSAAsyncSelect/WSAEventSelect的缓存可读通知已接线，事件对象分支已有模型验证。
后续新加载模块、安装前缓存的函数指针、原SDK的具体异步调用覆盖仍必须在VM确认。
安装先以有界PE读取生成完整计划，按原导入名称/序号核对地址，再串行提交。失败只回滚仍属于本模块的槽，外部改写不覆盖。模块加载通知只使代次失效并唤醒工作线程，不在加载器回调中扫描/改IAT；不支持或回滚失败进入Failed。缺少原导入名称表的模块拒绝准入，不做猜测。
开发状态不等于独立密码协议审计；此设计没有另加应用层前向保密握手，依靠TLS下发的短期随机授权密钥。

## VM出站白名单

[Set-KkNativeEgress.ps1](../Set-KkNativeEgress.ps1)提供`Plan / Apply / Check / Restore`。
Apply/Restore只在具有`C:\KK-Lab`的VMware客体、管理员权限且无原客户端/SDK进程时执行。
Apply使用机器级互斥和原子v2日志，先保存原profile和规则签名，建立全出站阻断保护规则，再设置默认拒绝、禁用旧允许规则并建立白名单。校验后最后移除保护规则；失败后保留/重建保护，日志不完成则Check不能放行。
仅放行指定IP：启动器PowerShell到认证TCP端口；指定gfxz-lab.exe到SDK/game TCP和relay UDP端口。
不放行旧SDK子进程、DNS、其他IPv4/IPv6或任意网页；不依靠旧域名是否登记。
这会限制整个专用实验VM的出站（包括更新/DHCP/DNS），不得用于日常桌面或宿主。入站规则不改，VMware guest operations不依赖新增公网出站。
规则持久保留，启动器崩溃不会自动撤销；恢复前必须关闭游戏，使用同一机器、同一计划的v2日志显式Restore。同计划Apply可续接中断；Restore幂等且检测外部改动。旧v1收据不会静默升级。OS规则与磁盘日志不是跨系统原子事务，提交边界崩溃由过渡阶段和启动拒绝门恢复。
Check核对ActiveStore实际生效profile、允许规则总数、程序、IP、协议及端口，GPO或其他允许项不一致就停止。

认证连接直接用配置的IPv4，TLS仍校验auth_host证书名称、链和吊销状态，不禁用校验。
部署前须在VM准备可信证书链/离线可用吊销状态；不为证书下载临时放开任意80/443。
首次配置先看Plan；本源码批次没有执行Apply，也没有修改任何实际防火墙。

## UDP限流修正

未知来源和已绑定端点独立限流：未知IP 64/s、突发128；未知全局256/s、突发512；512项LRU、空闲60秒淘汰；已绑定端点400/s、突发800。AEAD验证成功后才进入每授权200/s业务额度。地址不是身份，伪造同一端点与链路饱和不在此保证内。错误按有限类别每10秒汇总一次，不逐包记录密钥或正文。

## 本批验证边界

安全修正后的干净导出副本：690项服务测试、17项兼容工具测试、8项资源工具测试通过（715项Python测试）。i686合同、拥有的测试DLL迟加载/并发撤销、CNG/Python互通、V2跨进程交接、登录窗口及防火墙模型全部通过。未运行原客户端/VM，未应用真实防火墙，未部署或推送。

首批34项相关Python测试通过；CNG/Python固定向量一致，真实回环套接字完成100000字节加密往返、17字节读取、MSG_PEEK、缓存可读事件、强制短写/WouldBlock和UDP加解密。
防火墙模型验证额外允许规则、程序/地址/端口放宽、profile禁用等10种拒绝分支；真实Windows规则尚未Apply。
DLL严格i686构建、跨进程224字节交接及WinForms回归通过；未加载原游戏/SDK，未开放网络，未部署云服务器。
因此下一道门仍是离线VM的真实IAT/异步套接字与规则验证，不能将模型测试当作公网发布验收。

实现参考：[Microsoft CNG认证模式](https://learn.microsoft.com/en-us/windows/win32/api/bcrypt/ns-bcrypt-bcrypt_authenticated_cipher_mode_info)、[AEAD nonce与InvalidTag约束](https://cryptography.io/en/latest/hazmat/primitives/aead/)。

## 构建

使用`Build.ps1 -OutputDirectory <全新目录>`；固定VS2022/MSVC14.36 x86，警告视为错误。
输出DLL与独立合同测试EXE，测试不加载原SDK，不启动原客户端或VM。
`Test-NativeCloudHandoff.ps1 -BuildDirectory <构建目录>`另测试真实跨进程配置/状态/清理：
只启动自建i686模型进程，不是原游戏。旧窗口回归为`Test-KkLocalAccountWindow.ps1`。
`Test-KkNativeEgressTransaction.ps1`只加载内存后端，覆盖17个Apply、11个Restore中断点、提交边界恢复及外部冲突。Windows后端实际效果仍未验证。
证据定位见本地EXP-20260922-0930-native-cloud-ticket-carriers；原载荷不随工具发布。
