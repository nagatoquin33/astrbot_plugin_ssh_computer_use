"""Linux 目标机：bash 执行；GUI 截图/键鼠尽力而为（xdotool / grim / ydotool 等）。

适用于普通 Linux 主机与飞牛 NAS（fnOS）。NAS 通常无桌面，GUI 工具会给出
明确的安装/配置提示。
"""

from __future__ import annotations

import asyncio
import shlex
import uuid

from .base import SshTarget
from .errors import SSHConnectionError
from .models import CommandResult, Screenshot, decode_bytes

GUI_DETECT_SH = r"""
uid=$(id -u)
echo "XDG_RUNTIME_DIR=/run/user/$uid"
dtype=$(loginctl list-sessions --no-legend 2>/dev/null | awk '{print $1}' | while read -r s; do loginctl show-session "$s" -p Type 2>/dev/null; done | grep -ioE 'x11|wayland' | head -1)
echo "SESSION_TYPE=${dtype:-unknown}"
ddisp=$(loginctl list-sessions --no-legend 2>/dev/null | awk '{print $1}' | while read -r s; do loginctl show-session "$s" -p Display 2>/dev/null; done | grep -oE 'Display=[^ ]+' | head -1 | cut -d= -f2)
[ -z "$ddisp" ] && ddisp=$(who 2>/dev/null | grep -oE '\(:[0-9]+\)' | head -1 | tr -d '(): ')
echo "DISPLAY=${ddisp:-:0}"
xa=""
for c in "/run/user/$uid/gdm/Xauthority" "$HOME/.Xauthority" "/run/user/$uid/.Xauthority"; do
  if [ -f "$c" ]; then xa="$c"; break; fi
done
if [ -z "$xa" ]; then
  c=$(ls /run/user/$uid/.mutter-Xwaylandauth* 2>/dev/null | head -1)
  [ -n "$c" ] && xa="$c"
fi
echo "XAUTHORITY=$xa"
b=""
command -v xdotool >/dev/null 2>&1 && b="$b xdotool"
command -v ydotool >/dev/null 2>&1 && b="$b ydotool"
command -v wtype >/dev/null 2>&1 && b="$b wtype"
command -v grim >/dev/null 2>&1 && b="$b grim"
command -v gnome-screenshot >/dev/null 2>&1 && b="$b gnome-screenshot"
command -v scrot >/dev/null 2>&1 && b="$b scrot"
command -v maim >/dev/null 2>&1 && b="$b maim"
command -v import >/dev/null 2>&1 && b="$b import"
command -v spectacle >/dev/null 2>&1 && b="$b spectacle"
echo "BACKENDS=$b"
""".strip()


