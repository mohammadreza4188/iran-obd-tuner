"""ISO-TP (ISO 15765-2) over a synchronous fake bus with a fake clock: fully deterministic."""

from __future__ import annotations

import pytest

from iran_obd_tuner.core.exceptions import AdapterTimeoutError, ProtocolError
from iran_obd_tuner.core.traffic import Direction, TrafficLog
from iran_obd_tuner.hardware.can_bus import CanFrame
from iran_obd_tuner.hardware.isotp import (
    MAX_PAYLOAD,
    IsoTpChannel,
    IsoTpConfig,
    decode_st_min,
    functional_request,
)

TX, RX = 0x7E0, 0x7E8
CONFIG = IsoTpConfig(tx_id=TX, rx_id=RX)


def frame(arbitration_id: int, hex_data: str) -> CanFrame:
    return CanFrame(arbitration_id, bytes.fromhex(hex_data))


def channel(bus, config: IsoTpConfig = CONFIG, **kwargs) -> IsoTpChannel:
    return IsoTpChannel(bus, config, clock=bus.clock, sleep=bus.sleep, **kwargs)


def fc(status: int = 0, bs: int = 0, st_min: int = 0) -> CanFrame:
    return CanFrame(RX, bytes([0x30 | status, bs, st_min, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC]))


# --------------------------------------------------------------------------- sending


def test_single_frame_is_padded_to_eight_bytes(fake_can_bus) -> None:
    bus = fake_can_bus()
    channel(bus).send(bytes.fromhex("22F190"))

    assert [f.data.hex(" ").upper() for f in bus.sent] == ["03 22 F1 90 CC CC CC CC"]
    assert bus.sent[0].arbitration_id == TX


def test_padding_can_be_disabled(fake_can_bus) -> None:
    bus = fake_can_bus()
    channel(bus, IsoTpConfig(TX, RX, padding=None)).send(b"\x3e\x00")

    assert bus.sent[0].data == b"\x02\x3e\x00"


def test_segmented_send_waits_for_flow_control(fake_can_bus) -> None:
    bus = fake_can_bus(lambda f: [fc()] if f.data[0] >> 4 == 1 else [])
    payload = bytes(range(20))

    channel(bus).send(payload)

    data = [f.data for f in bus.sent]
    assert data[0][:2] == b"\x10\x14" and data[0][2:] == payload[:6]
    assert [d[0] for d in data[1:]] == [0x21, 0x22]
    assert data[1][1:] == payload[6:13] and data[2][1:] == payload[13:20]


def test_st_min_separates_consecutive_frames(fake_can_bus) -> None:
    for raw, expected in ((10, 0.010), (0xF5, 0.0005)):
        sleeps: list[float] = []
        bus = fake_can_bus(lambda f, raw=raw: [fc(st_min=raw)] if f.data[0] >> 4 == 1 else [])
        IsoTpChannel(bus, CONFIG, clock=bus.clock, sleep=sleeps.append).send(bytes(30))

        assert sleeps == [pytest.approx(expected)] * 3  # 4 CFs -> 3 separations


def test_block_size_requests_a_new_flow_control_per_block(fake_can_bus) -> None:
    sent_cf = 0

    def responder(f: CanFrame):
        nonlocal sent_cf
        if f.data[0] >> 4 == 1:
            return [fc(bs=2)]
        sent_cf += 1
        return [fc(bs=2)] if sent_cf % 2 == 0 else []

    bus = fake_can_bus(responder)
    channel(bus).send(bytes(40))  # 6 + 5 CFs

    assert sum(1 for f in bus.sent if f.data[0] >> 4 == 2) == 5


def test_wait_frames_are_honoured_and_bounded(fake_can_bus) -> None:
    bus = fake_can_bus(lambda f: [fc(status=1), fc()] if f.data[0] >> 4 == 1 else [])
    channel(bus).send(bytes(10))
    assert bus.sent[-1].data[0] == 0x21

    endless = fake_can_bus(lambda f: [fc(status=1)] * 20 if f.data[0] >> 4 == 1 else [])
    with pytest.raises(ProtocolError, match="N_WFTmax"):
        channel(endless).send(bytes(10))


def test_overflow_and_missing_flow_control(fake_can_bus) -> None:
    overflow = fake_can_bus(lambda f: [fc(status=2)])
    with pytest.raises(ProtocolError, match="overflow"):
        channel(overflow).send(bytes(10))

    silent = fake_can_bus()
    with pytest.raises(AdapterTimeoutError, match="N_Bs"):
        channel(silent).send(bytes(10))


