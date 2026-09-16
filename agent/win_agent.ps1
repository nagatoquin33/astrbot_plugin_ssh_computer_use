# =============================================================================
# AstrBot SSH Computer Use - Windows GUI Agent v1
#
# 运行在 Windows 目标机的交互式用户会话中（由计划任务 AstrBotSshAgent 启动），
# 监听 127.0.0.1:<Port>，bot 通过 SSH 端口转发连接本端口。
#
# 协议：每个 TCP 连接发送一行 UTF-8 JSON 请求，Agent 回一行 UTF-8 JSON 应答。
#   请求: {"id": 1, "op": "screenshot", "max_width": 1280, "format": "jpeg", "quality": 80}
#   应答: {"id": 1, "ok": true, "data": {...}} 或 {"id": 1, "ok": false, "err": "..."}
#
# 支持 op:
#   ping / info / screenshot / click / move / scroll / type / key
#   exec（在用户会话内执行命令，可启动 GUI 程序）/ start（启动程序不等待）
#   clipboard_get / clipboard_set
#
# 兼容 Windows PowerShell 5.1（系统自带，无需安装任何依赖）。
# =============================================================================

param(
    [int]$Port = 7622
)

$ErrorActionPreference = 'Stop'
$AgentVersion = 2

$LogDir = Join-Path $env:USERPROFILE '.sshbot'
$LogFile = Join-Path $LogDir 'agent.log'

function Write-Log {
    param([string]$Message)
    try {
        if (-not (Test-Path $LogDir)) {
            New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
        }
        $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
        Add-Content -Path $LogFile -Value $line -Encoding UTF8
    } catch {}
}

# 单实例检查：端口被占用说明已有 Agent 在跑，直接退出
$listener = New-Object System.Net.Sockets.TcpListener ([System.Net.IPAddress]::Loopback), $Port
try {
    $listener.Start()
} catch {
    Write-Log "端口 $Port 已被占用（Agent 可能已在运行），退出。$_"
    exit 0
}

try {
    Add-Type -AssemblyName System.Windows.Forms | Out-Null
    Add-Type -AssemblyName System.Drawing | Out-Null
} catch {
    Write-Log "加载 WinForms/Drawing 程序集失败: $_"
    exit 1
}

