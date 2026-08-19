import base64

from app.services.validators.zatca_qr import decode_tlv, vat_number_ok


def tlv(tag: int, value: str) -> bytes:
    b = value.encode("utf-8")
    return bytes([tag, len(b)]) + b


def make_payload() -> str:
    raw = (
        tlv(1, "شركة تجريبية")
        + tlv(2, "310122393500003")
        + tlv(3, "2026-06-04T10:30:00Z")
        + tlv(4, "115.00")
        + tlv(5, "15.00")
    )
    return base64.b64encode(raw).decode()


def test_decodes_valid_payload():
    result = decode_tlv(make_payload())
    assert result.valid_tlv
    assert result.fields["vat_number"] == "310122393500003"
    assert result.fields["total"] == "115.00"


def test_rejects_non_base64():
    assert not decode_tlv("not-base64!!").valid_tlv


def test_rejects_missing_tags():
    raw = tlv(1, "بائع") + tlv(2, "310122393500003")
    assert not decode_tlv(base64.b64encode(raw).decode()).valid_tlv


def test_rejects_truncated_tlv():
    raw = make_payload()
    truncated = base64.b64encode(base64.b64decode(raw)[:-3]).decode()
    assert not decode_tlv(truncated).valid_tlv


def test_vat_number_format():
    assert vat_number_ok("310122393500003")
    assert not vat_number_ok("12345")
    assert not vat_number_ok("410122393500003")  # must start with 3
    assert not vat_number_ok("310122393500001")  # must end with 3