def test_payload_limits(fake_can_bus) -> None:
    bus = fake_can_bus(lambda f: [fc()] if f.data[0] >> 4 == 1 else [])
    channel(bus).send(bytes(MAX_PAYLOAD))
    assert bus.sent[0].data[:2] == b"\x1f\xff"
    for size in (0, MAX_PAYLOAD + 1):
        with pytest.raises(ValueError):
            channel(bus).send(bytes(size))


# --------------------------------------------------------------------------- receiving


def test_receive_single_frame_and_ignore_other_ids(fake_can_bus) -> None:
    bus = fake_can_bus()
    bus.incoming = [frame(0x123, "0201020000000000"), frame(RX, "027E00CCCCCCCCCC")]

    assert channel(bus).recv(1.0) == b"\x7e\x00"


def test_receive_segmented_message_sends_flow_control(fake_can_bus) -> None:
    def responder(f: CanFrame):
        if f.data[0] == 0x30:  # our flow control
            return [frame(RX, "21" + "07080910111213"), frame(RX, "22" + "141516CCCCCCCC")]
        return []

    bus = fake_can_bus(responder)
    bus.incoming = [frame(RX, "100F" + "010203040506")]
    config = IsoTpConfig(TX, RX, block_size=0, st_min=5)

    message = channel(bus, config).recv(1.0)

    assert message == bytes.fromhex("010203040506070809101112131415" + "16")[:15]
    assert bus.sent[0].data[:3] == b"\x30\x00\x05"  # FC advertises our BS/STmin


def test_receive_with_block_size_sends_flow_control_per_block(fake_can_bus) -> None:
    cfs = iter([frame(RX, f"2{n}" + "AA" * 7) for n in range(1, 4)])

    def responder(f: CanFrame):
        return [next(cfs)] if f.data[0] == 0x30 else []

    bus = fake_can_bus(responder)
    bus.incoming = [frame(RX, "101B" + "AA" * 6)]  # 27 bytes -> 3 CFs

    message = channel(bus, IsoTpConfig(TX, RX, block_size=1)).recv(1.0)

    assert len(message) == 27
    assert sum(1 for f in bus.sent if f.data[0] == 0x30) == 3


def test_wrong_sequence_and_missing_consecutive_frame(fake_can_bus) -> None:
    bad = fake_can_bus(lambda f: [frame(RX, "22" + "00" * 7)] if f.data[0] == 0x30 else [])
    bad.incoming = [frame(RX, "1010" + "00" * 6)]
    with pytest.raises(ProtocolError, match="2 received, 1 expected"):
        channel(bad).recv(1.0)

    stalled = fake_can_bus()
    stalled.incoming = [frame(RX, "1010" + "00" * 6)]
    with pytest.raises(AdapterTimeoutError, match="N_Cr"):
        channel(stalled).recv(1.0)


def test_a_new_first_frame_restarts_reception(fake_can_bus) -> None:
    replies = iter(
        [
            [frame(RX, "100A" + "BBBBBBBBBBBB")],  # sender restarts
            [frame(RX, "21" + "BBBBBBBBCCCCCC")],
        ]
    )
    bus = fake_can_bus(lambda f: next(replies) if f.data[0] == 0x30 else [])
    bus.incoming = [frame(RX, "1010" + "AA" * 6)]

    assert channel(bus).recv(1.0) == b"\xbb" * 10


class DrippingBus:
    """Answers every recv with ``make_frame()`` after ``every_s`` seconds of fake time."""

    simulated = True

    def __init__(self, clock, make_frame, every_s: float) -> None:
        self.clock, self.make_frame, self.every_s = clock, make_frame, every_s
        self.sent: list[CanFrame] = []

    def send(self, frame: CanFrame) -> None:
        self.sent.append(frame)

    def recv(self, timeout_s: float):
        if timeout_s < self.every_s:
            self.clock.advance(timeout_s)
            return None
        self.clock.advance(self.every_s)
        return self.make_frame()

    def close(self) -> None:
        pass

    def sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)


def test_stray_frames_do_not_keep_the_n_cr_wait_alive(fake_clock) -> None:
    first = [True]

    def next_frame() -> CanFrame:
        if first[0]:
            first[0] = False
            return frame(RX, "1010" + "AA" * 6)  # FF of 16 bytes, then only stray FCs
        return fc()

    bus = DrippingBus(fake_clock, next_frame, every_s=0.4)
    started = fake_clock()

    with pytest.raises(AdapterTimeoutError, match="N_Cr"):
        channel(bus).recv(1.0)

    assert fake_clock() - started <= 0.4 + CONFIG.n_cr_s + 0.01


