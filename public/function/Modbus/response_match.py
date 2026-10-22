"""Request/response checks used by safety-critical UFC shutdown commands.

The serial layer has already checked the Modbus CRC before these checks run.
"""


def matches_write_single_coil_response(request: bytes, response: bytes) -> bool:
    """Function 05 must echo the exact eight-byte request (including CRC)."""
    return (
        isinstance(request, bytes)
        and isinstance(response, bytes)
        and len(request) == 8
        and request[1] == 5
        and response == request
    )


def matches_ufc_status_response(response: bytes) -> bool:
    """UFC's 12-coil status read must return two data bytes from slave 02."""
    return (
        isinstance(response, bytes)
        and len(response) == 7
        and response[:3] == b"\x02\x01\x02"
    )
