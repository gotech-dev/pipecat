#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Transport params cho màn hình /minutes (chỉ nhận audio để transcribe).

Tách riêng khỏi bot.py để minutes_bot_entry không phải import nguyên màn hình cũ
(Gladia). Chỉ cần VAD để chia câu; KHÔNG dùng smart-turn vì đây là phiên ghi/
transcribe một chiều (không hội thoại 2 chiều, không cần phát hiện lượt nói).
"""

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

minutes_transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=False,  # màn hình biên bản không cần phát audio ra
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
    ),
    "twilio": lambda: FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=False,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
    ),
}

# Thêm Daily nếu khả dụng (giống bot.py)
try:
    from pipecat.transports.daily.transport import DailyParams

    minutes_transport_params["daily"] = lambda: DailyParams(
        audio_in_enabled=True,
        audio_out_enabled=False,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
    )
except ImportError:
    pass