def test_a_sender_dripping_consecutive_frames_hits_the_message_ceiling(fake_clock) -> None:
    sequence = [0]

    def next_frame() -> CanFrame:
        if sequence[0] == 0:
            sequence[0] = 1
            return frame(RX, "1FFF" + "AA" * 6)  # 4095 bytes announced
        number, sequence[0] = sequence[0] & 0x0F, sequence[0] + 1
        return CanFrame(RX, bytes([0x20 | number]) + b"\xaa" * 7)

    bus = DrippingBus(fake_clock, next_frame, every_s=0.9)  # just inside N_Cr every time
    config = IsoTpConfig(TX, RX, max_message_s=5.0)
    started = fake_clock()

    with pytest.raises(AdapterTimeoutError, match="not complete within 5.0s"):
        channel(bus, config).recv(1.0)

    assert fake_clock() - started <= 0.9 + 5.0 + 0.01


def test_a_sender_that_keeps_restarting_is_refused(fake_clock) -> None:
    bus = DrippingBus(fake_clock, lambda: frame(RX, "1010" + "AA" * 6), every_s=0.01)

    with pytest.raises(ProtocolError, match="restarted its message 4 times"):
        channel(bus, IsoTpConfig(TX, RX, max_restarts=3)).recv(5.0)

    assert sum(1 for f in bus.sent if f.data[0] == 0x30) == 4  # one FC per FF, bounded


def test_a_restart_after_the_deadline_is_refused(fake_clock) -> None:
    bus = DrippingBus(fake_clock, lambda: frame(RX, "1010" + "AA" * 6), every_s=0.6)

    with pytest.raises(AdapterTimeoutError, match="after the 1.0s deadline"):
        channel(bus, IsoTpConfig(TX, RX, max_restarts=10)).recv(1.0)


def test_frames_received_counts_only_the_response_identifier(fake_can_bus) -> None:
    bus = fake_can_bus()
    bus.incoming = [frame(0x123, "0101"), frame(RX, "037E00CCCCCCCCCC")]
    ch = channel(bus)
    assert ch.frames_received == 0

    ch.recv(1.0)

    assert ch.frames_received == 1


def test_receive_timeout_and_malformed_single_frame(fake_can_bus) -> None:
    bus = fake_can_bus()
    assert channel(bus).recv(0.5) is None

    bus.incoming = [frame(RX, "09" + "00" * 7)]
    with pytest.raises(ProtocolError, match="invalid single frame"):
        channel(bus).recv(0.5)


def test_st_min_decoding() -> None:
    assert decode_st_min(0x00) == 0.0
    assert decode_st_min(0x7F) == pytest.approx(0.127)
    assert decode_st_min(0xF1) == pytest.approx(0.0001)
    assert decode_st_min(0xF9) == pytest.approx(0.0009)
    for reserved in (0x80, 0xF0, 0xFA, 0xFF):
        assert decode_st_min(reserved) == pytest.approx(0.127)


def test_functional_request_collects_single_frame_answers(fake_can_bus) -> None:
    def responder(f: CanFrame):
        return [
            frame(0x7E8, "027E00CCCCCCCCCC"),
            frame(0x7E9, "027E00CCCCCCCCCC"),
            frame(0x7EA, "1014620000000000"),  # multi-frame: not collected
            frame(0x123, "027E00CCCCCCCCCC"),  # not a response id
        ]

    bus = fake_can_bus(responder)

    answers = functional_request(bus, 0x7DF, b"\x3e\x00", range(0x7E8, 0x7F0), 0.2, clock=bus.clock)

    assert answers == {0x7E8: b"\x7e\x00", 0x7E9: b"\x7e\x00"}
    assert bus.sent[0].arbitration_id == 0x7DF
    with pytest.raises(ValueError):
        functional_request(bus, 0x7DF, bytes(8), range(0x7E8, 0x7F0), 0.2, clock=bus.clock)


def test_frames_are_recorded_in_the_traffic_log(fake_can_bus) -> None:
    log = TrafficLog()
    bus = fake_can_bus(lambda f: [frame(RX, "037E00CCCCCCCCCC")])
    ch = channel(bus, traffic_log=log, adapter_name="can-sim")
    ch.send(b"\x3e\x00")
    ch.recv(1.0)

    entries = log.snapshot()
    assert [(e.direction, e.text) for e in entries] == [
        (Direction.TX, "7E0  02 3E 00 CC CC CC CC CC"),
        (Direction.RX, "7E8  03 7E 00 CC CC CC CC CC"),
    ]
    assert all(e.simulated and e.adapter == "can-sim" for e in entries)


def test_frame_validation() -> None:
    with pytest.raises(ValueError):
        CanFrame(0x800, b"")
    with pytest.raises(ValueError):
        CanFrame(0x7E0, bytes(9))
    assert CanFrame(0x18DAF110, b"\x01", is_extended=True).format() == "18DAF110  01"
