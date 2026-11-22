# Phira 多人服务器助手

AstrBot 插件，将 [phira-mp-node](https://github.com/lisongyun857/phira-mp-node) 的房间查询、服务器状态、建房、删房、管理消息和公告接入 QQ，支持 QQ 绑定 Phira 账号，统一使用 `/p` 指令。

## 功能

- 查看在线房间、房间人数、谱面、锁定状态
- 查看服务器运行时间、在线用户、连接数、房间数和内存占用
- 管理员创建/解散房间
- 管理员向指定房间或全部房间发送管理消息
- 管理员向所有在线玩家发送公告
- 管理员禁止/允许玩家在客户端创建房间
- QQ 绑定 Phira 账号：生成一次性登录链接、查询绑定、确认后解绑
- 支持 `/p房间` 和 `/p 房间` 两种写法

## 前置条件

1. AstrBot `>= 4.0.0`
2. 已部署 [phira-mp-node](https://github.com/lisongyun857/phira-mp-node)，并在 `server_config.yml` 中启用 HTTP API：

```yaml
api:
  enabled: true
  host: "127.0.0.1"
  port: 3052
  access_token: ""

login:
  enabled: true
  public_url: "https://phira.lisongyun.top"
```

3. AstrBot 已配置 NapCat / aiocqhttp 适配器。

> AstrBot 与 phiraserver 不在同一台机器时，把 `api.host` 改为 `0.0.0.0`，设置 `access_token`，并在插件中填写对应的 API 地址和令牌。
>
> 账号绑定需要把 `login.public_url` 下的 `/login/` 反向代理到 phiraserver 的 `/login/`，例如 `https://phira.lisongyun.top/login/` → `http://127.0.0.1:3052/login/`。

## 安装

- 在 AstrBot 插件市场搜索「Phira 多人服务器助手」安装；
- 或在 AstrBot WebUI 上传插件 zip；
- 或将本目录放入 `AstrBot/data/plugins/astrbot_plugin_phira_mp` 后重载插件。

## 配置

| 配置项 | 说明 | 默认值 |
| --- | --- | --- |
| `api_base` | phiraserver HTTP API 地址 | `http://127.0.0.1:3052` |
| `api_token` | API 访问令牌，与 `api.access_token` 一致 | 空 |
| `admin_ids` | 管理员 QQ 号列表 | 空 |
| `timeout` | 请求超时（秒） | `5` |

只有 `admin_ids` 中的 QQ 可以创建/删除房间、切换建房开关、发送管理消息和公告。账号绑定对所有人生效，不需要管理员。

## 指令

| 指令 | 说明 | 权限 |
| --- | --- | --- |
| `/p登录` | 绑定 Phira 账号；已绑定则显示账号信息 | 所有人 |
| `/p我的` | 查看当前绑定的 Phira 账号 | 所有人 |
| `/p解绑` | 解除绑定，需在 60 秒内回复「确认」 | 所有人 |
| `/p房间` | 查看在线房间 | 所有人 |
| `/p房间 全部` | 查看全部房间（含隐藏） | 管理员 |
| `/p状态` | 查看服务器状态 | 所有人 |
| `/p创建房间 <房间ID>` | 创建房间 | 管理员 |
| `/p删除房间 <房间ID>` | 解散并删除房间 | 管理员 |
| `/p管理消息 <房间ID\|all> <内容>` | 向房间发送管理消息 | 管理员 |
| `/p建房开关 开\|关` | 允许/禁止玩家在客户端创建房间 | 管理员 |
| `/p公告 <内容>` | 向所有在线玩家发送公告 | 管理员 |
| `/p帮助` | 显示帮助 | 所有人 |

房间 ID 只能包含字母、数字、`-`、`_`，长度 1~20；以 `_` 开头的房间为隐藏房间。

## 服务端 API

插件调用 phiraserver 的以下接口，设置 `access_token` 后需携带 `Authorization: Bearer <token>`：

```text
GET    /api/status
POST   /api/room-creation      {"enabled": false}
GET    /api/rooms?all=1
POST   /api/rooms/create       {"id": "room1"}
POST   /api/rooms/delete       {"id": "room1"}
POST   /api/say                {"target": "room1|all", "content": "内容"}
POST   /api/announce           {"content": "内容"}
POST   /api/login/link         {"platform": "qq", "userId": "123456789"}
GET    /api/login/binding?platform=qq&userId=123456789
DELETE /api/login/binding      {"platform": "qq", "userId": "123456789"}
```

## 常见问题

**连接失败 / 操作失败：无法连接 Phira 服务器管理接口**

检查 phiraserver 是否启动、`api.port` 是否与插件 `api_base` 一致、防火墙是否放行。AstrBot 在 Docker 中运行时，`127.0.0.1` 指向容器自身，需要改用宿主机地址。

**权限不足**

把操作者的 QQ 号加入插件配置 `admin_ids`，保存后重载插件。

**/p登录 提示生成登录链接失败**

检查服务端 `login.enabled` 是否为 `true`、`login.public_url` 是否配置，以及反向代理是否把 `/login/` 转发到了 phiraserver。

**登录链接打不开**

确认 `login.public_url` 是公网可访问的 HTTPS 地址，并且反向代理指向 `http://127.0.0.1:3052/login/`。链接默认 10 分钟有效，过期后重新发送 `/p登录`。

**启动 phiraserver 时报 EADDRINUSE**

端口已被旧实例占用，先停止旧进程再启动，避免同时运行多个实例。

**指令没有响应**

确认 AstrBot 的唤醒前缀与用法一致。默认前缀为 `/`，直接发送 `/p状态` 即可；如果修改过前缀，需要使用对应的前缀。

## License

[Apache-2.0](LICENSE)
