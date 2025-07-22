# client/audio_engine.py

import asyncio
import threading
import logging
import numpy as np
import opuslib
import sounddevice as sd
import samplerate
import logging
import time
from PySide6.QtCore import QMetaObject, Qt, Slot  # near other QtCore imports
from PySide6 import QtCore  # Added for Qt signal support
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

###############################################################################
# ─── Audio Handling (Encoder / Decoder / I/O) ──────────────────────────────
###############################################################################

"""
Audio handling module:
• Captures microphone audio
• Encodes with Opus codec for efficient bandwidth usage
• Decodes incoming Opus audio for playback
• Supports Push-To-Talk (PTT) and Voice Activation (VOX)
• Uses sounddevice for real-time low-latency audio I/O
• Runs audio I/O on a dedicated thread to keep GUI responsive
"""

class AudioEngine(QtCore.QObject):
    """
    Handles audio capture and playback with Opus encoding/decoding.
    Designed for low latency, privacy-focused, efficient transmission.
    """

    # Qt signals to emit changes for GUI/UI to track in real-time
    micStatusChanged = QtCore.Signal(bool)     # True = muted
    spkStatusChanged = QtCore.Signal(bool)     # True = muted
    voxActivity = QtCore.Signal(bool)          # True = voice active
    inputLevel = QtCore.Signal(float)          # Mic level (RMS)
    outputLevel = QtCore.Signal(float)  # Speaker output level (RMS)
    incomingAudio = QtCore.Signal(str)  # new signal for queuing audio safely



    SAMPLE_RATE = 48000          # Opus standard sample rate
    CHANNELS = 1                 # Mono audio for voice
    FRAME_DURATION_MS = 20       # 20 ms per frame for low latency
    FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)  # samples per frame

    def __init__(self, settings, net_thread, status_callback=None):
        super().__init__()
        self.settings = settings
        self.net_thread = net_thread
        self.status_callback = status_callback  # Optional UI-bound logger

        self.incomingAudio.connect(self.queue_incoming_audio)

        # Create Opus encoder and decoder instances
        self.encoder = opuslib.Encoder(
            self.SAMPLE_RATE, self.CHANNELS, opuslib.APPLICATION_VOIP
        )
        self.decoder = opuslib.Decoder(
            self.SAMPLE_RATE, self.CHANNELS
        )

        # Internal flags for mute states and modes
        self.loopback_enabled = False
        self.mic_muted = False
        self.mic_paused = False 
        self.spk_muted = False
        self.ptt_pressed = False  # Track PTT state from GUI
        self.ptt_enabled = self.settings.get("ptt", False)
        self.vox_enabled = self.settings.get("vox", False)

        # Audio stream objects for input/output, initialized in start()
        self.stream_in = None
        self.stream_out = None

        # Buffer queue for incoming audio packets to play
        self.incoming_audio_queue = asyncio.Queue()

        # Start a dedicated thread for audio I/O to avoid blocking GUI
        self.audio_thread = threading.Thread(target=self._audio_io_loop, daemon=True)
        self.running = False

        # Voice activation detection threshold and state
        self.vox_threshold = 0.01   # Adjust this threshold for sensitivity
        self.vox_active = False

        # Lock for thread-safe state changes
        self.lock = threading.Lock()

    def _status(self, msg: str):
        """
        Send status message to GUI via callback or fallback to print().
        """
        if self.status_callback:
            self.status_callback(msg)
        else:
            print(msg)

    
    # ── Helper: probe the first input-legal sample-rate that device accepts ──
    def _find_compatible_samplerate(self, dev_index, *, is_input):
        """
        Try a list of common VoIP sample-rates until sounddevice says OK.
        Returns the first sample-rate that passes sd.check_*_settings().
        """
        candidate_rates = [48000, 44100, 32000, 16000, 8000]
        for rate in candidate_rates:
            try:
                if is_input:
                    sd.check_input_settings(device=dev_index, samplerate=rate)
                else:
                    sd.check_output_settings(device=dev_index, samplerate=rate)
                return rate
            except Exception:
                continue
        # As a last resort ask PortAudio for the device’s default rate
        info = sd.query_devices(dev_index, 'input' if is_input else 'output')
        return int(info['default_samplerate'])

    # ── Helper: find a device index by name or fall back to system default ──
    def _find_device_index(self, name, *, is_input):
        devices = sd.query_devices()
        logging.debug("[Audio] Available devices:")
        for idx, dev in enumerate(devices):
            logging.debug(f"  {idx}: {dev['name']} | Input: {dev['max_input_channels']} | Output: {dev['max_output_channels']}")

        if not name:
            logging.debug("[Audio] No device name specified, using default.")
            return None

        for idx, dev in enumerate(devices):
            if dev['name'] == name and (
                (is_input and dev['max_input_channels'] > 0) or
                (not is_input and dev['max_output_channels'] > 0)
            ):
                logging.debug(f"[Audio] Matched requested device '{name}' at index {idx}")
                return idx

        logging.warning(f"[Audio] Requested device '{name}' not found. Falling back to default.")
        return None



    # ── Start the audio subsystem: open streams & launch worker thread ──────
    def start(self):
        logging.debug("[Audio] Entering start()")
        try:
            if self.stream_in or self.stream_out:
                logging.debug("[Audio] Restart requested — stopping old streams")
                self.stop()
                # Wait for audio thread to finish cleanly
                if self.audio_thread and self.audio_thread.is_alive():
                    logging.debug("[Audio] Waiting for audio thread to finish")
                    self.audio_thread.join(timeout=2.0)
                    if self.audio_thread.is_alive():
                        logging.warning("[Audio] Audio thread did not finish in time")

            # Pick devices from saved settings (or defaults)
            in_name = self.settings.get("input_device", None)
            out_name = self.settings.get("output_device", None)
            in_idx = self._find_device_index(in_name, is_input=True)
            out_idx = self._find_device_index(out_name, is_input=False)

            logging.debug(f"[Audio] Input stream device index: {in_idx}")
            logging.debug(f"[Audio] Output stream device index: {out_idx}")

            # Validate actual working samplerates
            self.dev_in_rate = self._find_compatible_samplerate(in_idx, is_input=True)
            sd.check_input_settings(device=in_idx, channels=self.CHANNELS,
                                    samplerate=self.dev_in_rate, dtype='int16')

            self.dev_out_rate = self._find_compatible_samplerate(out_idx, is_input=False)
            sd.check_output_settings(device=out_idx, channels=self.CHANNELS,
                                    samplerate=self.dev_out_rate, dtype='int16')

            logging.debug(f"[Audio] Using input rate {self.dev_in_rate} Hz, output rate {self.dev_out_rate} Hz")
            self._status(f"[Audio] Using input rate {self.dev_in_rate} Hz, output rate {self.dev_out_rate} Hz")

            self.resample_input = (self.dev_in_rate != 48000)

            # Re-create Opus codec
            self.encoder = opuslib.Encoder(48000, self.CHANNELS, opuslib.APPLICATION_VOIP)
            self.decoder = opuslib.Decoder(48000, self.CHANNELS)

            self.dev_in_frames = int(self.dev_in_rate * self.FRAME_DURATION_MS / 1000)
            self.opus_frames = int(48000 * self.FRAME_DURATION_MS / 1000)

            self.stream_in = sd.InputStream(
                samplerate=self.dev_in_rate,
                blocksize=self.dev_in_frames,
                channels=self.CHANNELS,
                dtype='int16',
                device=in_idx,
                callback=self._input_callback,
                latency='low'
            )
            self.stream_out = sd.OutputStream(
                samplerate=48000,
                blocksize=self.opus_frames,
                channels=self.CHANNELS,
                dtype='int16',
                device=out_idx,
                callback=self._output_callback,
                latency='low'
            )

            self.stream_in.start()
            logging.debug("[Audio] stream_in started")
            self.stream_out.start()
            logging.debug("[Audio] stream_out started")

            self.running = True
            self.audio_thread = threading.Thread(target=self._audio_io_loop, daemon=True)
            self.audio_thread.start()
            logging.debug("[Audio] audio_thread started")

            self._status("[Audio] Streams started successfully")

        except Exception as e:
            logging.error(f"[Audio][ERR] Failed to start audio streams: {e}")
            self._status(f"[Audio][ERR] Failed to start audio streams: {e}")
            self.running = False

    def stop(self):
        logging.debug("[Audio] Stopping audio streams")
        self.running = False
        try:
            if self.stream_in:
                self.stream_in.stop()
                self.stream_in.close()
                self.stream_in = None
                logging.debug("[Audio] stream_in stopped and closed")
            if self.stream_out:
                self.stream_out.stop()
                self.stream_out.close()
                self.stream_out = None
                logging.debug("[Audio] stream_out stopped and closed")
        except Exception as e:
            logging.error(f"[Audio][ERR] Error stopping streams: {e}")

        if self.audio_thread and self.audio_thread.is_alive():
            logging.debug("[Audio] Waiting for audio thread to finish on stop")
            self.audio_thread.join(timeout=2.0)
            if self.audio_thread.is_alive():
                logging.warning("[Audio] Audio thread did not finish in time after stop")
            else:
                logging.debug("[Audio] Audio thread finished cleanly after stop")

    # ── Mic capture callback: encode + send ────────────────────────────────
    def _input_callback(self, indata, frames, time_info, status):
        if self.mic_muted:
            #logging.debug("[Audio][_input_callback] Mic muted, skipping frame")
            return

        if status:
            logging.warning(f"[Audio][_input_callback] Input stream status: {status}")

        try:
            pcm = indata[:, 0].copy()
            rms = np.sqrt(np.mean(pcm.astype(np.float32) ** 2))
            
            logging.debug(f"[Audio][Mic Mode Check] 1")
            if self.ptt_enabled:
                logging.debug(f"[Audio][PTT Check] Enabled 1")
                if not self.ptt_pressed:
                    logging.debug("[Audio][_input_callback] PTT enabled but not pressed, skipping frame 1")
                    return
            elif self.vox_enabled:
                logging.debug(f"[Audio][VOX] Enabled 1")
                new_vox = bool(rms >= self.vox_threshold)
                if new_vox != self.vox_active:
                    self.vox_active = new_vox
                    self.voxActivity.emit(new_vox)
                if not new_vox:
                    logging.debug("[Audio][_input_callback] VOX enabled but no voice detected, skipping frame")
                    return  # Skip sending audio because VOX inactive
            else:
                logging.debug(f"[Audio][OPEN MIC] 1")
                # Open Mic mode — always send audio, reset VOX state if needed
                if self.vox_active:
                    self.vox_active = False
                    self.voxActivity.emit(False)

            ## After PTT/Active So Mic level won't go up
            self.inputLevel.emit(rms / 32768.0)

            # Check PTT
            if self.ptt_enabled:
                logging.debug(f"[Audio][PTT Check] Enabled 2")
                if not self.ptt_pressed:
                    logging.debug("[Audio][_input_callback] PTT enabled but not pressed, skipping frame 2")
                    return

            if self.resample_input:
                resampled = samplerate.resample(pcm, 48000 / self.dev_in_rate, 'sinc_fastest')
                gain = self.settings.get("mic_gain", 2.0)
                boosted = (resampled * gain).clip(-32768, 32767)
                pcm_int16 = boosted.astype(np.int16)
            else:
                gain = self.settings.get("mic_gain", 2.0)
                boosted = (pcm.astype(np.float32) * gain).clip(-32768, 32767)
                pcm_int16 = boosted.astype(np.int16)

            opus_bytes = self.encoder.encode(pcm_int16.tobytes(), int(self.opus_frames))
            self.net_thread.queue_message({
                "type": "audio",
                "data": opus_bytes.hex()
            })
            logging.debug(f"[Audio][_input_callback] Queued audio packet: {len(opus_bytes)} bytes")

            if self.loopback_enabled:
                try:
                    self.incoming_audio_queue.put_nowait(opus_bytes.hex())
                except asyncio.QueueFull:
                    logging.warning("[Audio][_input_callback] Loopback audio queue full, dropping packet")

        except Exception as e:
            logging.error(f"[Audio][_input_callback] Error: {e}")




            # ── Resample if device ≠ 48 kHz ───────────────────────────────
            if self.resample_input:
                resampled = samplerate.resample(pcm, 48000 / self.dev_in_rate, 'sinc_fastest')
                gain = self.settings.get("mic_gain", 2.0)  # 2× gain default
                boosted = (resampled * gain).clip(-32768, 32767)
                pcm_int16 = boosted.astype(np.int16)
            else:
                gain = self.settings.get("mic_gain", 2.0)
                boosted = (pcm.astype(np.float32) * gain).clip(-32768, 32767)
                pcm_int16 = boosted.astype(np.int16)


            # ── Opus encode & ship ────────────────────────────────────────
            opus_bytes = self.encoder.encode(pcm_int16.tobytes(), int(self.opus_frames))
            self.net_thread.queue_message({
                "type": "audio",
                "data": opus_bytes.hex()  # hex for JSON safety
            })
            logging.debug(f"[Audio] Queued audio packet: {len(opus_bytes)} bytes")


            
            # Loopback monitoring: enqueue audio locally for playback
            if self.loopback_enabled:
                try:
                    self.incoming_audio_queue.put_nowait(opus_bytes.hex())
                except asyncio.QueueFull:
                    pass

        except Exception as e:
            print(f"[Audio][ERR] Input callback error: {e}")

    def _output_callback(self, outdata, frames, time_info, status):
        if self.spk_muted:
            outdata.fill(0)  # mute speaker output
            return
        if status:
            logging.warning(f"[Audio][OutputCallback] PortAudio status: {status}")

        try:
            opus_packet = self.incoming_audio_queue.get_nowait()
        except asyncio.QueueEmpty:
            logging.debug("[Audio][OutputCallback] Output queue empty - filling silence")
            outdata.fill(0)
            return

        try:
            opus_bytes = bytes.fromhex(opus_packet)
            pcm_bytes = self.decoder.decode(opus_bytes, self.FRAME_SIZE)
            pcm_array = np.frombuffer(pcm_bytes, dtype='int16')
            rms_out = np.sqrt(np.mean(pcm_array.astype(np.float32) ** 2)) / 32768.0
            self.outputLevel.emit(rms_out)
            outdata[:] = pcm_array.reshape((-1, self.CHANNELS))
        except Exception as e:
            logging.warning(f"[Audio][OutputCallback] Audio decoding error: {e}")
            outdata.fill(0)



    @Slot(str)
    def queue_incoming_audio(self, opus_hex):
        """
        Called from GUI thread to queue incoming audio packets for playback.
        We must add this in the event loop thread safely.
        """
        try:
            self.incoming_audio_queue.put_nowait(opus_hex)
        except asyncio.QueueFull:
            pass  # Drop packets if queue is full to maintain low latency

    def watchdog(self):
        """
        Periodically logs current VOX/PTT states thread-safely.
        Call this method regularly (e.g. via a timer or loop).
        """
        with self.lock:
            vox_enabled = self.vox_enabled
            ptt_enabled = self.ptt_enabled
            vox_active = self.vox_active
            mic_muted = self.mic_muted
            spk_muted = self.spk_muted

        logging.debug(f"[Audio][Watchdog] Thread alive | VOX Enabled={vox_enabled} | VOX Active={vox_active} | PTT Enabled={ptt_enabled} | Mic Muted={mic_muted} | Spk Muted={spk_muted}")

    def _audio_io_loop(self):
        """
        Background thread to keep asyncio.Queue alive and monitor audio thread health.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ticks = 0
        try:
            while self.running:
                loop.run_until_complete(asyncio.sleep(1.0))
                ticks += 1
                if ticks % 5 == 0:
                    self.watchdog()

        finally:
            loop.close()

    def set_audio_mode(self, mode: str):
        """
        Set the audio mode: "Open Mic", "Push to Talk", or "Voice Activated".
        Update internal flags accordingly.
        """
        with self.lock:
            if mode == "Open Mic":
                self.ptt_enabled = False
                self.vox_enabled = False
            elif mode == "Push to Talk":
                self.ptt_enabled = True
                self.vox_enabled = False
            elif mode == "Voice Activated":
                self.ptt_enabled = False
                self.vox_enabled = True
            else:
                # Unknown mode — default to PTT
                self.ptt_enabled = True
                self.vox_enabled = False
        logging.info(f"[AudioEngine] Audio mode set to: {mode}")


    def set_ptt_pressed(self, pressed: bool):
        with self.lock:
            if self.ptt_pressed != pressed:
                self.ptt_pressed = pressed
                logging.debug(f"[AudioEngine] PTT pressed set to: {pressed}")


    def set_loopback_enabled(self, enabled: bool):
        """
        Enables or disables loopback monitoring (mic to speaker).
        Emits RMS and queues mic audio to output for self-monitoring.
        """
        with self.lock:
            self.loopback_enabled = enabled
            
    def enqueue_audio_threadsafe(self, opus_hex: str):
        self.incomingAudio.emit(opus_hex)

    # ── Push-to-Talk gate (stub: always allowed unless you implement key state) ──
    def _is_ptt_pressed(self):
        """
        Return whether Push-To-Talk key is pressed.
        Now returns the flag updated from GUI key event filter.
        """
        return self.ptt_enabled and self.ptt_pressed
            
    # ─── Setters to update mute/mode flags from GUI controls ───────────────
    def set_mic_muted(self, muted):
        with self.lock:
            self.mic_muted = muted
        self.micStatusChanged.emit(muted)  # Notify GUI

    def set_spk_muted(self, muted):
        with self.lock:
            self.spk_muted = muted
        self.spkStatusChanged.emit(muted)  # Notify GUI

    def set_ptt_enabled(self, enabled):
        with self.lock:
            self.ptt_enabled = enabled

    def set_vox_enabled(self, enabled):
        with self.lock:
            self.vox_enabled = enabled
            
    def is_vox_enabled(self):
        with self.lock:
            return self.vox_enabled

    def is_ptt_enabled(self):
        with self.lock:
            return self.ptt_enabled

    def is_vox_active(self):
        with self.lock:
            return self.vox_active
    
   


