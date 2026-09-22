# 服务运行与数据库

> 2026-09-22：下文原窗口/本机PID模式仍保留；新`--mode native`是独立、回环限定的加密开发路径，不与旧模式自动混用。见[本次更新](UPDATE_20260922.md)。

## 1. 无客户端测试

安装 `requirements.txt` 后运行 README 的单元测试。测试使用临时 SQLite 和回环随机端口；不需要生产数据库、VM、专有资源或已保存账号。

## 2. 真实客户端模式

Windows 上，使用自己合法持有、已适配到本地服务的客户端副本：

```powershell
python -m server.kk_local.sdo_service --client-root C:\MyGameLab\Client --runtime C:\MyGameLab\Run-001 --database C:\MyGameLab\accounts.sqlite3
```

`runtime` 每次必须是新目录。默认端口为本地账号 API 17999、登录 18000、游戏 TCP/UDP 18001、SDK HTTP 18082。路径可改，但适配模块与启动脚本中的路径/端口也必须相应配置。

这个命令只启动服务，不会自动复制或修改客户端，不会自动完成 GPK、SDK 启动与角色表初始化。缺少客户端配置、可用兼容模块、资源或就绪信息时可能无法进入游戏。不要通过伪造就绪文件绕过检查。

`sdo_service` 使用客户端根下配置解析地图，只在支持的模式/地图范围内准入。提供服务代码并不等于任意版本客户端即插即用。

真实配置加载依赖随本仓库发布的`tools/resource-recovery/spf2_index.py`、`spf2_extract.py`。这两项不是pip依赖，部署时必须保留相对路径。无mock的合成包测试位于`server/tests/test_client_config_package.py`；资源包由测试临时生成，不发布原客户端资源。

## 3. 账号

数据库通过 `Store` 自动建表，不随仓库提供预置真实账号。注册与登录在 `auth_service.py` 的 `kk-local-auth-v1` 本地 API；`tests/test_auth.py` 展示合成请求。API 消息为4字节网络序长度加 UTF-8 JSON，含 `schema/operation/arguments`。

典型顺序：register → login → regions → select_region → bind_client。正常路径还需要 Windows TCP/UDP 进程归属校验；不要发送测试固定回复代替密码认证。

若需为已有本地账号设置密码，使用 `python -m server.kk_local.accounts --database <路径> set-password <账号>`，在终端隐藏输入；不要将密码写在命令行。

## 4. 存储与业务

`accounts` 保存360字节档案，`inventory` 保存68字节物品记录；余额、商品目录、成交收据及训练状态分别持久化。完整原始记录保留未知字段。复制活动数据库应使用 SQLite backup，而不是单独复制主文件遗漏 WAL。

商城必须先导入有来源的完整记录并显式启用支持的商品类型。未启用或未实现分支不得扣款、授予物品或假报成功。所有奖励/经济政策均应单独审查。

`rooms.py` 的共享协调器是实验性本地多账号功能；原 SDK 认证服务仍限制每区单活动客户端。跨机器服务不能沿用本机 PID 验证，必须另行设计认证、TLS、授权及状态同步。

## 5. 协议扩展方法

1. 从原客户端实际请求确认方向、连接阶段、ID 和长度。
2. 检查消费函数，确认读取字段、类型、尾部与副作用，不只看消息号相近。
3. 在 `layouts.py/menu_layouts.py` 写严格解析，保留未明字段。
4. 在服务层校验当前账号、阶段、归属、容量、幂等和失败路径。
5. 先用合成夹具测试，再在授权隔离客户端验证实际显示/状态变化。
6. 区分结构恢复、本地服务设计和原客户端验证，不用一个测试通过覆盖整个协议族。

本包不携带原始研究证据。源码中的地址、ID和参考名称仅用于追踪兼容来源，不代表恢复了旧服务器源码。
