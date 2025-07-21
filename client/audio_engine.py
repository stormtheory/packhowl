# client/audio_engine.py

import asyncio
import threading
import logging
import numpy as np
import opuslib
import sounddevice as sd
import samplerate
import logging

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


    SAMPLE_RATE = 48000          # Opus standard sample rate
    CHANNELS = 1                 # Mono audio for voice
    FRAME_DURATION_MS = 20       # 20 ms per frame for low latency
    FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)  # samples per frame

    def __init__(self, settings, net_thread, status_callback=None):
        super().__init__()
        self.settings = settings
        self.net_thread = net_thread
        self.status_callback = status_callback  # Optional UI-bound logger


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
        if not name:
            return None
        for idx, dev in enumerate(sd.query_devices()):
            if dev['name'] == name and (
                (is_input  and dev['max_input_channels']  > 0) or
                (not is_input and dev['max_output_channels'] > 0)
            ):
                logging.debug(f"Found device '{name}' as index {idx}")
                return idx
        logging.warning(f"Device '{name}' not found. Falling back to default.")
        return None


    # ── Start the audio subsystem: open streams & launch worker thread ──────
    def start(self):
        """
        Opens input/output streams at the closest-supported device rate.
        Resamples to 48 kHz for Opus if the device can’t do 48 kHz natively.
        """
        # ── Pick devices from saved settings (or defaults) ──────────────────
        in_name  = self.settings.get("input_device",  None)
        out_name = self.settings.get("output_device", None)
        in_idx   = self._find_device_index(in_name,  is_input=True)
        out_idx  = self._find_device_index(out_name, is_input=False)

        # ── Validate actual working samplerates ─────────────────────────────
        try:
            self.dev_in_rate = self._find_compatible_samplerate(in_idx, is_input=True)
            sd.check_input_settings(device=in_idx, channels=self.CHANNELS,
                                    samplerate=self.dev_in_rate, dtype='int16')
        except Exception as e:
            raise RuntimeError(f"[Audio][ERR] Could not configure input device: {e}")

        try:
            self.dev_out_rate = self._find_compatible_samplerate(out_idx, is_input=False)
            sd.check_output_settings(device=out_idx, channels=self.CHANNELS,
                                    samplerate=self.dev_out_rate, dtype='int16')
        except Exception as e:
            raise RuntimeError(f"[Audio][ERR] Could not configure output device: {e}")

        logging.debug(f"[Audio] Using input rate {self.dev_in_rate} Hz, output rate {self.dev_out_rate} Hz")
        self._status(f"[Audio] Using input rate {self.dev_in_rate} Hz, output rate {self.dev_out_rate} Hz")
        self._status("[Audio] Streams started successfully")


        # Flag if we must resample mic frames → 48 kHz for Opus
        self.resample_input = (self.dev_in_rate != 48000)

        # ── (Re-)create Opus codec (always 48 kHz mono) ─────────────────────
        self.encoder = opuslib.Encoder(48000, self.CHANNELS, opuslib.APPLICATION_VOIP)
        self.decoder = opuslib.Decoder(48000, self.CHANNELS)

        # ── Compute frame sizes for each rate (20 ms default frame) ─────────
        self.dev_in_frames  = int(self.dev_in_rate  * self.FRAME_DURATION_MS / 1000)
        self.opus_frames    = int(48000            * self.FRAME_DURATION_MS / 1000)

        # ── Build PortAudio streams ─────────────────────────────────────────
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
            samplerate=48000,                    # always feed 48 kHz into speaker path
            blocksize=self.opus_frames,
            channels=self.CHANNELS,
            dtype='int16',
            device=out_idx,
            callback=self._output_callback,
            latency='low'
        )

        # ── Kick everything off ─────────────────────────────────────────────
        self.stream_in.start()
        self.stream_out.start()
        self.running = True
        print("[Audio] Streams started successfully")

        # Worker thread keeps any asyncio queues alive for playback
        self.audio_thread = threading.Thread(target=self._audio_io_loop, daemon=True)
        self.audio_thread.start()

    def stop(self):
        """
        Stop audio processing and close streams.
        """
        self.running = False
        if self.stream_in:
            self.stream_in.stop()
            self.stream_in.close()
            self.stream_in = None
        if self.stream_out:
            self.stream_out.stop()
            self.stream_out.close()
            self.stream_out = None

    # ── Mic capture callback: encode + send ────────────────────────────────
    def _input_callback(self, indata, frames, time_info, status):
        """
        Called by sounddevice every `dev_in_frames` samples. Handles VOX/PTT,
        optional resampling to 48 kHz, then Opus-encodes and enqueues JSON.
        """
        if self.mic_muted:
            return  # Hard-mute

        try:
            pcm = indata[:, 0].copy()  # mono → flat int16 ndarray

            # ── Compute RMS for input level meter ────────────────────────
            rms = np.sqrt(np.mean(pcm.astype(np.float32) ** 2))
            self.inputLevel.emit(rms / 32768.0) # Emit signal for GUI mic level

            # ── Basic VOX (voice detection) ──────────────────────────────
            if self.vox_enabled:
                new_vox = rms >= self.vox_threshold
                if new_vox != self.vox_active:
                    self.vox_active = new_vox
                    self.voxActivity.emit(self.vox_active)
                if not new_vox:
                    return  # below threshold, skip frame

            # ── Push-to-Talk gate (stub: always allowed unless you implement key state) ──
            def _is_ptt_pressed(self):
                """
                Return whether Push-To-Talk key is pressed.
                Now returns the flag updated from GUI key event filter.
                """
                return self.ptt_enabled and self.ptt_pressed
            
            def set_ptt_pressed(self, pressed: bool):
                with self.lock:
                    self.ptt_pressed = pressed

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
            opus_bytes = self.encoder.encode(pcm_int16.tobytes(), self.opus_frames)
            self.net_thread.queue_message({
                "type": "audio",
                "data": opus_bytes.hex()  # hex for JSON safety
            })
            
            # Loopback monitoring: enqueue audio locally for playback
            if self.loopback_enabled:
                try:
                    self.incoming_audio_queue.put_nowait(opus_bytes.hex())
                except asyncio.QueueFull:
                    pass

        except Exception as e:
            print(f"[Audio][ERR] Input callback error: {e}")

    def _output_callback(self, outdata, frames, time_info, status):
        """
        Called in sounddevice output thread context when audio playback buffer is needed.
        Dequeues Opus packets, decodes to PCM, and writes to output buffer.
        """
        # Non-blocking read from incoming queue
        try:
            opus_packet = self.incoming_audio_queue.get_nowait()
        except asyncio.QueueEmpty:
            # No audio packet available, output silence
            outdata.fill(0)
            return

        try:
            # Decode hex string back to bytes
            opus_bytes = bytes.fromhex(opus_packet)
            # Decode to PCM int16 bytes
            pcm_bytes = self.decoder.decode(opus_bytes, self.FRAME_SIZE)
            # Convert PCM bytes to numpy int16 array for playback
            pcm_array = np.frombuffer(pcm_bytes, dtype='int16')
            # Calculate RMS level normalized between 0-1 (max int16 = 32768)
            rms_out = np.sqrt(np.mean(pcm_array.astype(np.float32) ** 2)) / 32768.0
            self.outputLevel.emit(rms_out)
            outdata[:] = pcm_array.reshape((-1, self.CHANNELS))

        except Exception as e:
            logging.warning(f"Audio decoding error: {e}")
            outdata.fill(0)  # Output silence on error

    def queue_incoming_audio(self, opus_hex):
        """
        Called from GUI thread to queue incoming audio packets for playback.
        We must add this in the event loop thread safely.
        """
        try:
            self.incoming_audio_queue.put_nowait(opus_hex)
        except asyncio.QueueFull:
            pass  # Drop packets if queue is full to maintain low latency

    def _audio_io_loop(self):
        """
        Runs in separate thread to keep asyncio.Queue running properly with sounddevice threads.
        This thread can be extended to handle other audio processing as needed.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while self.running:
            loop.run_until_complete(asyncio.sleep(0.1))
        loop.close()

    def _is_ptt_pressed(self):
        """
        Return whether Push-To-Talk key is pressed.
        This function needs platform-specific implementation or integration with GUI input events.
        For now, we assume PTT is always pressed if enabled (for demo purposes).
        """
        # TODO: integrate with actual PTT key state detection (e.g., via pynput or Qt events)
        return True if self.ptt_enabled else False
    
    def set_ptt_pressed(self, pressed: bool):
        """
        Called by GUI to update Push-To-Talk key state in real time.
        This is required for dynamic PTT gating in the input callback.
        """
        with self.lock:
            self.ptt_pressed = pressed

    def set_loopback_enabled(self, enabled: bool):
        """
        Enables or disables loopback monitoring (mic to speaker).
        Emits RMS and queues mic audio to output for self-monitoring.
        """
        with self.lock:
            self.loopback_enabled = enabled


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
