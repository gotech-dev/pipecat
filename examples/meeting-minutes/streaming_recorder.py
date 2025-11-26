#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Streaming audio recorder that writes directly to file instead of buffering in memory.

This processor writes audio frames directly to a WAV file, preventing memory
overflow for long recordings (30 minutes to 1.5 hours).
"""

import os
import wave
from typing import Optional

from loguru import logger

from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class StreamingAudioRecorder(FrameProcessor):
    """Records audio directly to WAV file without buffering in memory.

    This processor is designed for long recordings (30+ minutes) where
    buffering all audio in memory would cause memory overflow.

    Example::

        recorder = StreamingAudioRecorder(output_dir="recordings")
        await recorder.start_recording("meeting_20240101.wav")

        # Audio frames are written directly to file as they arrive
        # No memory buffering occurs

        await recorder.stop_recording()
    """

    def __init__(
        self,
        *,
        output_dir: str = "recordings",
        sample_rate: Optional[int] = None,
        **kwargs,
    ):
        """Initialize the streaming audio recorder.

        Args:
            output_dir: Directory where recordings will be saved.
            sample_rate: Target sample rate for recording. If None, uses source rate.
            **kwargs: Additional arguments passed to parent class.
        """
        super().__init__(**kwargs)
        self._output_dir = output_dir
        self._init_sample_rate = sample_rate
        self._sample_rate = 0
        self._wave_file: Optional[wave.Wave_write] = None
        self._recording = False
        self._output_filepath: Optional[str] = None
        self._input_resampler = create_stream_resampler()

        # Ensure output directory exists
        os.makedirs(self._output_dir, exist_ok=True)

    @property
    def is_recording(self) -> bool:
        """Check if currently recording.

        Returns:
            True if recording is active.
        """
        return self._recording

    @property
    def output_filepath(self) -> Optional[str]:
        """Get the current output file path.

        Returns:
            Path to the current recording file, or None if not recording.
        """
        return self._output_filepath

    async def start_recording(self, filename: str):
        """Start recording audio to a WAV file.

        Args:
            filename: Name of the output file (will be saved in output_dir).
        """
        if self._recording:
            logger.warning("Already recording, stopping previous recording first")
            await self.stop_recording()

        self._output_filepath = os.path.join(self._output_dir, filename)
        self._wave_file = wave.open(self._output_filepath, "wb")
        self._wave_file.setsampwidth(2)  # 16-bit audio
        self._wave_file.setnchannels(1)  # Mono
        self._wave_file.setframerate(self._sample_rate or 16000)
        self._recording = True
        logger.info(f"Started recording to {self._output_filepath}")

    async def stop_recording(self):
        """Stop recording and close the WAV file."""
        if not self._recording:
            return

        if self._wave_file:
            self._wave_file.close()
            self._wave_file = None

        self._recording = False
        logger.info(f"Stopped recording: {self._output_filepath}")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process audio frames and write to file.

        Args:
            frame: The frame to process.
            direction: The direction of frame flow in the pipeline.
        """
        await super().process_frame(frame, direction)

        # Update sample rate from start frame
        if isinstance(frame, StartFrame):
            self._sample_rate = self._init_sample_rate or frame.audio_in_sample_rate
            if self._wave_file:
                self._wave_file.setframerate(self._sample_rate)

        # Write audio frames directly to file
        if self._recording and isinstance(frame, InputAudioRawFrame):
            if self._wave_file:
                # Resample if needed
                if frame.sample_rate != self._sample_rate:
                    resampled = await self._input_resampler.resample(
                        frame.audio, frame.sample_rate, self._sample_rate
                    )
                    self._wave_file.writeframes(resampled)
                else:
                    self._wave_file.writeframes(frame.audio)

        # Stop recording on end/cancel
        if isinstance(frame, (CancelFrame, EndFrame)):
            await self.stop_recording()

        await self.push_frame(frame, direction)

