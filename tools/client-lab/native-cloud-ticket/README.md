# 登录器与原客户端加密适配

本目录提供原套接字上的认证票据与加密适配源码。玩家入口是独立的 **功夫小子启动器.exe**；适配 DLL 本身不提供登录窗口，也不代理游戏流量。

## 使用前提

- Windows x64、.NET Framework 4.8。
- 合法且已适配的 32 位客户端副本；当前安装器要求入口文件名为 `gfxz-lab.exe`，仅改名不代表已适配。
- 匹配的加密适配 DLL、注入器和初始化器。源码仓库不附原游戏及工具二进制成品。
- 已配置的自建账号服务、正确的游戏端点与可信 TLS 证书。
- 客户端版本和组件组合必须匹配，见[兼容说明](../../../docs/CLIENT_COMPATIBILITY.md)。

路径由安装配置指定。客户端运行环境不构成服务端的信任凭证；第三方也可按 [SERVICE](../../../docs/SERVICE.md) 直接实现接入。

## 先构建三个接入工具

仓库提供自有工具源码，不分发原游戏二进制。需要 VS2022 Community、MSVC 14.36 x86 工具集与 Windows SDK。下列命令在仓库根目录执行，输出目录必须尚不存在：

```powershell
powershell -NoProfile -File tools/client-lab/native-cloud-ticket/Build.ps1 -OutputDirectory build/cloud-adapter
powershell -NoProfile -File tools/client-lab/Build-KkClientInitializer.ps1 -OutputDirectory build/client-initializer
```

| 安装依赖 | 源码 | 构建产物 |
|---|---|---|
| 加密票据适配 | [adapter.cpp](adapter.cpp) | `build/cloud-adapter/kk_native_cloud_ticket.dll` |
| 手动初始化器 | [kk1_official_flow_probe.c](../kk1_official_flow_probe.c) | `build/client-initializer/kk1-official-flow.dll` |
| DLL 加载工具 | [kk_inject.c](../../../client-adapter/src/kk_inject.c) | `build/client-initializer/kk_inject.exe` |

`Build-KkClientInitializer.ps1` 固定开启 `KK_OFFICIAL_FLOW_MANUAL_START` 和 `KK_OFFICIAL_FLOW_PORTABLE`，以 i686 编译，并通过 `.def` 导出不带修饰的 `KkOfficialFlowBegin`。不要自行省略这些选项或用旧自动启动探针替代。

初始化器复用现有已适配客户端的上层初始化调用，在客户端 UI 线程处理启动请求；不是 GPK 适配器，也不能把未适配的原版安装包直接变成可用客户端。地址/入口检查只是保护条件，不是任意 `1.13.0.594` 文件均兼容的证明。

构建内置自有 i686 模型测试：检查手动导出、拒绝无关进程和 DLL 相邻日志路径，不加载原客户端或连接任何服务。该测试会生成自己的模型日志，**不要把构建目录的日志复制到客户端**。

## 配置初始化器日志

`initializer.log` 不是缺失的下载文件。新初始化器运行时在自身 DLL 所在目录创建 `kk1-official-flow.log`，角色表就绪后另生成 `kk1-roleprop-ready.txt`。安装目录必须允许当前用户写入这两个文件；加载时如果日志不可写，DLL 直接拒绝加载，不再继续初始化后让登录器等待超时。

安装前的配置推荐：

```json
{
  "initializer_manual_start": true,
  "initializer_log": "auto"
}
```

这是 [launcher.example.json](launcher.example.json) 的字段片段，不是完整配置。将 `adapter_dll / injector_path / initializer_dll` 分别指向上表实际构建产物，再填写认证端点和公开证书配置。

安装器将 `auto` 保存成相对配置目录的 `native-tools/kk1-official-flow.log`，启动器在加载配置时解析成实际位置，并检查本次进程的新日志；整个游戏目录搬迁后仍有效。安装器不会创建虚假的就绪日志。仍使用旧初始化器时，显式填写其真实日志路径，不可套用 `auto`。直接绕过安装器读取源配置的开发工具不解释这个安装期占位值。

