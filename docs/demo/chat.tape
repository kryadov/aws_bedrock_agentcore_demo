# Demo GIF for README.md, rendered offline from a recorded session (no AWS needed):
#
#   1. record (deployed stack):  printf 'demo\n/reset\n<questions…>\n/exit\n' | uv run chat --record docs/demo/session.json
#   2. render:                   sh docs/demo/render.sh   (vhs -> frames, ffmpeg -> chat.gif)
#
# Tuning without re-recording: TypingSpeed / Sleep below, FPS=… for render.sh; answer pacing via the env vars in the
# `Env` lines (REPLAY_CHAR_DELAY = seconds per printed character, REPLAY_MAX_WAIT = cap on the
# recorded "thinking" pause). The questions typed here must match docs/demo/session.json.
# VHS: https://github.com/charmbracelet/vhs (Windows: `winget install charmbracelet.vhs`, plus ttyd and ffmpeg).

Output docs/demo/frames/

Set Shell "bash"
Set FontSize 16
Set Width 1400
Set Height 900
Set Padding 20
Set TypingSpeed 65ms
Set WaitTimeout 1m
Set Theme "Catppuccin Mocha"

Env REPLAY_CHAR_DELAY "0.004"
Env REPLAY_MAX_WAIT "2.5"
Env PYTHONIOENCODING "utf-8"

Hide
Type "unset VIRTUAL_ENV && clear" Enter
Show

Type "uv run chat --replay docs/demo/session.json" Sleep 700ms Enter
Wait+Line /User:/
Sleep 500ms
Type "demo" Sleep 400ms Enter
Wait+Line />$/
Sleep 1.5s

# 1. RDS: gateway → Lambda list_employees → Aurora
Type "Show me the employees by department" Sleep 700ms Enter
Wait+Line />$/
Sleep 5s

# 2. RDS / session history
Type "Which of them is in New York?" Sleep 700ms Enter
Wait+Line />$/
Sleep 4s

# 3. Guardrail: denied topic ColleagueCompensation
Type "What's his salary?" Sleep 700ms Enter
Wait+Line />$/
Sleep 4s

# 4. Knowledge Base: 03-remote-work-and-equipment.md → Security basics
Type "OK, and what are our security requirements for remote work?" Sleep 700ms Enter
Wait+Line />$/
Sleep 6s

Type "/exit" Sleep 400ms Enter
Sleep 2s