# --- Win32 API（SendInput / SetCursorPos / VkKeyScanW）---
if (-not ('SshBotNative' -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class SshBotNative
{
    [StructLayout(LayoutKind.Sequential)]
    public struct MOUSEINPUT
    {
        public int dx;
        public int dy;
        public uint mouseData;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT
    {
        public ushort wVk;
        public ushort wScan;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Explicit)]
    public struct INPUTUNION
    {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT
    {
        public uint type;
        public INPUTUNION U;
    }

    public const uint INPUT_MOUSE = 0;
    public const uint INPUT_KEYBOARD = 1;
    public const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP = 0x0004;
    public const uint MOUSEEVENTF_RIGHTDOWN = 0x0008;
    public const uint MOUSEEVENTF_RIGHTUP = 0x0010;
    public const uint MOUSEEVENTF_MIDDLEDOWN = 0x0020;
    public const uint MOUSEEVENTF_MIDDLEUP = 0x0040;
    public const uint MOUSEEVENTF_WHEEL = 0x0800;
    public const uint KEYEVENTF_EXTENDEDKEY = 0x0001;
    public const uint KEYEVENTF_KEYUP = 0x0002;
    public const uint KEYEVENTF_UNICODE = 0x0004;

    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern short VkKeyScanW(char ch);

    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();
}
"@
}

# DPI 感知：保证多比例缩放屏上坐标与截图像素一一对应
try { [void][SshBotNative]::SetProcessDPIAware() } catch {}

$InputSize = [System.Runtime.InteropServices.Marshal]::SizeOf([type][SshBotNative+INPUT])

function New-InputArray {
    param([int]$Count)
    return New-Object 'SshBotNative+INPUT[]' $Count
}

function Send-Inputs {
    param([SshBotNative+INPUT[]]$Inputs)
    [void][SshBotNative]::SendInput([uint32]$Inputs.Length, $Inputs, $InputSize)
}

function Send-MouseFlag {
    param([uint32]$Flag)
    $inp = New-Object 'SshBotNative+INPUT'
    $inp.type = [SshBotNative]::INPUT_MOUSE
    $inp.U.mi.dwFlags = $Flag
    $arr = New-InputArray 1
    $arr[0] = $inp
    Send-Inputs $arr
}

function Send-MouseWheel {
    param([int]$Delta)
    $inp = New-Object 'SshBotNative+INPUT'
    $inp.type = [SshBotNative]::INPUT_MOUSE
    $inp.U.mi.dwFlags = [SshBotNative]::MOUSEEVENTF_WHEEL
    $inp.U.mi.mouseData = [uint32]$Delta
    $arr = New-InputArray 1
    $arr[0] = $inp
    Send-Inputs $arr
}

function Send-VK {
    param([ushort]$Vk, [bool]$Up = $false, [bool]$Extended = $false)
    $inp = New-Object 'SshBotNative+INPUT'
    $inp.type = [SshBotNative]::INPUT_KEYBOARD
    $inp.U.ki.wVk = $Vk
    $flags = [uint32]0
    if ($Extended) { $flags = $flags -bor [SshBotNative]::KEYEVENTF_EXTENDEDKEY }
    if ($Up) { $flags = $flags -bor [SshBotNative]::KEYEVENTF_KEYUP }
    $inp.U.ki.dwFlags = $flags
    $arr = New-InputArray 1
    $arr[0] = $inp
    Send-Inputs $arr
}

# 命名键 -> VK 码
$VkMap = @{
    'enter' = 0x0D; 'return' = 0x0D
    'esc' = 0x1B; 'escape' = 0x1B
    'tab' = 0x09; 'space' = 0x20
    'backspace' = 0x08; 'bs' = 0x08
    'delete' = 0x2E; 'del' = 0x2E
    'insert' = 0x2D; 'ins' = 0x2D
    'home' = 0x24; 'end' = 0x23
    'pageup' = 0x21; 'pgup' = 0x21
    'pagedown' = 0x22; 'pgdn' = 0x22
    'up' = 0x26; 'down' = 0x28; 'left' = 0x25; 'right' = 0x27
    'capslock' = 0x14; 'caps' = 0x14
    'printscreen' = 0x2C; 'prtsc' = 0x2C
    'win' = 0x5B; 'lwin' = 0x5B; 'rwin' = 0x5C
    'menu' = 0x5D; 'apps' = 0x5D
    'ctrl' = 0x11; 'control' = 0x11; 'lctrl' = 0xA2; 'rctrl' = 0xA3
    'alt' = 0x12; 'lalt' = 0xA4; 'ralt' = 0xA5; 'altgr' = 0xA5
    'shift' = 0x10; 'lshift' = 0xA0; 'rshift' = 0xA1
    'volumeup' = 0xAF; 'volumedown' = 0xAE; 'volumemute' = 0xAD
    'mediaplay' = 0xFA; 'mediapause' = 0xFA; 'medianext' = 0xB0; 'mediaprev' = 0xB1; 'mediastop' = 0xB2
    'browserback' = 0xA6; 'browserforward' = 0xA7; 'browserrefresh' = 0xA8; 'browserhome' = 0xAB
}
for ($i = 1; $i -le 24; $i++) { $VkMap[('f' + $i)] = (0x6F + $i) }

# 需要扩展键标志的键
$ExtendedKeys = @('insert','ins','delete','del','home','end','pageup','pgup','pagedown','pgdn',
    'up','down','left','right','printscreen','prtsc','rctrl','ralt','rwin','menu','apps')

# 修饰键名集合
$ModifierKeys = @('ctrl','control','lctrl','rctrl','alt','lalt','ralt','altgr','shift','lshift','rshift','win','lwin','rwin')

function Resolve-Key {
    # 返回 @('vk' -> ushort, 'extended' -> bool, 'shift' -> bool) 或 $null
    param([string]$Name)
    $k = $Name.Trim().ToLower()
    if ($k.Length -eq 0) { return $null }
    if ($VkMap.ContainsKey($k)) {
        return @{ vk = [ushort]$VkMap[$k]; extended = ($ExtendedKeys -contains $k); shift = $false }
    }
    if ($k.Length -eq 1) {
        $scan = [SshBotNative]::VkKeyScanW($k[0])
        if ($scan -ne -1) {
            $vk = [ushort]($scan -band 0xFF)
            $state = (($scan -band 0xFF00) -shr 8)
            return @{ vk = $vk; extended = $false; shift = (($state -band 1) -ne 0) }
        }
        # 找不到键盘映射时退化为 UNICODE 扫描码
        return @{ unicode = [ushort][int]($k[0]) }
    }
    return $null
}

function Invoke-KeyPress {
    param([string]$Combo)
    $parts = $Combo -split '\+' | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ }
    if (-not $parts) { throw "空按键组合" }

    $mods = @()
    $main = $null
    foreach ($p in $parts) {
        if ($ModifierKeys -contains $p) { $mods += $p }
        elseif ($null -eq $main) { $main = $p }
        else { throw "无法识别按键组合 '$Combo'：出现多个主键" }
    }
    if ($null -eq $main) { throw "按键组合 '$Combo' 缺少主键" }

    $mainResolved = Resolve-Key $main
    if ($null -eq $mainResolved) { throw "无法识别按键: $main" }

    foreach ($m in $mods) { Send-VK ([ushort]$VkMap[$m]) $false $false }

    if ($mainResolved.ContainsKey('unicode')) {
        $inp = New-Object 'SshBotNative+INPUT'
        $inp.type = [SshBotNative]::INPUT_KEYBOARD
        $inp.U.ki.wScan = [ushort]$mainResolved['unicode']
        $inp.U.ki.dwFlags = [SshBotNative]::KEYEVENTF_UNICODE
        $arr = New-InputArray 1
        $arr[0] = $inp
        Send-Inputs $arr
        $inp.U.ki.dwFlags = [SshBotNative]::KEYEVENTF_UNICODE -bor [SshBotNative]::KEYEVENTF_KEYUP
        $arr[0] = $inp
        Send-Inputs $arr
    } else {
        if ($mainResolved['shift']) { Send-VK 0x10 $false $false }
        Send-VK ([ushort]$mainResolved['vk']) $false ([bool]$mainResolved['extended'])
        Send-VK ([ushort]$mainResolved['vk']) $true ([bool]$mainResolved['extended'])
        if ($mainResolved['shift']) { Send-VK 0x10 $true $false }
    }

    [array]::Reverse($mods)
    foreach ($m in $mods) { Send-VK ([ushort]$VkMap[$m]) $true $false }
}

function Send-Text {
    param([string]$Text)
    foreach ($ch in $Text.ToCharArray()) {
        if ($ch -eq "`r") { continue }
        if ($ch -eq "`n") {
            Send-VK 0x0D $false $false
            Send-VK 0x0D $true $false
            continue
        }
        $down = New-Object 'SshBotNative+INPUT'
        $down.type = [SshBotNative]::INPUT_KEYBOARD
        $down.U.ki.wScan = [ushort][int]$ch
        $down.U.ki.dwFlags = [SshBotNative]::KEYEVENTF_UNICODE

        $up = New-Object 'SshBotNative+INPUT'
        $up.type = [SshBotNative]::INPUT_KEYBOARD
        $up.U.ki.wScan = [ushort][int]$ch
        $up.U.ki.dwFlags = [SshBotNative]::KEYEVENTF_UNICODE -bor [SshBotNative]::KEYEVENTF_KEYUP

        $arr = New-InputArray 2
        $arr[0] = $down
        $arr[1] = $up
        Send-Inputs $arr
    }
}

function Get-VirtualScreen {
    return [System.Windows.Forms.SystemInformation]::VirtualScreen
}

function Invoke-Screenshot {
    param([int]$MaxWidth, [string]$Format, [int]$Quality)
    $vs = Get-VirtualScreen
    $bmp = New-Object System.Drawing.Bitmap $vs.Width, $vs.Height
    try {
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($vs.X, $vs.Y, 0, 0, (New-Object System.Drawing.Size $vs.Width, $vs.Height))
        $g.Dispose()

        $out = $bmp
        try {
            if ($MaxWidth -gt 0 -and $out.Width -gt $MaxWidth) {
                $newH = [int]([double]$out.Height * $MaxWidth / $out.Width)
                if ($newH -lt 1) { $newH = 1 }
                $scaled = New-Object System.Drawing.Bitmap $MaxWidth, $newH
                $g2 = [System.Drawing.Graphics]::FromImage($scaled)
                $g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                $g2.DrawImage($out, 0, 0, $MaxWidth, $newH)
                $g2.Dispose()
                if (-not [object]::ReferenceEquals($out, $bmp)) { $out.Dispose() }
                $out = $scaled
            }

            $ms = New-Object System.IO.MemoryStream
            try {
                if ($Format -eq 'png') {
                    $out.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
                } else {
                    $jpegEncoder = $null
                    foreach ($enc in [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders()) {
                        if ($enc.MimeType -eq 'image/jpeg') { $jpegEncoder = $enc; break }
                    }
                    if ($null -eq $jpegEncoder) { throw "找不到 JPEG 编码器" }
                    $ep = New-Object System.Drawing.Imaging.EncoderParameters 1
                    $ep.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality), ([long]$Quality)
                    $out.Save($ms, $jpegEncoder, $ep)
                }
                return @{
                    b64 = [Convert]::ToBase64String($ms.ToArray())
                    w = $out.Width
                    h = $out.Height
                    sx = $vs.X; sy = $vs.Y; sw = $vs.Width; sh = $vs.Height
                }
            } finally { $ms.Dispose() }
        } finally {
            if (-not [object]::ReferenceEquals($out, $bmp)) { $out.Dispose() }
        }
    } finally { $bmp.Dispose() }
}

function Invoke-ShellCommand {
    # 在用户会话内执行命令：wrapper.cmd 负责转码与输出重定向，body.cmd 为原始命令
    param([string]$Command, [int]$TimeoutSec = 30)
    $id = [guid]::NewGuid().ToString('N')
    $bodyF = Join-Path $env:TEMP ("sshbot_body_{0}.cmd" -f $id)
    $wrapF = Join-Path $env:TEMP ("sshbot_wrap_{0}.cmd" -f $id)
    $outF = Join-Path $env:TEMP ("sshbot_out_{0}.txt" -f $id)
    $errF = Join-Path $env:TEMP ("sshbot_err_{0}.txt" -f $id)

    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($bodyF, $Command + "`r`n", $utf8)
    $wrap = "@echo off`r`nchcp 65001 >nul`r`ncall `"$bodyF`" > `"$outF`" 2> `"$errF`"`r`nexit /b %errorlevel%`r`n"
    [System.IO.File]::WriteAllText($wrapF, $wrap, $utf8)

    $timedOut = $false
    try {
        $p = Start-Process -FilePath $env:ComSpec -ArgumentList ("/d /s /c `"" + $wrapF + "`"") -PassThru -WindowStyle Hidden
        if (-not $p.WaitForExit($TimeoutSec * 1000)) {
            $timedOut = $true
            try { $p.Kill() } catch {}
        }
        $code = 0
        try { $code = $p.ExitCode } catch {}
        $stdout = ''
        $stderr = ''
        if (Test-Path $outF) { $stdout = [System.IO.File]::ReadAllText($outF, [System.Text.Encoding]::UTF8) }
        if (Test-Path $errF) { $stderr = [System.IO.File]::ReadAllText($errF, [System.Text.Encoding]::UTF8) }
        return @{ stdout = $stdout; stderr = $stderr; code = $code; timed_out = $timedOut }
    } finally {
        foreach ($f in @($bodyF, $wrapF, $outF, $errF)) {
            try { Remove-Item -Path $f -Force -ErrorAction SilentlyContinue | Out-Null } catch {}
        }
    }
}

function Invoke-StartApp {
    param([string]$Command)
    # fire-and-forget：start "" <command>
    $argStr = '/d /s /c start "" ' + $Command
    Start-Process -FilePath $env:ComSpec -ArgumentList $argStr -WindowStyle Hidden | Out-Null
    return @{ started = $true }
}

function Get-ClipboardTextSafe {
    try { return [System.Windows.Forms.Clipboard]::GetText() } catch {}
    try { return (Get-Clipboard -Raw) } catch {}
    throw "读取剪贴板失败（可能无交互式会话）"
}

function Set-ClipboardTextSafe {
    param([string]$Text)
    try { [System.Windows.Forms.Clipboard]::SetText($Text); return } catch {}
    try { Set-Clipboard -Value $Text; return } catch {}
    throw "写入剪贴板失败（可能无交互式会话）"
}

function Get-Info {
    $vs = Get-VirtualScreen
    $primary = [System.Windows.Forms.Screen]::PrimaryScreen
    return @{
        version = $AgentVersion
        machine = $env:COMPUTERNAME
        user = $env:USERNAME
        ps_version = $PSVersionTable.PSVersion.ToString()
        screen = @{ x = $vs.X; y = $vs.Y; w = $vs.Width; h = $vs.Height }
        primary = @{ x = $primary.Bounds.X; y = $primary.Bounds.Y; w = $primary.Bounds.Width; h = $primary.Bounds.Height }
    }
}

function Invoke-Op {
    param($Req)
    $op = [string]$Req.op
    $data = $null

    # LLM 直连协议时的常见自创 op 名 → 规范化，避免误用直接失败
    if ($op -eq 'double_click') { $op = 'click'; $Req | Add-Member -NotePropertyName double -NotePropertyValue $true -Force }
    elseif ($op -eq 'single_click' -or $op -eq 'left_click') { $op = 'click' }
    elseif ($op -eq 'key_press' -or $op -eq 'press_key') { $op = 'key' }

    switch ($op) {
        'ping' { $data = @{ pong = $true; version = $AgentVersion } }
        'info' { $data = Get-Info }
        'screenshot' {
            $maxW = 1280
            if ($null -ne $Req.max_width) { $maxW = [int]$Req.max_width }
            $fmt = 'jpeg'
            if ($null -ne $Req.format) { $fmt = [string]$Req.format }
            $q = 80
            if ($null -ne $Req.quality) { $q = [int]$Req.quality }
            $data = Invoke-Screenshot -MaxWidth $maxW -Format $fmt -Quality $q
        }
        'click' {
            $x = [int]$Req.x
            $y = [int]$Req.y
            $button = 'left'
            if ($null -ne $Req.button) { $button = ([string]$Req.button).ToLower() }
            $dbl = $false
            if ($null -ne $Req.double) { $dbl = [bool]$Req.double }

            [void][SshBotNative]::SetCursorPos($x, $y)
            Start-Sleep -Milliseconds 30
            $downF = [SshBotNative]::MOUSEEVENTF_LEFTDOWN
            $upF = [SshBotNative]::MOUSEEVENTF_LEFTUP
            if ($button -eq 'right') { $downF = [SshBotNative]::MOUSEEVENTF_RIGHTDOWN; $upF = [SshBotNative]::MOUSEEVENTF_RIGHTUP }
            elseif ($button -eq 'middle') { $downF = [SshBotNative]::MOUSEEVENTF_MIDDLEDOWN; $upF = [SshBotNative]::MOUSEEVENTF_MIDDLEUP }

            if ($dbl) {
                for ($i = 0; $i -lt 2; $i++) {
                    Send-MouseFlag $downF
                    Start-Sleep -Milliseconds 40
                    Send-MouseFlag $upF
                    if ($i -eq 0) { Start-Sleep -Milliseconds 120 }
                }
            } else {
                Send-MouseFlag $downF
                Start-Sleep -Milliseconds 40
                Send-MouseFlag $upF
            }
            $data = @{ x = $x; y = $y; button = $button; double = $dbl }
        }
        'move' {
            [void][SshBotNative]::SetCursorPos([int]$Req.x, [int]$Req.y)
            $data = @{ x = [int]$Req.x; y = [int]$Req.y }
        }
        'scroll' {
            $amount = [int]$Req.amount
            # 正数向上滚，负数向下滚；单位：滚轮齿格
            Send-MouseWheel ($amount * 120)
            $data = @{ amount = $amount }
        }
        'type' {
            Send-Text ([string]$Req.text)
            $data = @{ typed = ([string]$Req.text).Length }
        }
        'key' {
            Invoke-KeyPress ([string]$Req.keys)
            $data = @{ keys = [string]$Req.keys }
        }
        'exec' {
            $timeout = 30
            if ($null -ne $Req.timeout_sec) { $timeout = [int]$Req.timeout_sec }
            $data = Invoke-ShellCommand -Command ([string]$Req.command) -TimeoutSec $timeout
        }
        'start' {
            $data = Invoke-StartApp -Command ([string]$Req.command)
        }
        'clipboard_get' { $data = @{ text = (Get-ClipboardTextSafe) } }
        'clipboard_set' {
            Set-ClipboardTextSafe ([string]$Req.text)
            $data = @{ ok = $true }
        }
        default { throw "未知 op: $op" }
    }
    return $data
}

function Handle-Client {
    param([System.Net.Sockets.TcpClient]$Client)
    $stream = $Client.GetStream()
    try {
        $utf8Strict = New-Object System.Text.UTF8Encoding($false, $true)
        $reader = New-Object System.IO.StreamReader ($stream), ($utf8Strict)
        $line = $reader.ReadLine()
        if (-not $line) { return }

        $reqId = 0
        try {
            $req = ConvertFrom-Json $line
            if ($null -ne $req.id) { $reqId = [int]$req.id }
            $data = Invoke-Op -Req $req
            $resp = @{ id = $reqId; ok = $true; data = $data }
        } catch {
            $resp = @{ id = $reqId; ok = $false; err = ($_ | Out-String).Trim() }
            Write-Log "op 处理失败: $_"
        }

        $json = ConvertTo-Json -InputObject $resp -Depth 6 -Compress
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($json + "`n")
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush()
    } finally {
        try { $Client.Close() } catch {}
    }
}

Write-Log "Agent v$AgentVersion 启动，监听 127.0.0.1:$Port（PID=$PID）"

# 主循环：串行处理连接（bot 单客户端，截图类操作本就需串行）
while ($true) {
    try {
        $client = $listener.AcceptTcpClient()
        Handle-Client -Client $client
    } catch [System.Management.Automation.PipelineStoppedException] {
        throw
    } catch {
        Write-Log "连接处理异常: $_"
        Start-Sleep -Milliseconds 200
    }
}