## C# EXE 登录入口（2026-09-23）

从仓库根目录构建、测试并安装：

```powershell
powershell -NoProfile -File tools/client-lab/Build-KkLauncher.ps1 -OutputDirectory build/launcher
powershell -NoProfile -File tools/client-lab/Test-KkLauncherExe.ps1 -LauncherExecutable build/launcher/功夫小子启动器.exe
powershell -NoProfile -File tools/client-lab/Test-KkLauncherInstall.ps1 -LauncherExecutable build/launcher/功夫小子启动器.exe
powershell -NoProfile -File tools/client-lab/Install-KkLauncher.ps1 -ClientRoot C:\MyGame -NativeCloudSettings C:\MyConfig\launcher.json -LauncherExecutable build/launcher/功夫小子启动器.exe -DesktopShortcut
```

安装前复制并填写 [launcher.example.json](launcher.example.json)。安装器复制 EXE、EXE.config、自有工具及 `launcher/` 配置目录；可建立直接指向 EXE 的桌面快捷方式，不修改游戏二进制。EXE 和配套目录须一起保留。

安装器先复用提供的启动器 EXE 中的配置验证器，拒绝错误端点、端口及缺失工具，再写入安装目录；配置文件使用同目录临时文件原子替换。安装前应关闭正在使用该目录的游戏和启动器，这不等于整个安装目录具备断电事务回滚能力。

`initializer_log` 必须对应初始化器实际输出位置；上节的新构建使用 `auto` 由安装器填写。`initializer_manual_start=true` 要求初始化器导出 `KkOfficialFlowBegin`。不能任意换用自动启动版本。初始化器仍是版本适配组件，不是所有客户端通用的启动模块。

玩家流程：

1. 打开启动器，必要时点右上角“服务器设置”。
2. 使用该服务器的账号登录；新账号按服务器政策输入邀请码注册。
3. 选择区服，点击进入游戏。
4. 等待实际大厅就绪；完成后隐藏残留旧登录窗口。关闭自制登录窗口不会关闭游戏。

错误账号或密码会弹出独立“登录失败”窗口，并保留页面红色提示；不继续启动游戏，也不叠加重复弹窗。

## 地址与证书

- IP/端口是连接目标；证书名称是 TLS 证书 SAN 对应的域名或 IP，**不是文件路径**。
- `certificate_path` 留空时使用系统信任链；指定时读取管理员提供的公开证书，并继续校验名称、有效期及相应信任条件。
- 不会自动下载并信任陌生服务器的证书。切换不同自签证书的服务器，需要更新安装配置。
- 只保存服务器偏好，不保存密码、邀请码、会话或加密密钥；设置默认位于 `%LOCALAPPDATA%\KungFuKid\Launcher`。
- 已登录或正在登录时不能切换服务器；修改不会改变已运行游戏的连接。
- 服务器实际下发端点仍需通过交接检查，不会因为用户填了 IP 就跳过校验。

## 交接与失败处理

交接链依次检查覆盖 mask29、selector4 与资源就绪、授权安装、初始化器加载、覆盖 mask31、当前进程角色表就绪、selector5、client_ready，最终以服务端大厅就绪和 selector6 稳定确认成功。固定延时不是成功判据。

授权仅在内存交接，不写命令行或配置。初始化失败不伪造成功，不自动重复启动第二个游戏进程。首次连接截止和完整会话寿命分别处理。

适配器不更改战斗、位置或碰撞。游戏侧进程检查用于避免交接到错误进程，不是服务端身份认证依据。

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
- `KkNativeCloudGetCoverageDiagnostic(CoverageDiagnostic*)`：可选20字节只读诊断，size=20/version=1/reason/module/location。reason与检查分支由`socket_transport.hpp`定义；location按该分支为模块RVA或Win32错误码。无账号、票据、密钥或包正文，不改变V2准入条件。
- `KkNativeCloudGetIoDiagnostic(Snapshot*)`：可选136字节只读计数，size=136/version=1、8个API计数、12个已解密帧计数及12个已交付帧计数。固定编号顺序见`stream_observation.hpp`；只统计游戏通道，窥读不计为交付。交付到recv调用方不等于原生业务处理完成。计数为32位累积值，跨计数读取不是原子时间快照；不导出正文、账号、票据或可变标签。
- `KkNativeCloudClearTicket(void*)` / `KkNativeCloudRemove(void*)`：退出/重登时撤销凭据。Remove 保留拒绝层到进程退出，不恢复明文发送。

