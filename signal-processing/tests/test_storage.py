import hashlib
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "mocks"))

import pytest

from storage import Signal, SignalNotFoundError, SignalStore, SignalValidationError, validate_signal_dict
from mock_signal import mock_audio_bytes, mock_signal_dict

REAL_RECORDING_PATH = (
    Path(__file__).resolve().parents[2] / "shared" / "tests" / "test.wav"
)


@pytest.fixture()
def store(tmp_path):
    with SignalStore(root=tmp_path / "signals") as s:
        yield s


def test_save_and_get_round_trips_metadata(store):
    audio = mock_audio_bytes(duration_ms=500)
    signal = store.save(
        participant_id="p_teacher",
        room_id="room_1",
        start_time=1000.0,
        end_time=1500.0,
        sample_rate=44100,
        audio_bytes=audio,
        role="teacher",
        mode="teach",
        metadata={"detected_confidence": 0.9},
    )

    fetched = store.get(signal.id)
    assert fetched.id == signal.id
    assert fetched.participant_id == "p_teacher"
    assert fetched.room_id == "room_1"
    assert fetched.role == "teacher"
    assert fetched.mode == "teach"
    assert fetched.metadata["detected_confidence"] == 0.9


def test_replay_is_bit_faithful(store):
    audio = mock_audio_bytes(duration_ms=750, frequency_hz=523.25)
    signal = store.save(
        participant_id="p1",
        room_id="room_1",
        start_time=0.0,
        end_time=750.0,
        sample_rate=44100,
        audio_bytes=audio,
    )

    replayed = store.get_audio(signal.id)
    assert replayed == audio


def test_replay_independent_of_call_session(tmp_path):
    """A Signal can be fetched/replayed after the store handle that wrote it
    is gone, i.e. persistence doesn't depend on the live call still being
    active."""
    audio = mock_audio_bytes(duration_ms=200)
    root = tmp_path / "signals"

    with SignalStore(root=root) as s1:
        signal = s1.save(
            participant_id="p1",
            room_id="room_1",
            start_time=0.0,
            end_time=200.0,
            sample_rate=44100,
            audio_bytes=audio,
        )
        signal_id = signal.id

    with SignalStore(root=root) as s2:
        fetched = s2.get(signal_id)
        assert fetched.id == signal_id
        assert s2.get_audio(signal_id) == audio


def test_get_missing_signal_raises(store):
    with pytest.raises(SignalNotFoundError):
        store.get("does-not-exist")


def test_update_metadata_merges_without_touching_audio(store):
    audio = mock_audio_bytes(duration_ms=300)
    signal = store.save(
        participant_id="p1",
        room_id="room_1",
        start_time=0.0,
        end_time=300.0,
        sample_rate=44100,
        audio_bytes=audio,
        metadata={"detected_confidence": 0.8},
    )

    updated = store.update_metadata(signal.id, cleaned=True)
    assert updated.metadata["cleaned"] is True
    assert updated.metadata["detected_confidence"] == 0.8
    assert store.get_audio(signal.id) == audio


def test_list_for_room_orders_by_start_time(store):
    audio = mock_audio_bytes(duration_ms=100)
    s2 = store.save(
        participant_id="p1", room_id="room_1", start_time=500.0, end_time=600.0,
        sample_rate=44100, audio_bytes=audio,
    )
    s1 = store.save(
        participant_id="p1", room_id="room_1", start_time=0.0, end_time=100.0,
        sample_rate=44100, audio_bytes=audio,
    )

    signals = store.list_for_room("room_1")
    assert [s.id for s in signals] == [s1.id, s2.id]


def test_signal_from_dict_validates_against_schema():
    valid = mock_signal_dict()
    Signal.from_dict(valid)  # should not raise

    invalid = mock_signal_dict()
    invalid["audio_format"] = "mp3"  # lossy formats are rejected
    with pytest.raises(SignalValidationError):
        Signal.from_dict(invalid)

    invalid2 = mock_signal_dict()
    invalid2["end_time"] = invalid2["start_time"] - 1
    with pytest.raises(SignalValidationError):
        Signal.from_dict(invalid2)


@pytest.mark.skipif(
    not REAL_RECORDING_PATH.exists(),
    reason=f"no real recording fixture at {REAL_RECORDING_PATH}",
)
def test_real_recording_round_trips_bit_faithfully(tmp_path):
    """End-to-end check against Task 3's acceptance criteria using an actual
    voice recording (shared/tests/test.wav — converted from a Voice Memos
    .m4a via `afconvert -f WAVE -d LEI16@44100 -c 1`), not just a synthetic
    tone. Verifies extraction -> storage -> replay is bit-faithful using a
    cryptographic hash (stronger than `==`, which could pass on a truncated
    buffer of equal length) and that fetch works via a fresh SignalStore
    instance, independent of whatever session wrote it."""
    original_bytes = REAL_RECORDING_PATH.read_bytes()
    original_hash = hashlib.sha256(original_bytes).hexdigest()

    with wave.open(str(REAL_RECORDING_PATH), "rb") as wf:
        sample_rate = wf.getframerate()
        duration_ms = wf.getnframes() / sample_rate * 1000

    root = tmp_path / "signals"
    with SignalStore(root=root) as writer:
        signal = writer.save(
            participant_id="p_teacher",
            room_id="room_demo",
            start_time=0.0,
            end_time=duration_ms,
            sample_rate=sample_rate,
            audio_bytes=original_bytes,
            role="teacher",
            mode="teach",
        )
        validate_signal_dict(signal.to_dict())
        signal_id = signal.id

    # Fresh store instance: simulates fetching after the writing session/app
    # has ended, not just re-reading in-process.
    with SignalStore(root=root) as reader:
        fetched = reader.get(signal_id)
        assert fetched.participant_id == "p_teacher"

        replayed_bytes = reader.get_audio(signal_id)
        replayed_hash = hashlib.sha256(replayed_bytes).hexdigest()
        assert replayed_hash == original_hash
