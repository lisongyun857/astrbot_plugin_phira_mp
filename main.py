import asyncio
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_phira_mp"

HELP_TEXT = """Phira 多人服务器指令（/p + 子指令）：
/p登录                      绑定 Phira 账号 / 查看当前绑定
/p我的                      查看当前绑定的 Phira 账号
/p解绑                      解除绑定（需回复「确认」）
/p房间                      查看在线房间
/p房间 全部                 查看全部房间（含隐藏，管理员）
/p房间信息                  查看自己所在房间的成员与状态
/p锁定 /p解锁               锁定 / 解锁自己的房间（房主）
/p轮换 开|关                切换房间轮换模式（房主）
/p选谱 <谱面ID>             选择谱面（房主）
/p开始                      开始游戏（房主）
/p准备 /p取消准备           准备 / 取消准备
/p中止                      中止当前游戏
/p踢人 <玩家ID或昵称>        踢出房间成员（房主）
/p转让房主 <玩家ID或昵称>     把房主转让给其他玩家（房主）
/p解散 [房间ID]             解散自己的房间；管理员可指定房间
/p创建房间 <房间ID>          创建房间（管理员）
/p删除房间 <房间ID>          解散并删除房间（管理员）
/p管理消息 <房间ID|all> <内容>  发送管理消息（管理员）
/p建房开关 开|关             禁止/允许玩家在客户端创建房间（管理员）
/p公告 <内容>                向所有在线玩家发送公告（管理员）
/p状态                      查看服务器状态
/p帮助                      显示本帮助"""

# 同时支持 /p房间 和 /p 房间 两种写法
COMMAND_ALIASES = {
    "p帮助",
    "p登录",
    "p绑定",
    "p我的",
    "p解绑",
    "plogin",
    "pme",
    "punbind",
    "p房间",
    "p列表",
    "p状态",
    "p房间信息",
    "p我的房间",
    "p信息",
    "p锁定",
    "p解锁",
    "p锁房",
    "p轮换",
    "p选谱",
    "p开始",
    "p准备",
    "p取消准备",
    "p中止",
    "p踢人",
    "p转让房主",
    "p给房主",
    "p解散",
    "p创建房间",
    "p建房",
    "p删除房间",
    "p解散房间",
    "p管理消息",
    "p喊话",
    "p建房开关",
    "p房间创建",
    "proomlock",
    "p公告",
    "phelp",
    "prooms",
    "pstatus",
    "pcreate",
    "pdelete",
    "psay",
    "pannounce",
    "proominfo",
    "plock",
    "punlock",
    "pcycle",
    "pselect",
    "pstart",
    "pready",
    "punready",
    "pabort",
    "pkick",
    "ptransfer",
    "pdissolve",
}


