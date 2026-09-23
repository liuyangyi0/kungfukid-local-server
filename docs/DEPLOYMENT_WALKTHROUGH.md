# 从源码到原登录窗口：操作手册

本文把步骤写到目录、命令、进程与判据。原理和字段说明见 [兼容实现详解](CLIENT_COMPATIBILITY.md)。**不是拿一份任意客户端就能直接套用的安装说明。**

## 0. 先判断你的起点

| 你现在拥有的环境 | 能先做什么 | 不能跳过什么 |
|---|---|---|
| 只有本GitHub仓库 | 运行全部合成测试、编译适配器 | 原客户端、SDK、资源不在仓库内 |
| 有原安装包，但客户端无法启动 | 检查本地副本、日志及版本 | GPK/更新/缺失依赖不是HTTP服务能自动解决的 |
| 副本能打开原登录框 | 进行下面的本地认证接线 | 核对版本、路径、端口、对象契约 |
| 已能进大厅 | 测房间、装备、错误和生命周期 | 不能直接宣布所有战斗/结算协议完成 |

目前没有公开原始客户端、离线GPK适配二进制、原始资源或VM镜像。下面步骤从“授权副本已能到原登录框”开始。如果不能满足这一前提，停在相应问题上，不伪造`kk-roleprop-ready.txt`或把所有服务器请求回成功。

