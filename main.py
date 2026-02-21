from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
import aiohttp
import asyncio
import random
import re
import hashlib
import json


@dataclass
class Song:
    id: str
    name: str
    artist: str
    album: str
    duration: int
    url: str = ""
    cover: str = ""
    source: str = "qq"
    
    def to_display(self) -> str:
        source_icon = "QQ音乐" if self.source == "qq" else "网易云"
        return f"🎵 {self.name} - {self.artist} [{source_icon}]"


@dataclass
class MusicRoom:
    room_id: str
    owner_id: str
    owner_name: str
    group_id: str
    playlist: list = field(default_factory=list)
    current_index: int = -1
    members: dict = field(default_factory=dict)
    is_playing: bool = False
    create_time: datetime = field(default_factory=datetime.now)
    play_mode: str = "sequence"
    
    def add_song(self, song: Song):
        self.playlist.append(song)
    
    def remove_song(self, index: int) -> Optional[Song]:
        if 0 <= index < len(self.playlist):
            return self.playlist.pop(index)
        return None
    
    def get_current_song(self) -> Optional[Song]:
        if 0 <= self.current_index < len(self.playlist):
            return self.playlist[self.current_index]
        return None
    
    def next_song(self) -> Optional[Song]:
        if not self.playlist:
            return None
        if self.play_mode == "random":
            self.current_index = random.randint(0, len(self.playlist) - 1)
        else:
            self.current_index = (self.current_index + 1) % len(self.playlist)
        return self.get_current_song()
    
    def prev_song(self) -> Optional[Song]:
        if not self.playlist:
            return None
        self.current_index = (self.current_index - 1) % len(self.playlist)
        return self.get_current_song()
    
    def add_member(self, user_id: str, user_name: str):
        self.members[user_id] = user_name
    
    def remove_member(self, user_id: str) -> bool:
        if user_id in self.members:
            del self.members[user_id]
            return True
        return False


