"""Phase 0 — Smoke test: dependency & API Speechmatics tồn tại đúng như giả định.

Mục tiêu: đảm bảo môi trường cài đặt đủ và các field diarization của Speechmatics
mà plan dựa vào THỰC SỰ tồn tại (không giả định API).
"""


def test_import_speechmatics_and_frames():
    # Import được các thành phần cốt lõi của màn hình /minutes
    from pipecat.services.speechmatics.stt import SpeechmaticsSTTService  # noqa: F401
    from pipecat.frames.frames import (  # noqa: F401
        TranscriptionFrame,
        InterimTranscriptionFrame,
    )
    from pipecat.processors.frame_processor import (  # noqa: F401
        FrameProcessor,
        FrameDirection,
    )
    from pipecat.transcriptions.language import Language

    # Tiếng Việt phải map đúng "vi"
    assert Language.VI.value == "vi"


def test_anthropic_available():
    import anthropic  # noqa: F401


def test_speechmatics_inputparams_has_diarization_fields():
    """Các field plan dựa vào phải tồn tại trong InputParams."""
    from pipecat.services.speechmatics.stt import SpeechmaticsSTTService

    fields = SpeechmaticsSTTService.InputParams.model_fields
    required = [
        "language",
        "enable_diarization",
        "speaker_sensitivity",
        "max_speakers",
        "speaker_active_format",
        "speaker_passive_format",
        "focus_speakers",
        "operating_point",
    ]
    missing = [f for f in required if f not in fields]
    assert not missing, f"Speechmatics InputParams thiếu field: {missing}"


def test_transcription_frame_has_user_id():
    """user_id là nơi speaker label (S1/S2) được gán -> bắt buộc tồn tại."""
    import dataclasses
    from pipecat.frames.frames import TranscriptionFrame

    field_names = {f.name for f in dataclasses.fields(TranscriptionFrame)}
    assert "user_id" in field_names
    assert "timestamp" in field_names