SDK首包前导为`KKS1`+独立SDK票据32字节。它不把密码放入旧SDK包；服务端验证票据后才处理原
SDK1001/1011和返回实际账号/UID。send适配处理短写及WSAEWOULDBLOCK，只将原始字节数返回SDK；
closesocket清理对应socket状态，避免句柄复用漏发前导。只允许发送到配置的SDK端点。

`NativeCloudHandoff.cs`使用校验服务器名称/证书链的TLS客户端，经目标PID、路径、创建时间核对后，
在进程内存投递配置；不把票据写入参数或文件。远程调用超时时不释放仍可能被使用的参数内存。
复用已存在的初始化DLL，不新增强写人物/战斗状态。该旧初始化器仍含原先的12秒延后初始化兼容逻辑；
新放行条件是读到角色表、钩子就绪及服务器实际收到2250，而不是等固定时间就宣称成功。

正常`910510`路径已定位；另一`822CE0`是既有网络注册表1190的处理器。当前服务不发送1190，
因此不主动进入这个分支；未宣称支持所有旧服务端重试控制，也未放宽缺票据拒绝。


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
`MSG_PEEK`按明文流合并当前可读的多个加密记录，不能把一条加密记录误作TCP分段边界；后续记录半包时立即返回已有明文，不额外阻塞。重复窥读不消费明文；窥读请求上限256 KiB，缓存最多多保留一条32 KiB记录，超限显式拒绝。普通小块recv不反复移动整个缓存。新增真实回环测试覆盖两条记录之间的半个头、缓存增长、重复窥读、分块读取及EOF；此修复不改变线上加密格式。
动态GetProcAddress在已适配模块内也经过同一表；`select`、WSAAsyncSelect/WSAEventSelect的缓存可读通知已接线，事件对象分支已有模型验证。
后续新加载模块、安装前缓存的函数指针与具体异步调用覆盖，需要按目标客户端单独验证。
安装先以有界PE读取生成完整计划，按原导入名称/序号核对地址，再串行提交。失败只回滚仍属于本模块的槽，外部改写不覆盖。模块加载通知只使代次失效并唤醒工作线程，不在加载器回调中扫描/改IAT；不支持或回滚失败进入Failed。缺少原导入名称表的模块拒绝准入，不做猜测。
开发状态不等于独立密码协议审计；此设计没有另加应用层前向保密握手，依靠TLS下发的短期随机授权密钥。


## 构建适配 DLL 与相关验证

在本目录执行 `Build.ps1 -OutputDirectory <全新目录>`。当前脚本使用 VS2022/MSVC14.36 x86，警告视为错误；请先检查工具链路径。输出 DLL 与独立合同测试 EXE，测试使用自行编写的模型，不加载原 SDK。

从仓库根目录可运行：

```powershell
powershell -NoProfile -File tools/client-lab/Test-KkLocalAccountWindow.ps1
powershell -NoProfile -File tools/client-lab/Test-NativeCloudHandoff.ps1 -BuildDirectory <适配DLL构建目录>
```

后者覆盖跨进程配置、状态与撤销模型。模型验证不能代替目标游戏版本的登录、大厅和对战实测。可执行包目前未做代码签名。

## 已知边界

- 不支持任意游戏或 SDK 版本；相同游戏版本号也可能携带不兼容组件。
- 同步多缓冲区收发已有实现，overlapped、完成例程和 IOCP 仍明确拒绝。
- 安装前缓存的函数指针和未经过受控入口的模块加载，不能宣称自动全覆盖。
- `accept/listen` 映射为明确拒绝，不提供原 P2P 入站监听；当前网络路径使用服务端中继。
- 加密记录层是自建设计，尚未经过独立密码协议审计；不是完整反作弊方案。