class MusicAPI:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        self.timeout = aiohttp.ClientTimeout(total=10)
    
    async def search(self, keyword: str, limit: int = 5) -> list:
        songs = await self._search_qq(keyword, limit)
        if songs:
            return songs
        songs = await self._search_netease(keyword, limit)
        return songs
    
    async def _search_qq(self, keyword: str, limit: int) -> list:
        url = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
        params = {
            "w": keyword,
            "p": 1,
            "n": limit,
            "format": "json",
            "aggr": 1,
            "lossless": 0,
            "cr": 1,
            "new_json": 1
        }
        headers = {
            **self.headers,
            "Referer": "https://y.qq.com",
        }
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    text = await resp.text()
                    data = json.loads(text)
                    
            if data.get("code") != 0:
                return []
            
            songs = []
            song_list = data.get("data", {}).get("song", {}).get("list", [])
            for item in song_list:
                singers = item.get("singer", [])
                artists = ", ".join([s.get("name", "") for s in singers]) if singers else "未知歌手"
                album_info = item.get("album", {})
                song_mid = item.get("mid", "")
                
                song = Song(
                    id=song_mid,
                    name=item.get("name", ""),
                    artist=artists,
                    album=album_info.get("name", "") if album_info else "",
                    duration=item.get("interval", 0),
                    cover=self._get_qq_album_cover(album_info.get("mid", "")),
                    source="qq"
                )
                songs.append(song)
            return songs
        except Exception as e:
            logger.error(f"QQ音乐搜索失败: {e}")
            return []
    
    async def _search_netease(self, keyword: str, limit: int) -> list:
        url = "https://music.163.com/api/search/get"
        params = {
            "s": keyword,
            "type": 1,
            "limit": limit,
            "offset": 0
        }
        headers = {
            **self.headers,
            "Referer": "https://music.163.com",
        }
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    data = await resp.json()
                    
            if data.get("code") != 200:
                return []
            
            songs = []
            result = data.get("result", {}).get("songs", [])
            for item in result:
                artists = ", ".join([a["name"] for a in item.get("artists", [])])
                song_id = item.get("id", "")
                
                song = Song(
                    id=str(song_id),
                    name=item.get("name", ""),
                    artist=artists,
                    album=item.get("album", {}).get("name", ""),
                    duration=item.get("duration", 0) // 1000,
                    cover=item.get("album", {}).get("picUrl", ""),
                    source="netease"
                )
                songs.append(song)
            return songs
        except Exception as e:
            logger.error(f"网易云音乐搜索失败: {e}")
            return []
    
    def _get_qq_album_cover(self, album_mid: str) -> str:
        if album_mid:
            return f"https://y.qq.com/music/photo_new/T002R300x300M000{album_mid}.jpg"
        return ""
    
    async def get_song_url(self, song: Song) -> str:
        if song.source == "qq":
            return await self._get_qq_song_url(song.id)
        else:
            return await self._get_netease_song_url(song.id)
    
    async def _get_qq_song_url(self, song_mid: str) -> str:
        try:
            url = "https://u.y.qq.com/cgi-bin/musicu.fcg"
            data = {
                "req": {
                    "module": "CDN.SrfCdnDispatchServer",
                    "method": "GetCdnDispatch",
                    "param": {
                        "guid": "1234567890",
                        "calltype": 0,
                        "userip": ""
                    }
                },
                "req_0": {
                    "module": "vkey.GetVkeyServer",
                    "method": "CgiGetVkey",
                    "param": {
                        "guid": "1234567890",
                        "songmid": [song_mid],
                        "songtype": [0],
                        "uin": "0",
                        "loginflag": 1,
                        "platform": "20"
                    }
                }
            }
            params = {
                "data": json.dumps(data)
            }
            headers = {
                **self.headers,
                "Referer": "https://y.qq.com",
            }
            
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    text = await resp.text()
                    result = json.loads(text)
            
            req_0 = result.get("req_0", {})
            if req_0.get("code") == 0:
                midurlinfo = req_0.get("data", {}).get("midurlinfo", [])
                if midurlinfo and midurlinfo[0].get("purl"):
                    sip = req_0.get("data", {}).get("sip", [""])[0]
                    return sip + midurlinfo[0]["purl"]
            
            return f"https://y.qq.com/n/ryqq/songDetail/{song_mid}"
        except Exception as e:
            logger.error(f"获取QQ音乐链接失败: {e}")
            return f"https://y.qq.com/n/ryqq/songDetail/{song_mid}"
    
    async def _get_netease_song_url(self, song_id: str) -> str:
        try:
            url = "https://music.163.com/api/song/enhance/player/url"
            params = {
                "ids": f"[{song_id}]",
                "br": 320000
            }
            headers = {
                **self.headers,
                "Referer": "https://music.163.com",
                "Cookie": "_ntes_nnid=7eced20b9f8d49c22d5da8e2f9ca784b; _ntes_nuid=7eced20b9f8d49c22d5da8e2f9ca784b"
            }
            
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    text = await resp.text()
                    result = json.loads(text)
            
            if result.get("code") == 200:
                data = result.get("data", [])
                if data and data[0].get("url"):
                    return data[0]["url"]
            
            return f"https://music.163.com/song?id={song_id}"
        except Exception as e:
            logger.error(f"获取网易云链接失败: {e}")
            return f"https://music.163.com/song?id={song_id}"


