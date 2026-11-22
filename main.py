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
}


@register(
    PLUGIN_NAME,
    "lisongyun857",
    "查询和管理 Phira 多人服务器，并支持 QQ 绑定 Phira 账号，使用 /p 指令。",
    "1.1.0",
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