@register(
    PLUGIN_NAME,
    "lisongyun857",
    "查询和管理 Phira 多人服务器，支持 QQ 绑定账号并在 QQ 内控制自己创建的房间，使用 /p 指令。",
    "1.2.0",
    "https://github.com/lisongyun857/astrbot_plugin_phira_mp",
)
class PhiraMpPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.api_base = str(config.get("api_base") or "http://127.0.0.1:3052").rstrip("/")
        self.api_token = str(config.get("api_token") or "")

        admin_ids = config.get("admin_ids") or []
        if isinstance(admin_ids, str):
            admin_ids = [admin_ids]
        self.admin_ids = {str(item).strip() for item in admin_ids if str(item).strip()}

        try:
            self.timeout = max(1, int(config.get("timeout") or 5))
        except (TypeError, ValueError):
            self.timeout = 5

        # QQ -> 待确认的解绑请求
        self._pending_unbind: dict[str, dict] = {}

    @filter.command("p", alias=COMMAND_ALIASES)
    async def phira(self, event: AstrMessageEvent):
        """Phira 多人服务器查询与管理指令。"""
        text = event.message_str.strip()
        # 兼容 /p房间（指令名自带参数）与 /p 房间 两种写法
        if text[:1].lower() == "p":
            text = text[1:].lstrip()

        parts = text.split(None, 1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        sender_id = str(event.get_sender_id())
        is_admin = sender_id in self.admin_ids

        try:
            reply = await self._build_reply(sub, rest, sender_id, is_admin)
        except Exception as e:  # 避免插件因单个请求异常而崩溃
            logger.error(f"Phira 指令执行失败: {e}")
            reply = f"指令执行失败：{e}"

        yield event.plain_result(reply)

    async def _build_reply(
        self, sub: str, rest: str, sender_id: str, is_admin: bool
    ) -> str:
        if sub not in ("解绑", "unbind"):
            self._pending_unbind.pop(sender_id, None)

        if sub in ("", "帮助", "help"):
            return HELP_TEXT

        if sub in ("登录", "绑定", "login", "bind"):
            return await self._login(sender_id)

        if sub in ("我的", "绑定查询", "me", "binding"):
            return await self._my_binding(sender_id)

        if sub in ("解绑", "unbind"):
            return await self._request_unbind(sender_id)

        if sub in ("状态", "status"):
            response = await self._request("GET", "/api/status")
            if not response.get("ok"):
                return self._error_text(response)
            data = response.get("data") or {}
            return (
                f"运行时间 {self._format_duration(data.get('uptime', 0))} · "
                f"在线用户 {data.get('users', 0)} · 连接 {data.get('sessions', 0)} · "
                f"房间 {data.get('rooms', 0)} · "
                f"客户端建房 {'允许' if data.get('allowClientCreateRoom', True) else '禁止'} · "
                f"内存 {data.get('memoryMB', 0)} MB"
            )

        if sub in ("房间", "房间列表", "列表", "rooms"):
            include_hidden = rest.lower() in ("全部", "all")
            if include_hidden and not is_admin:
                return "权限不足：只有管理员可以查看隐藏房间"
            query = "?all=1" if include_hidden else ""
            response = await self._request("GET", f"/api/rooms{query}")
            if not response.get("ok"):
                return self._error_text(response)
            rooms = (response.get("data") or {}).get("rooms") or []
            return self._format_rooms(rooms)

        if sub in ("房间信息", "我的房间", "信息", "roominfo", "myroom"):
            return await self._room_action(sender_id, is_admin, "info")

        if sub in ("锁定", "锁房", "lock"):
            return await self._room_action(sender_id, is_admin, "lock", {"locked": True})

        if sub in ("解锁", "unlock"):
            return await self._room_action(sender_id, is_admin, "lock", {"locked": False})

        if sub in ("轮换", "cycle"):
            value = rest.strip().lower()
            if value in ("开", "on", "enable", "enabled", "true", "开启", "允许"):
                return await self._room_action(sender_id, is_admin, "cycle", {"enabled": True})
            if value in ("关", "off", "disable", "disabled", "false", "关闭", "禁止"):
                return await self._room_action(sender_id, is_admin, "cycle", {"enabled": False})
            return "用法：/p轮换 开|关"

        if sub in ("选谱", "select"):
            chart_raw = rest.strip()
            if not chart_raw:
                return "用法：/p选谱 <谱面ID>"
            try:
                chart_id = int(chart_raw)
            except ValueError:
                return "谱面 ID 必须是整数"
            return await self._room_action(
                sender_id, is_admin, "select-chart", {"chartId": chart_id}
            )

        if sub in ("开始", "start"):
            return await self._room_action(sender_id, is_admin, "start")

        if sub in ("准备", "ready"):
            return await self._room_action(sender_id, is_admin, "ready")

        if sub in ("取消准备", "unready", "cancelready"):
            return await self._room_action(sender_id, is_admin, "cancel-ready")

        if sub in ("中止", "abort"):
            return await self._room_action(sender_id, is_admin, "abort")

        if sub in ("踢人", "kick"):
            target = self._target_payload(rest)
            if not target:
                return "用法：/p踢人 <玩家ID或昵称>"
            return await self._room_action(sender_id, is_admin, "kick", target)

        if sub in ("转让房主", "给房主", "transfer", "transferhost"):
            target = self._target_payload(rest)
            if not target:
                return "用法：/p转让房主 <玩家ID或昵称>"
            return await self._room_action(sender_id, is_admin, "set-host", target)

        if sub in ("解散", "dissolve"):
            extra = {}
            room_id = rest.strip()
            if room_id:
                extra["roomId"] = room_id
            return await self._room_action(sender_id, is_admin, "dissolve", extra)

        if sub in ("创建房间", "建房", "create"):
            denied = self._check_admin(is_admin, "创建房间")
            if denied:
                return denied
            room_id = rest.strip()
            if not room_id:
                return "用法：/p创建房间 <房间ID>"
            response = await self._request("POST", "/api/rooms/create", {"id": room_id})
            if not response.get("ok"):
                return self._error_text(response)
            hidden = "（隐藏）" if room_id.startswith("_") else ""
            return f"已创建房间 {room_id}{hidden}，第一个加入的玩家将成为房主"

        if sub in ("删除房间", "解散房间", "delete"):
            denied = self._check_admin(is_admin, "删除房间")
            if denied:
                return denied
            room_id = rest.strip()
            if not room_id:
                return "用法：/p删除房间 <房间ID>"
            response = await self._request("POST", "/api/rooms/delete", {"id": room_id})
            if not response.get("ok"):
                return self._error_text(response)
            members = (response.get("data") or {}).get("members", 0)
            return f"已删除房间 {room_id}，已通知房间内 {members} 人离开"

        if sub in ("管理消息", "喊话", "say"):
            denied = self._check_admin(is_admin, "发送管理消息")
            if denied:
                return denied
            target_parts = rest.split(None, 1)
            target = target_parts[0].strip() if target_parts else ""
            content = target_parts[1].strip() if len(target_parts) > 1 else ""
            if not target or not content:
                return "用法：/p管理消息 <房间ID|all> <内容>"
            response = await self._request(
                "POST", "/api/say", {"target": target, "content": content}
            )
            if not response.get("ok"):
                return self._error_text(response)
            if target == "all":
                rooms = (response.get("data") or {}).get("rooms", 0)
                return f"已向 {rooms} 个房间发送管理消息"
            return f"已向房间 {target} 发送管理消息"

        if sub in ("建房开关", "房间创建", "roomlock"):
            denied = self._check_admin(is_admin, "切换建房开关")
            if denied:
                return denied
            value = rest.strip().lower()
            if not value:
                response = await self._request("GET", "/api/status")
                if not response.get("ok"):
                    return self._error_text(response)
                enabled = bool((response.get("data") or {}).get("allowClientCreateRoom", True))
                return f"当前{'允许' if enabled else '禁止'}玩家在客户端创建房间"

            if value in ("开", "on", "enable", "enabled", "true", "允许"):
                enabled = True
            elif value in ("关", "off", "disable", "disabled", "false", "禁止"):
                enabled = False
            else:
                return "用法：/p建房开关 开|关"

            response = await self._request(
                "POST", "/api/room-creation", {"enabled": enabled}
            )
            if not response.get("ok"):
                return self._error_text(response)
            if enabled:
                return "已允许玩家在客户端创建房间"
            return "已禁止玩家在客户端创建房间（控制台、QQ、API 建房不受影响）"

        if sub in ("公告", "announce"):
            denied = self._check_admin(is_admin, "发送公告")
            if denied:
                return denied
            content = rest.strip()
            if not content:
                return "用法：/p公告 <内容>"
            response = await self._request("POST", "/api/announce", {"content": content})
            if not response.get("ok"):
                return self._error_text(response)
            users = (response.get("data") or {}).get("users", 0)
            return f"已向 {users} 位在线玩家发送公告"

        return f"未知子指令：{sub}，发送 /p帮助 查看可用指令"

    async def _room_action(
        self, user_id: str, is_admin: bool, action: str, extra: dict | None = None
    ) -> str:
        payload = {
            "platform": "qq",
            "userId": user_id,
            "action": action,
            "admin": is_admin,
        }
        if extra:
            payload.update(extra)
        response = await self._request("POST", "/api/room/action", payload)
        if not response.get("ok"):
            return self._error_text(response)
        return self._format_room_action(action, response.get("data") or {})

    @staticmethod
    def _target_payload(text: str) -> dict | None:
        target = text.strip()
        if not target:
            return None
        if target.isdigit():
            return {"targetId": int(target)}
        return {"targetName": target}

    @staticmethod
    def _format_room_action(action: str, data: dict) -> str:
        room_id = data.get("id") or "?"
        if action == "info":
            return PhiraMpPlugin._format_room_detail(data.get("room") or {})

        if action == "lock":
            return f"已{'锁定' if data.get('locked') else '解锁'}房间 {room_id}"

        if action == "cycle":
            return f"已{'开启' if data.get('cycle') else '关闭'}房间 {room_id} 的轮换模式"

        if action == "select-chart":
            chart = data.get("chart") or {}
            return f"已选择谱面 {chart.get('name', '?')}（ID {chart.get('id', '?')}）"

        if action == "start":
            return "已开始游戏，等待所有玩家准备"

        if action == "ready":
            return "已准备"

        if action == "cancel-ready":
            return "已取消准备"

        if action == "abort":
            return "已中止本局游戏"

        if action == "kick":
            target = data.get("target") or {}
            text = f"已把 {target.get('name', '?')}（ID {target.get('id', '?')}）移出房间"
            if data.get("roomDeleted"):
                text += "\n房间已无玩家，已自动解散"
            return text

        if action == "set-host":
            host = data.get("host") or {}
            return f"已把房主转让给 {host.get('name', '?')}（ID {host.get('id', '?')}）"

        if action == "dissolve":
            return f"已解散房间 {room_id}，房间内 {data.get('members', 0)} 人已退出"

        return "操作成功"

    @staticmethod
    def _format_room_detail(room: dict) -> str:
        if not room:
            return "你当前不在任何房间"
        state = {
            "SelectChart": "未开始",
            "WaitForReady": "准备中",
            "Playing": "游戏中",
        }.get(room.get("state"), "未知")
        host = room.get("host") or {}
        chart = room.get("chart") or {}
        chart_text = f"{chart.get('name', '未选谱')}"
        if chart:
            chart_text += f"（ID {chart.get('id', '?')}）"
        lines = [
            f"房间 {room.get('id', '?')} · {room.get('users', 0)}/{room.get('maxUsers', 8)} 人"
            f"（旁观 {room.get('monitors', 0)}）· {state}",
            f"房主：{host.get('name', '无')}（ID {host.get('id', '?')}）",
            f"谱面：{chart_text}",
            f"锁定：{'是' if room.get('locked') else '否'} · "
            f"轮换：{'开' if room.get('cycle') else '关'}",
        ]
        members = room.get("members") or []
        if members:
            lines.append("成员：")
            for index, member in enumerate(members, 1):
                role = "旁观" if member.get("monitor") else "玩家"
                if not member.get("online"):
                    role += "·掉线"
                if host and member.get("id") == host.get("id"):
                    role += "·房主"
                lines.append(
                    f"  {index}. {member.get('name', '?')}"
                    f"（ID {member.get('id', '?')}）· {role}"
                )
        return "\n".join(lines)

    async def _login(self, user_id: str) -> str:
        response = await self._fetch_binding(user_id)
        if not response.get("ok"):
            return self._error_text(response)

        data = response.get("data") or {}
        if data.get("bound"):
            return self._format_binding(data.get("binding") or {}, True)

        created = await self._request(
            "POST",
            "/api/login/link",
            {"platform": "qq", "userId": user_id},
        )
        if not created.get("ok"):
            return f"生成登录链接失败：{created.get('error') or '未知错误'}"

        created_data = created.get("data") or {}
        link = created_data.get("link")
        if not link:
            return "生成登录链接失败：服务端没有返回登录链接"

        minutes = self._link_minutes(created_data.get("expiresAt"))
        return (
            f"请在 {minutes} 分钟内打开以下链接，使用 Phira 邮箱和密码登录：\n"
            f"{link}\n\n"
            "链接只能绑定到你的 QQ，请勿转发。"
        )

    async def _my_binding(self, user_id: str) -> str:
        response = await self._fetch_binding(user_id)
        if not response.get("ok"):
            return self._error_text(response)

        data = response.get("data") or {}
        if not data.get("bound"):
            return "你还没有绑定 Phira 账号，请先发送 /p登录"
        return self._format_binding(data.get("binding") or {}, False)

    async def _request_unbind(self, user_id: str) -> str:
        response = await self._fetch_binding(user_id)
        if not response.get("ok"):
            return self._error_text(response)

        data = response.get("data") or {}
        if not data.get("bound"):
            return "你还没有绑定 Phira 账号"

        binding = data.get("binding") or {}
        name, phira_id = self._binding_identity(binding)
        self._pending_unbind[user_id] = {
            "expires": time.monotonic() + 60,
            "binding": binding,
        }
        return (
            f"确认解绑当前 Phira 账号 {name}（ID {phira_id}）吗？\n"
            "请在 60 秒内回复「确认」，或忽略本条消息取消。"
        )

    async def _fetch_binding(self, user_id: str) -> dict:
        query = urlencode({"platform": "qq", "userId": user_id})
        return await self._request("GET", f"/api/login/binding?{query}")

    @filter.regex(r"^[\s/!！]*确认[\s。.!！]*$")
    async def confirm_unbind(self, event: AstrMessageEvent):
        """确认解绑 Phira 账号。"""
        user_id = str(event.get_sender_id())
        pending = self._pending_unbind.get(user_id)
        if not pending:
            return

        if time.monotonic() > pending.get("expires", 0):
            self._pending_unbind.pop(user_id, None)
            yield event.plain_result("确认已过期，请重新发送 /p解绑")
            return

        self._pending_unbind.pop(user_id, None)
        response = await self._request(
            "DELETE",
            "/api/login/binding",
            {"platform": "qq", "userId": user_id},
        )
        if not response.get("ok"):
            yield event.plain_result(self._error_text(response))
            return

        if not (response.get("data") or {}).get("unbound"):
            yield event.plain_result("没有找到绑定记录，可能已经解绑")
            return

        name, phira_id = self._binding_identity(pending.get("binding") or {})
        yield event.plain_result(f"已解除与 Phira 账号 {name}（ID {phira_id}）的绑定")

    @staticmethod
    def _binding_identity(binding: dict) -> tuple[str, str]:
        name = str(binding.get("phiraName") or "未知玩家")
        phira_id = str(binding.get("phiraId", "?"))
        return name, phira_id

    def _format_binding(self, binding: dict, include_hint: bool) -> str:
        name, phira_id = self._binding_identity(binding)
        lines = [f"当前绑定：{name}（ID {phira_id}）"]
        bound_at = self._format_time(binding.get("boundAt"))
        if bound_at:
            lines.append(f"绑定时间：{bound_at}")
        if include_hint:
            lines.append("如需更换账号，请先 /p解绑。")
        return "\n".join(lines)

    @staticmethod
    def _format_time(value) -> str:
        if not value:
            return ""
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone().strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _link_minutes(expires_at) -> int:
        if not expires_at:
            return 10
        try:
            parsed = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            remaining = (parsed - datetime.now(timezone.utc)).total_seconds()
            return max(1, round(remaining / 60))
        except (TypeError, ValueError):
            return 10

    async def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method, f"{self.api_base}{path}", json=payload, headers=headers
                ) as response:
                    try:
                        data = await response.json(content_type=None)
                    except Exception:
                        text = await response.text()
                        return {
                            "ok": False,
                            "error": f"HTTP {response.status}: {text[:200]}",
                        }

                    if not isinstance(data, dict):
                        return {"ok": False, "error": "管理接口返回了无效数据"}
                    if response.status >= 400:
                        return {
                            "ok": False,
                            "error": data.get("error") or f"HTTP {response.status}",
                        }
                    return data
        except asyncio.TimeoutError:
            return {"ok": False, "error": "连接 Phira 服务器管理接口超时"}
        except aiohttp.ClientError as e:
            return {"ok": False, "error": f"无法连接 Phira 服务器管理接口：{e}"}

    @staticmethod
    def _check_admin(is_admin: bool, action: str) -> str | None:
        if is_admin:
            return None
        return f"权限不足：只有管理员可以{action}"

    @staticmethod
    def _error_text(response: dict) -> str:
        return f"操作失败：{response.get('error') or '未知错误'}"

    @staticmethod
    def _format_duration(seconds) -> str:
        try:
            total = int(float(seconds))
        except (TypeError, ValueError):
            total = 0
        total = max(0, total)
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}小时{minutes}分{secs}秒"
        return f"{minutes}分{secs}秒"

    @staticmethod
    def _format_rooms(rooms: list) -> str:
        if not rooms:
            return "当前没有在线房间"

        lines = [f"在线房间（{len(rooms)}）："]
        for index, room in enumerate(rooms, 1):
            chart = (room.get("chart") or {}).get("name") or "未选谱"
            state = {
                "SelectChart": "未开始",
                "WaitForReady": "准备中",
                "Playing": "游戏中",
            }.get(room.get("state"), "未知")
            line = (
                f"{index}. {room.get('id', '?')} · "
                f"{room.get('users', 0)}/{room.get('maxUsers', 8)} 人 · {state} · {chart}"
            )
            if room.get("locked"):
                line += " · 已锁定"
            if room.get("hidden"):
                line += " [隐藏]"
            lines.append(line)
        return "\n".join(lines)