@register("listen_together", "Binbim", "QQ一起听音乐插件 - 创建音乐房间，邀请好友一起听歌", "1.1.0")
class ListenTogetherPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.rooms: dict[str, MusicRoom] = {}
        self.user_room_map: dict[str, str] = {}
        self.music_api = MusicAPI()
        self.search_results: dict[str, list] = {}
    
    async def initialize(self):
        logger.info("一起听音乐插件已加载")
    
    def _get_group_key(self, group_id: str) -> str:
        return f"room_{group_id}"
    
    def _get_user_room(self, user_id: str, group_id: str) -> Optional[MusicRoom]:
        room_key = self._get_group_key(group_id)
        return self.rooms.get(room_key)
    
    def _format_duration(self, seconds: int) -> str:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}:{secs:02d}"
    
    def _format_playlist(self, room: MusicRoom, show_index: bool = True) -> str:
        if not room.playlist:
            return "📋 播放列表为空"
        
        lines = ["📋 播放列表:"]
        for i, song in enumerate(room.playlist):
            prefix = "▶️ " if i == room.current_index else f"{i+1}. "
            duration = self._format_duration(song.duration)
            current = " [正在播放]" if i == room.current_index else ""
            source = "QQ音乐" if song.source == "qq" else "网易云"
            lines.append(f"  {prefix}{song.name} - {song.artist} ({duration}) [{source}]{current}")
        return "\n".join(lines)
    
    @filter.command("创建房间")
    async def create_room(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        user_name = event.get_sender_name() or "未知用户"
        
        room_key = self._get_group_key(group_id)
        if room_key in self.rooms:
            yield event.plain_result("❌ 该群已存在音乐房间，请先关闭现有房间")
            return
        
        room = MusicRoom(
            room_id=room_key,
            owner_id=user_id,
            owner_name=user_name,
            group_id=group_id
        )
        room.add_member(user_id, user_name)
        self.rooms[room_key] = room
        self.user_room_map[f"{user_id}_{group_id}"] = room_key
        
        yield event.plain_result(
            f"🏠 音乐房间创建成功！\n"
            f"房主: {user_name}\n"
            f"使用 /加入房间 加入房间\n"
            f"使用 /点歌 <歌名> 添加歌曲"
        )
    
    @filter.command("加入房间")
    async def join_room(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        user_name = event.get_sender_name() or "未知用户"
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 当前没有可加入的音乐房间，使用 /创建房间 创建一个")
            return
        
        if user_id in room.members:
            yield event.plain_result("你已经在这个房间里了")
            return
        
        room.add_member(user_id, user_name)
        self.user_room_map[f"{user_id}_{group_id}"] = room.room_id
        
        yield event.plain_result(
            f"✅ {user_name} 加入了音乐房间\n"
            f"当前成员: {len(room.members)} 人"
        )
    
    @filter.command("退出房间")
    async def leave_room(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        user_name = event.get_sender_name() or "未知用户"
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 你不在任何音乐房间里")
            return
        
        if user_id == room.owner_id:
            yield event.plain_result("你是房主，请使用 /关闭房间 来关闭房间")
            return
        
        room.remove_member(user_id)
        if f"{user_id}_{group_id}" in self.user_room_map:
            del self.user_room_map[f"{user_id}_{group_id}"]
        
        yield event.plain_result(f"👋 {user_name} 离开了音乐房间")
    
    @filter.command("关闭房间")
    async def close_room(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 当前没有音乐房间")
            return
        
        if user_id != room.owner_id:
            yield event.plain_result("❌ 只有房主才能关闭房间")
            return
        
        room_key = self._get_group_key(group_id)
        for member_id in list(room.members.keys()):
            key = f"{member_id}_{group_id}"
            if key in self.user_room_map:
                del self.user_room_map[key]
        
        del self.rooms[room_key]
        yield event.plain_result("🏠 音乐房间已关闭")
    
    @filter.command("点歌")
    async def search_song(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        message = event.message_str.strip()
        
        keyword = re.sub(r'^[/,，.\s]*点歌\s*', '', message).strip()
        if not keyword:
            yield event.plain_result("请输入歌曲名称，例如: /点歌 稻香")
            return
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 请先加入音乐房间，使用 /加入房间")
            return
        
        yield event.plain_result(f"🔍 正在搜索: {keyword}...")
        
        songs = await self.music_api.search(keyword, limit=5)
        if not songs:
            yield event.plain_result("❌ 未找到相关歌曲，请尝试其他关键词")
            return
        
        search_key = f"{user_id}_{group_id}"
        self.search_results[search_key] = songs
        
        lines = ["搜索结果:"]
        for i, song in enumerate(songs):
            duration = self._format_duration(song.duration)
            source = "QQ音乐" if song.source == "qq" else "网易云"
            lines.append(f"  {i+1}. {song.name} - {song.artist} ({duration}) [{source}]")
        lines.append("\n使用 /选歌 <序号> 添加到播放列表")
        
        yield event.plain_result("\n".join(lines))
    
    @filter.command("选歌")
    async def select_song(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        user_name = event.get_sender_name() or "未知用户"
        message = event.message_str.strip()
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 你不在音乐房间里")
            return
        
        search_key = f"{user_id}_{group_id}"
        if search_key not in self.search_results:
            yield event.plain_result("❌ 请先使用 /点歌 搜索歌曲")
            return
        
        match = re.search(r'(\d+)', message)
        if not match:
            yield event.plain_result("❌ 请输入正确的序号，例如: /选歌 1")
            return
        
        try:
            index = int(match.group(1)) - 1
        except ValueError:
            yield event.plain_result("❌ 请输入正确的序号，例如: /选歌 1")
            return
        
        songs = self.search_results[search_key]
        if index < 0 or index >= len(songs):
            yield event.plain_result("❌ 序号超出范围")
            return
        
        song = songs[index]
        song.url = await self.music_api.get_song_url(song)
        room.add_song(song)
        
        del self.search_results[search_key]
        
        yield event.plain_result(
            f"✅ {user_name} 添加了歌曲\n"
            f"{song.to_display()}\n"
            f"当前播放列表共 {len(room.playlist)} 首歌"
        )
    
    @filter.command("播放列表")
    async def show_playlist(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 你不在音乐房间里")
            return
        
        yield event.plain_result(self._format_playlist(room))
    
    @filter.command("播放")
    async def play(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 你不在音乐房间里")
            return
        
        if not room.playlist:
            yield event.plain_result("❌ 播放列表为空，请先添加歌曲")
            return
        
        if room.is_playing:
            yield event.plain_result("▶️ 音乐正在播放中")
            return
        
        room.is_playing = True
        if room.current_index < 0:
            room.current_index = 0
        
        song = room.get_current_song()
        if song:
            if not song.url:
                song.url = await self.music_api.get_song_url(song)
            
            is_direct = song.url.endswith((".mp3", ".m4a", ".flac", ".ogg"))
            link_type = "🎵 直链播放" if is_direct else "🔗 歌曲链接"
            
            yield event.plain_result(
                f"▶️ 开始播放\n"
                f"{song.to_display()}\n"
                f"时长: {self._format_duration(song.duration)}\n"
                f"{link_type}: {song.url}"
            )
        else:
            yield event.plain_result("❌ 无法获取当前歌曲")
    
    @filter.command("暂停")
    async def pause(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 你不在音乐房间里")
            return
        
        if not room.is_playing:
            yield event.plain_result("⏸️ 当前没有正在播放的音乐")
            return
        
        room.is_playing = False
        yield event.plain_result("⏸️ 音乐已暂停")
    
    @filter.command("下一首")
    async def next_song(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 你不在音乐房间里")
            return
        
        if not room.playlist:
            yield event.plain_result("❌ 播放列表为空")
            return
        
        song = room.next_song()
        if song:
            if not song.url:
                song.url = await self.music_api.get_song_url(song)
            yield event.plain_result(
                f"⏭️ 下一首\n"
                f"{song.to_display()}\n"
                f"🎵 链接: {song.url}"
            )
    
    @filter.command("上一首")
    async def prev_song(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 你不在音乐房间里")
            return
        
        if not room.playlist:
            yield event.plain_result("❌ 播放列表为空")
            return
        
        song = room.prev_song()
        if song:
            if not song.url:
                song.url = await self.music_api.get_song_url(song)
            yield event.plain_result(
                f"⏮️ 上一首\n"
                f"{song.to_display()}\n"
                f"🎵 链接: {song.url}"
            )
    
    @filter.command("切歌")
    async def skip_to(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        message = event.message_str.strip()
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 你不在音乐房间里")
            return
        
        match = re.search(r'(\d+)', message)
        if not match:
            yield event.plain_result("❌ 请输入正确的序号，例如: /切歌 3")
            return
        
        try:
            index = int(match.group(1)) - 1
        except ValueError:
            yield event.plain_result("❌ 请输入正确的序号，例如: /切歌 3")
            return
        
        if index < 0 or index >= len(room.playlist):
            yield event.plain_result("❌ 序号超出范围")
            return
        
        room.current_index = index
        song = room.get_current_song()
        if song:
            if not song.url:
                song.url = await self.music_api.get_song_url(song)
            yield event.plain_result(
                f"🎵 切换到第 {index+1} 首\n"
                f"{song.to_display()}\n"
                f"🎵 链接: {song.url}"
            )
    
    @filter.command("移除")
    async def remove_song(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        message = event.message_str.strip()
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 你不在音乐房间里")
            return
        
        match = re.search(r'(\d+)', message)
        if not match:
            yield event.plain_result("❌ 请输入正确的序号，例如: /移除 2")
            return
        
        try:
            index = int(match.group(1)) - 1
        except ValueError:
            yield event.plain_result("❌ 请输入正确的序号，例如: /移除 2")
            return
        
        song = room.remove_song(index)
        if song:
            if room.current_index >= len(room.playlist):
                room.current_index = max(0, len(room.playlist) - 1)
            yield event.plain_result(f"✅ 已移除: {song.to_display()}")
        else:
            yield event.plain_result("❌ 序号超出范围")
    
    @filter.command("清空列表")
    async def clear_playlist(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 你不在音乐房间里")
            return
        
        if user_id != room.owner_id:
            yield event.plain_result("❌ 只有房主才能清空播放列表")
            return
        
        room.playlist.clear()
        room.current_index = -1
        room.is_playing = False
        yield event.plain_result("✅ 播放列表已清空")
    
    @filter.command("播放模式")
    async def set_play_mode(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        message = event.message_str.strip()
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 你不在音乐房间里")
            return
        
        mode = re.sub(r'^[/,，.\s]*播放模式\s*', '', message).strip()
        if mode in ["顺序", "sequence"]:
            room.play_mode = "sequence"
            yield event.plain_result("🔀 播放模式: 顺序播放")
        elif mode in ["随机", "random"]:
            room.play_mode = "random"
            yield event.plain_result("🔀 播放模式: 随机播放")
        else:
            yield event.plain_result(
                "当前播放模式: " + ("随机播放" if room.play_mode == "random" else "顺序播放") + "\n"
                "使用 /播放模式 顺序 或 /播放模式 随机 切换"
            )
    
    @filter.command("房间信息")
    async def room_info(self, event: AstrMessageEvent):
        group_id = str(event.get_group_id()) if event.get_group_id() else "private"
        user_id = str(event.get_sender_id())
        
        room = self._get_user_room(user_id, group_id)
        if not room:
            yield event.plain_result("❌ 当前没有音乐房间")
            return
        
        members_list = ", ".join(room.members.values()) if room.members else "无"
        status = "播放中" if room.is_playing else "已暂停"
        mode = "随机播放" if room.play_mode == "random" else "顺序播放"
        
        lines = [
            "🏠 房间信息",
            f"房主: {room.owner_name}",
            f"成员: {members_list}",
            f"歌曲数: {len(room.playlist)}",
            f"状态: {status}",
            f"模式: {mode}",
        ]
        
        current = room.get_current_song()
        if current:
            lines.append(f"当前: {current.to_display()}")
        
        yield event.plain_result("\n".join(lines))
    
    @filter.command("听歌帮助")
    async def help_cmd(self, event: AstrMessageEvent):
        help_text = """🎵 一起听音乐 - 帮助

【房间管理】
/创建房间 - 创建音乐房间
/加入房间 - 加入当前房间
/退出房间 - 退出房间
/关闭房间 - 关闭房间(仅房主)
/房间信息 - 查看房间详情

【歌曲操作】
/点歌 <歌名> - 搜索歌曲(QQ音乐/网易云)
/选歌 <序号> - 选择歌曲添加到列表
/播放列表 - 查看当前播放列表
/移除 <序号> - 移除指定歌曲
/清空列表 - 清空播放列表(仅房主)

【播放控制】
/播放 - 开始播放
/暂停 - 暂停播放
/下一首 - 播放下一首
/上一首 - 播放上一首
/切歌 <序号> - 切换到指定歌曲
/播放模式 [顺序/随机] - 设置播放模式

💡 提示: 音乐来源为QQ音乐和网易云音乐"""
        yield event.plain_result(help_text)
    
    async def terminate(self):
        logger.info("一起听音乐插件已卸载")