**先核对 SDK，而非只核对游戏版本。** `Version.dat=1.13.0.594` 不保证登录组件兼容。原窗口适配的参考组合使用 `SdoBaseClient.dll 2.2.2.0`、导出序号16 RVA `0xc1b0`；2.3.3.0 的 `0xc8c0` 目前不受支持。若 `inspect_client.py` 返回 `expected_export16_rva_match=false`，不要继续注入或修改常量绕过检查。组件身份、获取渠道边界及贡献要求见[SDK构建兼容性](CLIENT_COMPATIBILITY.md#sdk-build-compatibility)。

## 1. 环境与依赖

- 客户端和服务放在同一隔离Windows VM。本文的127.0.0.1指VM自身，不是宿主。
- Python 3.12、pip、`requirements.txt`中的cryptography。核心SQLite随Python提供，不需要单独下载数据库服务器。
- 编译机器需要Visual Studio C++工具、Windows SDK和x86开发环境；可在宿主编译后只把自己构建的工具复制到VM。
- 客户端/SDK为32位，注入工具和DLL也用x86。不要因宿主Windows是64位而编译x64 DLL。
- 保持原文件与实验副本分开；不要关闭系统防护或加载废弃驱动来满足教程。

联网构建机准备离线Python依赖：

```powershell
python -m pip download -r requirements.txt --only-binary=:all: --dest .\wheelhouse
```

此命令按当前Python平台选择轮子。构建机与VM的Python版本/架构需一致；否则请在相同目标平台准备依赖。VM中安装：

```powershell
python -m pip install --no-index --find-links .\wheelhouse -r requirements.txt
python -m unittest discover -s server/tests -p "test_*.py" -v
python -m unittest discover -s client-adapter/tests -p "test_*.py" -v
python -m unittest discover -s tools/resource-recovery -p "test_*.py" -v
```

离线轮子、Python安装程序和编译器不随源码仓库上传。

地图加载额外依赖本仓库的`tools/resource-recovery/spf2_index.py`和`spf2_extract.py`，不是pip包。复制服务源码时保留这个相对目录；只复制`server/`会导致`ModuleNotFoundError`。`server/tests/test_client_config_package.py`使用现场生成的合成SPF2、不mock解析器，覆盖实际索引→解码→XML→地图准入路径。

## 2. 推荐先复用原实验目录形状

当前C++白名单和模板写死以下布局。第一次移植若采用它，可减少路径变量；这不代替版本匹配。

```text
C:\KK-Lab\
  kk_inject.exe                         # 自行编译
  Start-SdoOriginalWindowGuest.ps1       # 仓库reference-scripts副本
  sdo-native-client-20260913-1931\       # 授权客户端副本，不在GitHub
    gfxz-lab.exe
    Data\config.xml
    sdo\sdologin\sdologin.exe
    sdo\sdologin\SdoBaseClient.dll
    sdo\sdologin\duilib.dll
    ...客户端自身资源...
  sdo-original-window-20260913-1931\
    source\                             # GitHub源码根目录
    request-adapter.dll
    input-provider-a07.dll
    http-path-a07.dll
    observer.dll
    active-public-key.bin               # 本轮服务生成的公钥副本
    deployment.json                     # 启动器读取的本轮信息
    accounts.sqlite3                    # 本地新建，不提交
    runs\<本轮唯一目录>\
```

`gfxz-lab.exe`是原实验的副本文件名。不要把改名等同于完成离线适配。

若使用另一目录，需要同步修改：C++映像路径白名单、公钥/日志路径、guest脚本中的root/client、host脚本的VMX路径与凭据引用、服务`--client-root`。**保留精确目标验证**，不要简单删除路径判断。

## 3. 先做只读检查

```powershell
python client-adapter/tools/inspect_client.py --client-root C:\KK-Lab\sdo-native-client-20260913-1931
```

工具只检查指定文件，不搜索所有磁盘、不读取数据库或密码。它报告PE架构、模块基址、文件摘要与所需导出是否存在。SHA用于记录你使用了什么，不代表授权，也不自动证明版本兼容。

当前源码针对`SdoBaseClient.dll 2.2.2.0`及对应duilib组合；还检查导出序号16的RVA、公钥store虚表和密码控件虚表等。任一检查不满足时，应恢复该版本契约，不能通过修改常数让检查强行“过绿”。

## 4. 编译并复制自己的工具

在x86 Native Tools Command Prompt，进入仓库根目录：

```bat
client-adapter\build.cmd
```

预期输出位于`build\adapter`：

| 输出 | 放置位置 | 作用对象 |
|---|---|---|
| kk_inject.exe | C:\KK-Lab\kk_inject.exe | 显式PID指定的授权进程 |
| request-adapter.dll | 服务根目录 | SDK进程 |
| input-provider-a07.dll | 服务根目录 | SDK进程 |
| http-path-a07.dll | 服务根目录 | SDK进程，只记录路径 |
| observer.dll | 服务根目录 | 主游戏进程 |

加载器源码支持按进程名查找，但本手册只使用`--pid`；按同名进程的“第一个结果”加载可能操作错对象。加载器本身不验证客户端版本，也不代替父子进程/创建时间检查。加载超时不要反复重试；确认现场后重新启动自己的副本。

模块按进程寿命存在，没有完整热卸载协议。不要在仍被函数指针引用时卸载DLL。

## 5. 建立自己的本地账号

账号数据库不提供默认密码。可以先使用附带的管理工具在VM本地创建自己的账号：

```powershell
python client-adapter/tools/create_local_account.py --database C:\KK-Lab\sdo-original-window-20260913-1931\accounts.sqlite3 --account MyLocalUser --nickname LocalPlayer
```

它要求交互终端、隐藏输入并重复确认口令，调用同一`AuthManager.register`，不接受`--password`参数或管道口令。工具采用更保守的12–30字节可见ASCII、不含空格子集；服务的原SDK解码还接受ASCII空格。本地通用API能接受更宽范围不代表旧SDK能输入它们。

此命令是本地管理员入口，不是客户端网络注册界面。账号创建失败不会覆盖已有账号或重置密码。迁移真实数据库需使用SQLite backup并单独保护，禁止直接上传GitHub。

## 6. 启动本轮服务与发布公钥

在VM PowerShell运行；变量路径按你的实际环境核对：

```powershell
$Root = 'C:\KK-Lab\sdo-original-window-20260913-1931'
$Client = 'C:\KK-Lab\sdo-native-client-20260913-1931'
$Source = Join-Path $Root 'source'
$Python = (Get-Command python.exe).Source
$Database = Join-Path $Root 'accounts.sqlite3'
$Runtime = Join-Path $Root ('runs\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path (Split-Path $Runtime -Parent) -Force | Out-Null

# 不连接端口测试：实际TCP连接可能被服务当成玩家。
$occupied = @(Get-NetTCPConnection -State Listen -LocalPort 17999,18000,18001,18082 -ErrorAction SilentlyContinue)
if ($occupied.Count) { throw '端口被占用，请核对旧服务，不要自动杀进程' }

$p = Start-Process -FilePath $Python -WorkingDirectory $Source -WindowStyle Hidden -PassThru `
  -ArgumentList @('-m','server.kk_local.sdo_service','--client-root',$Client,'--runtime',$Runtime,'--database',$Database,'--query-probe')
$deadline = (Get-Date).AddSeconds(30)
while (-not (Test-Path -LiteralPath "$Runtime\ready.json")) {
  $p.Refresh()
  if ($p.HasExited) { throw '服务退出；回查启动路径、资源配置与依赖' }
  if ((Get-Date) -ge $deadline) { throw '服务就绪超时；不要启动客户端' }
  Start-Sleep -Milliseconds 200
}
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 17999,18000,18001,18082)
if ($listeners.Count -ne 4 -or @($listeners | Where-Object OwningProcess -ne $p.Id).Count) {
  throw '监听者不是本轮服务'
}
Copy-Item -LiteralPath "$Runtime\public-key.bin" -Destination "$Root\active-public-key.bin"
@{ status='prepared'; service_pid=$p.Id; client_root=$Client; runtime=$Runtime;
   database=$Database; launch_ready=$true } | ConvertTo-Json | Set-Content -LiteralPath "$Root\deployment.json" -Encoding utf8