class LinuxTarget(SshTarget):
    os_type = "linux"

    def __init__(self, profile, cfg: dict):
        super().__init__(profile, cfg)
        self._gui_env_cache: tuple[float, dict] | None = None

    # ---------------- 命令执行 ----------------

    async def exec(self, command: str, timeout: int | None = None) -> CommandResult:
        timeout = timeout or int(self.cfg.get("exec_timeout", 60))
        quoted = "bash -lc " + shlex.quote(command)

        async def _run(conn):
            return await asyncio.wait_for(conn.run(quoted), timeout + 10)

        result = await self._with_retry(_run)
        return CommandResult(
            stdout=decode_bytes(result.stdout or b""),
            stderr=decode_bytes(result.stderr or b""),
            exit_code=result.exit_status if result.exit_status is not None else -1,
        )

    async def sysinfo(self) -> str:
        res = await self.exec("uname -a; head -2 /etc/os-release 2>/dev/null", 15)
        return res.format(1500)

    # ---------------- GUI 环境 ----------------

    async def gui_env(self) -> tuple[str, dict]:
        """返回 (环境变量前缀字符串, 探测信息 dict)，结果缓存 120 秒。"""
        override = (self.cfg.get("linux_gui_env") or "").strip()
        now = asyncio.get_event_loop().time()
        if self._gui_env_cache and now - self._gui_env_cache[0] < 120:
            info = self._gui_env_cache[1]
        else:
            res = await self.exec(GUI_DETECT_SH, 20)
            info = {}
            for line in res.stdout.splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    info[k.strip()] = v.strip()
            self._gui_env_cache = (now, info)

        if override:
            return override, info

        parts = []
        display = info.get("DISPLAY", ":0") or ":0"
        parts.append(f"DISPLAY={display}")
        if info.get("XAUTHORITY"):
            parts.append(f"XAUTHORITY={info['XAUTHORITY']}")
        if info.get("XDG_RUNTIME_DIR"):
            parts.append(f"XDG_RUNTIME_DIR={info['XDG_RUNTIME_DIR']}")
            parts.append(f"DBUS_SESSION_BUS_ADDRESS=unix:path={info['XDG_RUNTIME_DIR']}/bus")
        return " ".join(parts), info

    # ---------------- 截图 ----------------

    async def screenshot(
        self, max_width: int = 0, fmt: str = "jpeg", quality: int = 80
    ) -> Screenshot:
        env, info = await self.gui_env()
        out_path = f"/tmp/sshbot_shot_{uuid.uuid4().hex[:10]}.png"
        custom = (self.cfg.get("linux_screenshot_cmd") or "").strip()
        if custom:
            chain = custom.replace("{}", out_path)
        elif fmt == "jpeg":
            chain = (
                f"(grim -t jpeg -q {quality} {out_path}"
                f" || gnome-screenshot -f {out_path}"
                f" || scrot -o -q {quality} {out_path}"
                f" || maim -q {quality} {out_path}"
                f" || import -window root -quality {quality} {out_path}"
                f" || spectacle -b -n -o {out_path} -f)"
            )
        else:
            chain = (
                f"(grim {out_path}"
                f" || gnome-screenshot -f {out_path}"
                f" || scrot -o {out_path}"
                f" || maim {out_path}"
                f" || import -window root {out_path}"
                f" || spectacle -b -n -o {out_path})"
            )
        res = await self.exec(f"{env} {chain} && [ -s {out_path} ] && echo SHOT_OK", 60)
        try:
            if "SHOT_OK" not in res.stdout:
                raise SSHConnectionError(
                    "Linux 截图失败。请确认已安装截图工具（Wayland: grim / gnome-screenshot；"
                    "X11: scrot / maim / imagemagick），或配置 linux_gui_env / linux_screenshot_cmd。\n"
                    f"探测到的环境：DISPLAY={info.get('DISPLAY', '?')}，"
                    f"可用工具：{info.get('BACKENDS', '无')}\n"
                    f"{res.format(600)}"
                )
            data = await self.read_file(out_path)
            return Screenshot(data=data)
        finally:
            try:
                sftp = await self._get_sftp()
                await sftp.remove(out_path)
            except Exception:
                pass

    # ---------------- 键鼠（尽力而为） ----------------

    async def _input_backend(self, env: str, info: dict) -> str:
        prefer = (self.cfg.get("linux_input_backend") or "auto").strip()
        backends = (info.get("BACKENDS") or "").split()
        if prefer != "auto":
            if prefer not in backends:
                raise SSHConnectionError(f"指定的输入后端 {prefer} 未安装于目标机（可用：{backends}）")
            return prefer
        for b in ("xdotool", "ydotool", "wtype"):
            if b in backends:
                return b
        raise SSHConnectionError(
            "目标机没有可用的键鼠注入工具。X11 请安装 xdotool；Wayland 请安装 ydotool"
            "（需 ydotoold 守护进程）或 wtype。探测到的工具：" + (str(backends) or "无")
        )

    async def gui_click(self, x: int, y: int, button: str = "left", double: bool = False,
                        space: str = "image") -> str:
        env, info = await self.gui_env()
        backend = await self._input_backend(env, info)
        btn_map = {"left": 1, "middle": 2, "right": 3}
        if backend == "xdotool":
            n = btn_map.get(button, 1)
            rep = " --repeat 2 --delay 80" if double else ""
            await self.run_checked(f"{env} xdotool mousemove {int(x)} {int(y)} click{rep} {n}", 20)
        elif backend == "ydotool":
            code = {"left": 0x00, "middle": 0x01, "right": 0x02}[button]
            await self.run_checked(f"{env} ydotool mousemove -a {int(x)} {int(y)}", 20)
            await self.run_checked(f"{env} ydotool click {hex(code)}", 20)
            if double:
                await self.run_checked(f"{env} ydotool click {hex(code)}", 20)
        else:
            raise SSHConnectionError("wtype 不支持鼠标操作（Wayland 限制），请安装 ydotool/xdotool")
        return f"已在 ({x}, {y}) 完成{'双击' if double else '单击'}（{button} 键）"

    async def gui_move(self, x: int, y: int, space: str = "image") -> str:
        env, info = await self.gui_env()
        backend = await self._input_backend(env, info)
        if backend == "xdotool":
            await self.run_checked(f"{env} xdotool mousemove {int(x)} {int(y)}", 20)
        elif backend == "ydotool":
            await self.run_checked(f"{env} ydotool mousemove -a {int(x)} {int(y)}", 20)
        else:
            raise SSHConnectionError("wtype 不支持鼠标操作")
        return f"鼠标已移动到 ({x}, {y})"

    async def gui_scroll(self, amount: int) -> str:
        env, info = await self.gui_env()
        backend = await self._input_backend(env, info)
        n = abs(int(amount)) or 1
        if backend == "xdotool":
            btn = 4 if amount >= 0 else 5
            await self.run_checked(f"{env} xdotool click --repeat {n} --delay 60 {btn}", 30)
        elif backend == "ydotool":
            btn = 4 if amount >= 0 else 5
            await self.run_checked(f"{env} ydotool click {btn}", 30)
        else:
            raise SSHConnectionError("wtype 不支持滚动")
        return f"已滚动 {amount} 齿（正向上/负向下）"

    async def gui_type(self, text: str) -> str:
        env, info = await self.gui_env()
        backend = await self._input_backend(env, info)
        if backend == "xdotool":
            await self.run_checked(f"{env} xdotool type --delay 25 -- {shlex.quote(text)}", 60)
        elif backend == "ydotool":
            await self.run_checked(f"{env} ydotool type -- {shlex.quote(text)}", 60)
        else:
            await self.run_checked(f"{env} wtype -- {shlex.quote(text)}", 60)
        return f"已输入 {len(text)} 个字符"

    async def gui_key(self, keys: str) -> str:
        env, info = await self.gui_env()
        backend = await self._input_backend(env, info)
        combo = keys.strip()
        if backend == "xdotool":
            await self.run_checked(f"{env} xdotool key -- {shlex.quote(combo)}", 30)
        elif backend == "ydotool":
            await self.run_checked(f"{env} ydotool key {shlex.quote(combo)}", 30)
        else:
            await self.run_checked(f"{env} wtype -P {shlex.quote(combo)} -R {shlex.quote(combo)}", 30)
        return f"已按下 {keys}"

    async def gui_start(self, command: str) -> str:
        env, _ = await self.gui_env()
        await self.run_checked(f"{env} nohup {command} >/dev/null 2>&1 & sleep 0.5", 15)
        return f"已启动：{command}"

    async def clipboard_get(self) -> str:
        env, _ = await self.gui_env()
        res = await self.exec(
            f"{env} (xclip -selection clipboard -o 2>/dev/null || xsel --clipboard --output 2>/dev/null"
            f" || wl-paste 2>/dev/null) || true",
            20,
        )
        if not res.stdout.strip():
            raise SSHConnectionError("读取剪贴板失败：请安装 xclip/xsel（X11）或 wl-clipboard（Wayland）")
        return res.stdout

    async def clipboard_set(self, text: str) -> str:
        env, _ = await self.gui_env()
        res = await self.exec(
            f"{env} (printf %s {shlex.quote(text)} | xclip -selection clipboard -i 2>/dev/null"
            f" || printf %s {shlex.quote(text)} | xsel --clipboard --input 2>/dev/null"
            f" || printf %s {shlex.quote(text)} | wl-copy 2>/dev/null)",
            20,
        )
        if not res.ok:
            raise SSHConnectionError("写入剪贴板失败：请安装 xclip/xsel（X11）或 wl-clipboard（Wayland）")
        return "已写入剪贴板"
