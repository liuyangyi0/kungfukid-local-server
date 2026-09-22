# KungFuKid Local Server

自建的 Python / SQLite 本地兼容服务与 Windows 客户端适配源码。

新增`--mode public`为邀请码注册的非排位公开测试服务模式：目标100账号、单房8人，认证、加密、权限和资源限制由服务端执行，不依赖VM、指定DLL、PID或客户端防火墙。默认示例仍监听回环；本批没有部署或生产安全认证。
第三方标准接入见[服务规范](docs/SERVICE.md)，运维模板见[server/deploy](server/deploy/README.md)。复杂战斗子协议及永久战斗奖励默认关闭，不能声称完整防作弊或原客户端全功能兼容。

这是开发中的实验项目，**不是完整游戏发行包，也不是已恢复全部协议的旧服务器**。不包含原客户端、游戏素材、账号数据库、IDA/PDB、抓包、内存转储、用户口令或认证私钥。源码包含解析既有文件/帧格式所需的固定兼容常量，不是旧服务端私钥。使用者须自行提供有权使用的客户端，并在隔离环境内测试。

## 内容

- `server/kk_local/`：本地账号认证、游戏帧编解码、档案/背包、房间、地图准入、聊天、排行榜等。
- `server/tests/`：临时数据库与合成数据测试，不需要运行原客户端。
- `tools/resource-recovery/`：地图配置运行时实际依赖的SPF2索引/解码源码与合成测试；不包含资源包或解包结果。
- `client-adapter/src/`：自行编写的请求重定向、公钥适配、密码输入兼容、路径观察、角色表观察模块和DLL加载工具。
- `client-adapter/reference-scripts/`：原实验的主客体启动模板，仅供参考，不能直接用于任意电脑。
- `docs/CLIENT_COMPATIBILITY.md`：客户端适配方法、前置条件和迁移步骤。
- `docs/DEPLOYMENT_WALKTHROUGH.md`：从目录布局到依赖、编译、账号、服务、公钥、加载和验收的逐步操作。
- `docs/AI_PROTOCOL_WORKFLOW.md`：可复用的AI协作协议恢复与测试流程。
- `client-adapter/tools/`：只读PE/公钥检查与隐藏密码输入的本地账号创建工具。
- `client-adapter/tests/`：公开工具和认证封装的合成测试。
- `docs/SERVICE.md`：服务运行方式、数据库和已知边界。
- `PUBLIC_SOURCE_MANIFEST.json`：本次发布的源文件清单与内容指纹。
- `server/kk_local/app/`、`handlers/`、`storage/`：统一启动、业务分派和SQLite事务边界。
- `tools/client-lab/native-cloud-ticket/`：原套接字上的自建加密适配候选，含i686源码和合成模型；不带DLL成品。
- `tools/client-lab/Set-KkNativeEgress.ps1`：专用实验VM出站默认拒绝工具；默认只输出Plan，不修改宿主。

2026-09-22源码更新范围与验证边界见[本次更新](docs/UPDATE_20260922.md)。旧native开发入口仍限制回环；public为独立显式入口，不代表已完成公网部署或原客户端验收。

## 验证服务源码

需要 Python 3.12；在本仓库根目录运行：

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s server/tests -p "test_*.py" -v
python -m unittest discover -s client-adapter/tests -p "test_*.py" -v
python -m unittest discover -s tools/resource-recovery -p "test_*.py" -v
```

测试中的密码、身份、数据包是明确的合成夹具，不可用作真实部署默认凭据。

测试不启动游戏。成功只证明相应代码合同，不能替代原客户端逐项验收。

## 当前状态

已实现多个本地行为子集：账号密码验证、实际库存装备与消耗、房间/准备/对战中继、结算收据和部分任务、修炼、商城、邮件、护符、升级等显式规则。新服务按app/handler/storage拆分，仍保持原有兼容入口。

仍不完整：所有协议和模式、原服权威战斗/经济规则、战斗热重连，以及新加密链的原客户端端到端验证。新增任务/PVE/奖励均有适用范围和显式开关，不代表复原完整旧服务端。

来自客户端字段或行为的兼容代码，与本地设计的账号/房间/排名策略分别说明；后者标记为 `SYSTEM_DESIGN_INFERRED / PROVISIONAL`。

## 安全与权利

默认绑定回环地址；显式lab模式仅供指定私网对端的免认证双VM夹具，不能冒充正常登录或暴露到公网。旧 SDK 的密码编码与游戏帧算法不构成现代网络安全协议；新的加密开发模式也尚未完成原端与独立安全验收。本仓库不提供旧运营服务的凭据或访问。

本次未替权利人选择开源许可证；公开可见不等于授予任意复制或再分发许可。第三方名称与软件权利归各自权利人所有。请在明确授权范围后选择适当 LICENSE。

建议阅读顺序：[部署操作](docs/DEPLOYMENT_WALKTHROUGH.md) → [兼容实现详解](docs/CLIENT_COMPATIBILITY.md) → [服务边界](docs/SERVICE.md)。另见 [安全说明](SECURITY.md)。