```

示例使用无空格的实验目录；若更改为带空格目录，`Start-Process -ArgumentList`的字符串拼接需要额外引用处理，不照抄后假定路径正确。

检查公开密钥：

```powershell
python client-adapter/tools/inspect_client.py --public-key C:\KK-Lab\sdo-original-window-20260913-1931\active-public-key.bin
```

应为258字节：LE u16=1024、128字节BE模数、128字节BE指数3。这个文件不是PEM，不是私钥。服务每次重启生成新密钥；旧客户端内存的公钥不会自动同步，所以必须重新准备客户端进程。

## 7. 游戏登录地址不是SDK HTTP地址

`request-adapter.dll`只重定向SDK认证HTTP到18082；游戏自己连接的登录TCP必须也已配置到本机18000。授权副本的`Data\config.xml`中相关LoginServer配置要保持原编码与结构，只更改已识别的Ip/Port字段。不要全文件替换数字或把地图/房间服务器字段一起改掉。

公开包未提供自动修改XML/SDK磁盘公钥的脚本。原实验已发现：修改SDK磁盘文件可能触发自更新；当前路径通过运行时对象适配，不修改duilib磁盘公钥。

## 8. 在交互桌面启动与加载

把reference-scripts中的guest脚本复制到`C:\KK-Lab\Start-SdoOriginalWindowGuest.ps1`，在**VM已登录用户桌面的终端**运行：

```powershell
& 'C:\KK-Lab\Start-SdoOriginalWindowGuest.ps1' -AuthenticationMode OriginalSdk
```

不要从Windows服务的Session0直接启动游戏。公开宿主模板现在只可打开VM控制台，不读取VM凭据，也不使用`vmrun -gu/-gp`远程启动；避免VM密码出现在子进程参数。请在已登录VM桌面运行上述guest命令。原实验的guest operations和`-interactive`不构成公开推荐入口。

guest启动器会依序：检查服务端口 → 启动游戏 → 查找唯一SDK子进程 → 请求适配 → 输入适配 → 路径观察 → 角色表观察 → 写compatibility-ready。每个阶段都依据本轮PID和日志增量，而非固定长等待后猜成功。

如果你重写启动器，手工加载命令的形状为：

```powershell
# $SdkPid/$GamePid必须来自刚启动进程的精确父子关系与路径检查。
& 'C:\KK-Lab\kk_inject.exe' "$Root\request-adapter.dll" --pid $SdkPid
# 等当前SDK PID的stage=ready后：
& 'C:\KK-Lab\kk_inject.exe' "$Root\input-provider-a07.dll" --pid $SdkPid
# 等user_mode_event_adapter_ready后：
& 'C:\KK-Lab\kk_inject.exe' "$Root\http-path-a07.dll" --pid $SdkPid
& 'C:\KK-Lab\kk_inject.exe' "$Root\observer.dll" --pid $GamePid
```

这四条不是绕过前置检查的捷径。inject工具exit 0只表示LoadLibrary返回非零，不证明DLL安装逻辑成功；必须看模块自身Ready标记。

## 9. 登录及人物下发门禁

在原框输入第5步创建的账号密码。原SDK应调用checkAccountType → getGuid → staticLogin，本地服务才发会话与游戏绑定。HTTP 200不等于认证通过。

角色观察器只读表指针并写`kk-roleprop-ready.txt`。现有`ReadyFile`仅验证修改时间不早于本轮门禁起点、以及十六进制指针范围，不验证文件写入者PID/创建时间/运行ID。guest启动器移走旧文件，认证层另行校验当前游戏进程，两者不等于就绪文件强绑定；这一增强尚未实现。它也不创建角色表，若正常路径未初始化该表，应修复真实初始化顺序，不能手写非零文件。

SDK认证成功但人物读取失败时，先核对角色表和完整档案的时序，不先换密码或强制应用状态。原始SDK路径成功回调与本地API路径是不同入口，不要同时运行两套初始化。

## 10. 最小验收表

| 阶段 | 实际检查 | 失败时停在哪里 |
|---|---|---|
| 服务 | ready.json、准确PID及四个监听端口 | 不启动客户端 |
| SDK适配 | 当前PID的ready及用户态事件标记 | 不提交密码 |
| 密码 | 错误密码失败、正确密码成功、挑战不可重放 | 查接口/密钥/身份，别回假成功 |
| 档案 | 真实角色表已建、时间/指针基础门禁、完整物品记录 | 当前文件无PID绑定，不提前发人物包 |
| 大厅 | 原登录层不遮挡、昵称/档案正常 | 不以页面截图代替协议阶段 |
| 房间 | 创建/设置/换队/返回、失败后连接仍可用 | 逐项核对状态/通知 |
| 重入 | 游戏退出再启动、服务重启配套新公钥 | 不重复注入已初始化客户端 |

保存的分享记录只含时间、PID、阶段、消息ID、长度与结果；不含完整认证URL、Cookie、账号库或私钥。

## 11. 故障定位

| 现象 | 优先核对 |
|---|---|
| 游戏没窗口 | 进程SessionId、工作目录、SDK子进程，不先归因网络协议 |
| image_rejected | 副本路径和大小写检查、是否注入错进程 |
| sdk_identity_rejected | 模块版本、导出序号16/RVA，不删除检查 |
| 风险提示/不能输入 | 控件身份、provider是否初始化、遗留失败锁、是否发生窗口重建 |
| local account authentication failed | last completed HTTP stage、挑战、公钥、原外层IV、账号是否确实注册 |
| 服务器连接失败 | TCP18000/18001监听和原游戏地址，不把HTTP端口当GS |
| 角色读取崩溃 | RoleProperty表和人物包发送顺序 |
| 登录层还挡着大厅 | 确认真实GS/大厅连接，再处理SDK层显示；不要销毁整个游戏 |
| 源码改了但行为没变 | 当前Python进程版本、部署目录和DLL是否仍是旧版 |

## 12. 回滚与遗漏项

只回滚自己本轮修改的副本配置和部署文件；保留原安装目录。停止服务会断开游戏，重启时同步新公钥。不要在多进程共用文件时覆盖正在用的DLL或复制不完整SQLite文件。

尚未交付：通用版本自动识别、完整离线GPK环境、无人值守冷启动安装器、原客户端跨机器认证、所有模式/PVE/奖励/结算。新增源码和文档用于让这些依赖清楚可追踪，不是用教程文字宣称它们已完成。
