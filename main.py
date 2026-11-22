import asyncio

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_phira_mp"

HELP_TEXT = """Phira 多人服务器指令（/p + 子指令）：
/p房间                      查看在线房间
/p房间 全部                 查看全部房间（含隐藏，管理员）
/p创建房间 <房间ID>          创建房间（管理员）
/p删除房间 <房间ID>          解散并删除房间（管理员）
/p管理消息 <房间ID|all> <内容>  发送管理消息（管理员）
/p公告 <内容>                向所有在线玩家发送公告（管理员）
/p状态                      查看服务器状态
/p帮助                      显示本帮助"""

# 同时支持 /p房间 和 /p 房间 两种写法
COMMAND_ALIASES = {
    "p帮助",
    "p房间",
    "p列表",
    "p状态",
    "p创建房间",
    "p建房",
    "p删除房间",
    "p解散房间",
    "p管理消息",
    "p喊话",
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
    "将 Phira 多人服务器的房间查询与管理功能接入 AstrBot，使用 /p 指令。",
    "1.0.0",
    "https://github.com/lisongyun857/phira-mp-node",
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
            reply = await self._build_reply(sub, rest, is_admin)
        except Exception as e:  # 避免插件因单个请求异常而崩溃
            logger.error(f"Phira 指令执行失败: {e}")
            reply = f"指令执行失败：{e}"

        yield event.plain_result(reply)

    async def _build_reply(self, sub: str, rest: str, is_admin: bool) -> str:
        if sub in ("", "帮助", "help"):
            return HELP_TEXT

        if sub in ("状态", "status"):
            response = await self._request("GET", "/api/status")
            if not response.get("ok"):
                return self._error_text(response)
            data = response.get("data") or {}
            return (
                f"运行时间 {self._format_duration(data.get('uptime', 0))} · "
                f"在线用户 {data.get('users', 0)} · 连接 {data.get('sessions', 0)} · "
                f"房间 {data.get('rooms', 0)} · 内存 {data.get('memoryMB', 0)} MB"
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
            line = (
                f"{index}. {room.get('id', '?')} · "
                f"{room.get('users', 0)}/{room.get('maxUsers', 8)} 人 · {chart}"
            )
            if room.get("locked"):
                line += " · 已锁定"
            if room.get("hidden"):
                line += " [隐藏]"
            lines.append(line)
        return "\n".join(lines)
