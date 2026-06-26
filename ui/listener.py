"""
voice/listener.py — Wake word detection with fuzzy matching + deduplication
Wake words: "Jarvis" / "Jarv"
F8 = push-to-talk (bypasses wake word)

FIXES (Session 5):
  - _speaking_until = +86400s replaced: _speaking bool handles "stay muted
    during speech"; _speaking_until is ONLY the post-TTS cooldown tail (1.5s).
    If set_speaking(False) ever fails, mic recovers after 1.5s not 24hrs.
  - silero_deactivity_detection wrapped in try/except — invalid on older
    RealtimeSTT versions, caused startup TypeError.

CROSS-PLATFORM FIX (Session 41):
  - OSError shutdown detection was Windows-only: checked for "WinError 6" /
    "handle is invalid" string. On macOS/Linux the equivalent is errno.EBADF
    (bad file descriptor). Without this fix, a clean shutdown on non-Windows
    would fall through to the "restarting" branch and spin for 0.5s before
    the _stopping flag eventually broke the loop. Fix: also check
    getattr(exc, "errno", None) == errno.EBADF. Windows behaviour unchanged.

SAFETY FIXES (Session 33):
  - _ptt_active and _stopping are written from the keyboard/main thread and
    read from the listener thread. Made accesses atomic via a threading.Lock
    (_state_lock) so reads/writes are always consistent across threads on
    CPython and non-CPython implementations.
  - WAKE_WORD_ALIASES contained punctuated forms ("jarvis,", "jarvis.") but
    _normalise() strips all punctuation before comparison, so those entries
    never matched. Removed the punctuated forms — they were dead entries that
    could mislead future maintainers.
  - is_shutdown_command() received the post-wake-word command string, which
    has already been normalised. But if called directly from launch.py with
    the raw full transcript (before _extract_command), punctuation like "Jarvis,
    shut down." could fail the phrase match. Added a _normalise() call at the
    top of is_shutdown_command() as a defensive guard.
  - Fallback AudioToTextRecorder() construction after a TypeError could itself
    raise TypeError (e.g. if an unrelated kwarg is also unsupported). Wrapped
    the fallback construction in its own try/except so a second failure is
    caught and logged rather than crashing start() with an unhandled TypeError.
  - OSError restart branch called time.sleep(0.5) OUTSIDE the else block —
    it ran even on the "break" path (WinError 6 / handle invalid). The sleep
    was harmless there but confusing. Moved inside the else so it only fires
    on the "restarting" path.
  - Removed five blank lines between _extract_command() and the Shutdown
    section (cosmetic, no functional change).
"""

import logging
import re
import time
import threading
from RealtimeSTT import AudioToTextRecorder

logger = logging.getLogger("voice.listener")

# ---------------------------------------------------------------------------
# TTS mute flag
# ---------------------------------------------------------------------------
_speaking = False
_speaking_until: float = 0.0
_speaking_lock = threading.Lock()
TTS_COOLDOWN = 1.5


def set_speaking(active: bool):
    global _speaking, _speaking_until
    with _speaking_lock:
        _speaking = active
        if not active:
            # Start the post-TTS cooldown tail only when speech ends
            _speaking_until = time.time() + TTS_COOLDOWN
        # When active=True, _speaking bool alone holds the mute —
        # no absurd future timestamp needed.


def _is_muted() -> bool:
    with _speaking_lock:
        if _speaking:
            return True
        if time.time() < _speaking_until:
            return True
    return False


# ---------------------------------------------------------------------------
# Wake word config
# ---------------------------------------------------------------------------
WAKE_WORDS = ["jarvis", "jarv"]

# NOTE: these are compared AFTER _normalise() strips all punctuation,
# so do NOT add forms like "jarvis," or "jarvis." — they will never match.
WAKE_WORD_ALIASES = [
    "jarvis", "jarv", "jarwis",
    "jarwislo", "jarwise", "jarvish", "java", "jarbi",
    "jervis", "jarve", "jarfis", "jarwich", "jardis",
]

# Phrases that mean "shut everything down"
SHUTDOWN_PHRASES = [
    "close", "shut down", "shutdown", "shut off", "exit", "quit",
    "goodbye", "good bye", "bye", "turn off", "stop",
]

DEDUP_WINDOW = 2.5
_last_transcript: str = ""
_last_transcript_time: float = 0.0
_dedup_lock = threading.Lock()


def _is_duplicate(text: str) -> bool:
    global _last_transcript, _last_transcript_time
    now = time.time()
    with _dedup_lock:
        if text == _last_transcript and (now - _last_transcript_time) < DEDUP_WINDOW:
            return True
        _last_transcript = text
        _last_transcript_time = now
    return False


def _normalise(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_leading_wakeword(text: str) -> str:
    """
    Strip a leading wake word from raw (un-normalised) text, if present.

    Used for PTT (push-to-talk) transcripts, which bypass _extract_command()
    entirely since PTT doesn't require a wake word — but users habitually
    say "Jarv"/"Jarvis" out of habit anyway, and that word would otherwise
    ride straight through into the command (e.g. "jarv describe whats on
    my screen" reaching the agent verbatim, including "jarv").
    """
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return text
    first_norm = _normalise(parts[0])
    all_aliases = set(WAKE_WORD_ALIASES) | set(WAKE_WORDS)
    if first_norm in all_aliases:
        return parts[1] if len(parts) > 1 else ""
    return text


def _extract_command(text: str):
    norm = _normalise(text)
    words = norm.split()
    if not words:
        return False, None

    all_aliases = set(WAKE_WORD_ALIASES) | set(WAKE_WORDS)

    # Leading wake word — check first 3 words
    for i, word in enumerate(words[:3]):
        if word in all_aliases:
            remainder = words[i + 1:]
            # Strip any second wake word immediately following
            for j, w in enumerate(remainder):
                if w in all_aliases:
                    remainder = remainder[:j]
                    break
            command = " ".join(remainder).strip()
            if i > 0:
                logger.debug("Wake word '%s' found at position %d", word, i)
            return True, command

    # Two-word wake phrase at position 0 or 1
    for i in range(min(2, len(words) - 1)):
        two = f"{words[i]} {words[i+1]}"
        if two in ("hey jarvis", "ok jarvis", "okay jarvis"):
            command = " ".join(words[i + 2:]).strip()
            return True, command

    # Trailing wake word — "send the message Jarvis"
    # Everything before the wake word becomes the command.
    for i in range(len(words) - 1, max(len(words) - 3, -1), -1):
        if words[i] in all_aliases:
            command = " ".join(words[:i]).strip()
            if command:
                logger.debug("Trailing wake word '%s' found at position %d", words[i], i)
                return True, command
            # Wake word with nothing before it — fall through

    return False, None


# ---------------------------------------------------------------------------
# Shutdown detection (exported so launch.py can use it too)
# ---------------------------------------------------------------------------

def strip_wake_word(text: str) -> str:
    """
    Public wrapper for _strip_leading_wakeword(), for use by other modules
    (e.g. ui/text_input.py) that need to strip an accidental leading wake
    word from text that never passes through _on_transcript()/_extract_command().
    """
    return _strip_leading_wakeword(text)


def is_shutdown_command(command: str) -> bool:
    """
    Return True if the post-wake-word command means 'shut everything down'.

    Accepts either the already-normalised post-wake-word remainder OR the
    full raw transcript (e.g. when called from launch.py before extraction).
    _normalise() is applied defensively so punctuation never causes a miss.
    """
    if not command:
        return False
    norm = _normalise(command)
    for phrase in SHUTDOWN_PHRASES:
        if norm == phrase or norm.startswith(phrase + " ") or norm.endswith(" " + phrase):
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class VoiceListener:
    def __init__(self, on_command, on_ptt_command=None):
        self._on_command = on_command
        self._on_ptt = on_ptt_command or on_command
        self._recorder = None

        # _state_lock guards _ptt_active and _stopping.
        # Both are written from the keyboard/main thread and read from the
        # listener (daemon) thread. A lock ensures visibility on all Python
        # implementations, not just CPython with its GIL.
        self._state_lock = threading.Lock()
        self._ptt_active = False
        self._stopping = False

    def _on_transcript(self, text: str):
        text = text.strip()
        if not text:
            return

        if _is_muted():
            logger.debug("Suppressed during TTS: %r", text)
            return

        logger.info("Heard: %r", text)

        if _is_duplicate(text):
            logger.debug("Duplicate transcript ignored: %r", text)
            return

        with self._state_lock:
            ptt = self._ptt_active

        if ptt:
            # [FIXED] PTT bypasses wake-word *requirement*, but users still
            # say "jarv"/"jarvis" out of habit — strip it if present so it
            # doesn't ride along into the command sent to the agent.
            stripped = _strip_leading_wakeword(text)
            if stripped != text:
                logger.debug("Stripped leading wake word from PTT transcript: %r -> %r", text, stripped)
            text = stripped
            logger.info("PTT command: %r", text)
            self._on_ptt(text)
            return

        matched, command = _extract_command(text)
        if matched:
            if command:
                logger.info("Command after wake word: %r", command)
                self._on_command(command)
            else:
                logger.info("Wake word only — routing as 'hello'")
                self._on_command("hello")
        else:
            logger.debug("No wake word — ignored: %r", text)

    def start(self):
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            input_devices = [d for d in devices if d["max_input_channels"] > 0]
            if not input_devices:
                logger.error("NO INPUT DEVICES FOUND — check microphone connection.")
                return
            default_input = sd.query_devices(kind="input")
            logger.info("Microphone: %s", default_input["name"])
        except Exception as e:
            logger.warning("Could not check audio devices: %s", e)

        logger.info("Initialising RealtimeSTT (model=base)...")

        recorder_kwargs = dict(
            model="base",
            language="en",
            initial_prompt="Jarvis, Jarv. Interface navigation, system tasks, and responsive conversation.",
            spinner=False,
            enable_realtime_transcription=False,
            on_recording_stop=None,
            silero_sensitivity=0.3,
            min_length_of_recording=0.6,
        )

        # Try with silero_deactivity_detection first; fall back if unsupported.
        # Both construction attempts are individually guarded — a second
        # unexpected TypeError in the fallback path is now caught and logged
        # rather than crashing start() with an unhandled exception.
        try:
            self._recorder = AudioToTextRecorder(
                **recorder_kwargs,
                silero_deactivity_detection=True,
            )
        except TypeError:
            logger.warning(
                "silero_deactivity_detection not supported in this RealtimeSTT "
                "version — retrying without it."
            )
            try:
                self._recorder = AudioToTextRecorder(**recorder_kwargs)
            except TypeError as exc:
                logger.error("FAILED to initialise RealtimeSTT (fallback TypeError): %s", exc)
                return
            except Exception as exc:
                logger.error("FAILED to initialise RealtimeSTT (fallback): %s", exc)
                return
        except Exception as exc:
            logger.error("FAILED to initialise RealtimeSTT: %s", exc)
            return

        logger.info("RealtimeSTT ready — say 'Jarvis <command>' to activate.")
        logger.info("[F8] push-to-talk | [F10] text input")

        while True:
            with self._state_lock:
                stopping = self._stopping
            if stopping:
                logger.info("Stop requested — exiting listen loop.")
                break
            try:
                self._recorder.text(self._on_transcript)
            except KeyboardInterrupt:
                logger.info("Listener stopped.")
                break
            except OSError as exc:
                # WinError 6 / "handle is invalid" = Windows pipe closed on shutdown.
                # errno.EBADF (9) = macOS/Linux equivalent (bad file descriptor).
                import errno as _errno
                _s = str(exc).lower()
                if (
                    "winerror 6" in _s
                    or "handle is invalid" in _s
                    or getattr(exc, "errno", None) == _errno.EBADF
                ):
                    logger.debug("RealtimeSTT pipe closed — normal shutdown")
                    break
                else:
                    logger.warning("RealtimeSTT OS error: %s — restarting...", exc)
                    time.sleep(0.5)   # only on the restarting path, not on break
            except Exception as exc:
                with self._state_lock:
                    stopping = self._stopping
                if stopping:
                    logger.debug("RealtimeSTT error during shutdown (expected): %s", exc)
                    break
                logger.warning("RealtimeSTT error: %s — restarting...", exc)
                time.sleep(1)

    def set_ptt_active(self, active: bool):
        with self._state_lock:
            self._ptt_active = active
        logger.debug("PTT %s", "active" if active else "released")

    def stop(self):
        """
        Gracefully shut down RealtimeSTT before the process exits.

        Call this BEFORE the host process tears down (e.g. before app.quit())
        so RealtimeSTT's worker process/pipe are closed cleanly instead of
        being killed mid-poll, which otherwise floods the log with
        BrokenPipeError tracebacks from its internal poll_connection() loop.
        """
        with self._state_lock:
            self._stopping = True
        if self._recorder is not None:
            try:
                logger.info("Shutting down RealtimeSTT recorder...")
                self._recorder.shutdown()
                logger.info("RealtimeSTT recorder shut down cleanly.")
            except Exception as exc:
                logger.warning("Error during RealtimeSTT shutdown (ignored): %s", exc)
