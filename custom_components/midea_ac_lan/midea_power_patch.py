"""Model-specific compatibility patches for midea-local."""

from __future__ import annotations

import json
import logging
from typing import Any

from midealocal.devices.ac import DeviceAttributes as ACAttributes
from midealocal.devices.ac import MideaACDevice
from midealocal.devices.ac.message import (
    MessageNewProtocolQuery,
    MessageNewProtocolSet,
    XBXMessageBody,
    XC1MessageBody,
)
from midealocal.message import NewProtocolMessageBody

_LOGGER = logging.getLogger(__name__)

Q1D_MODEL = "00000Q1D"
Q1D_SUBTYPE = 524
Q1D_POWER_ANALYSIS_METHOD = 101
OUT_SILENT_ATTRIBUTE = "out_silent"
OUT_SILENT_TAG = 0x00CD
OUT_SILENT_VALUE = 0x03

_POWER_PATCHED = False
_OUT_SILENT_PATCHED = False
_ORIGINAL_PARSE_POWER = XC1MessageBody.parse_power
_ORIGINAL_PARSE_CONSUMPTION = XC1MessageBody.parse_consumption
_ORIGINAL_AC_INIT = MideaACDevice.__init__
_ORIGINAL_AC_SET_ATTRIBUTE = MideaACDevice.set_attribute
_ORIGINAL_NEW_PROTOCOL_QUERY_BODY = MessageNewProtocolQuery._body.fget  # noqa: SLF001
_ORIGINAL_NEW_PROTOCOL_SET_BODY = MessageNewProtocolSet._body.fget  # noqa: SLF001
_ORIGINAL_XBX_INIT = XBXMessageBody.__init__


def install_q1d_realtime_power_patch() -> None:
    """Teach midea-local the Q1D/524 mixed energy format.

    The appliance reports energy counters as BCD, but realtime power as a
    24-bit big-endian integer in 0.1 W units. The upstream midea-local
    power_analysis_method applies one format to both fields, so this fork uses
    a private method id and preserves the normal parser for every other device.
    """
    global _POWER_PATCHED  # noqa: PLW0603

    if _POWER_PATCHED:
        return

    def parse_power(
        _cls: type[XC1MessageBody],
        analysis_method: int,
        databytes: bytearray,
    ) -> float:
        if analysis_method == Q1D_POWER_ANALYSIS_METHOD:
            return int.from_bytes(bytes(databytes), "big") / 10
        return _ORIGINAL_PARSE_POWER(analysis_method, databytes)

    def parse_consumption(
        _cls: type[XC1MessageBody],
        analysis_method: int,
        databytes: bytearray,
    ) -> float:
        if analysis_method == Q1D_POWER_ANALYSIS_METHOD:
            return _ORIGINAL_PARSE_CONSUMPTION(1, databytes)
        return _ORIGINAL_PARSE_CONSUMPTION(analysis_method, databytes)

    XC1MessageBody.parse_power = classmethod(parse_power)  # type: ignore[method-assign,assignment]
    XC1MessageBody.parse_consumption = classmethod(parse_consumption)  # type: ignore[method-assign,assignment]
    _POWER_PATCHED = True


def install_out_silent_patch() -> None:
    """Backport midea-local outdoor silent mode until it is released.

    midea-local has upstream support merged for this AC new-protocol attribute,
    but midea_ac_lan still pins a release without it. This local fork keeps the
    patch self-contained so the integration can expose the switch without
    replacing Home Assistant's installed dependency.
    """
    global _OUT_SILENT_PATCHED  # noqa: PLW0603

    if _OUT_SILENT_PATCHED or hasattr(ACAttributes, OUT_SILENT_ATTRIBUTE):
        return

    def ac_init(self: MideaACDevice, *args: object, **kwargs: object) -> None:
        _ORIGINAL_AC_INIT(self, *args, **kwargs)
        ensure_out_silent_attribute(self)

    def set_attribute(
        self: MideaACDevice,
        attr: str,
        value: bool | int | str,
    ) -> None:
        if str(attr) == OUT_SILENT_ATTRIBUTE:
            message = self.make_newprotocol_message_set(attr=attr, value=value)
            self.build_send(message)
            return
        _ORIGINAL_AC_SET_ATTRIBUTE(self, attr, value)

    def query_body(self: MessageNewProtocolQuery) -> bytearray:
        if _ORIGINAL_NEW_PROTOCOL_QUERY_BODY is None:
            return bytearray()
        body = bytearray(_ORIGINAL_NEW_PROTOCOL_QUERY_BODY(self))
        out_silent_pair = bytearray([OUT_SILENT_TAG & 0xFF, OUT_SILENT_TAG >> 8])
        if out_silent_pair not in [body[i : i + 2] for i in range(1, len(body), 2)]:
            body[0] += 1
            body.extend(out_silent_pair)
        return body

    def set_body(self: MessageNewProtocolSet) -> bytearray:
        if _ORIGINAL_NEW_PROTOCOL_SET_BODY is None:
            return bytearray()
        body = bytearray(_ORIGINAL_NEW_PROTOCOL_SET_BODY(self))
        out_silent = getattr(self, OUT_SILENT_ATTRIBUTE, None)
        if out_silent is not None:
            body[0] += 1
            body.extend(
                NewProtocolMessageBody.pack(
                    param=OUT_SILENT_TAG,
                    value=bytearray([OUT_SILENT_VALUE if out_silent else 0x00]),
                ),
            )
        return body

    def xbx_init(self: XBXMessageBody, body: bytearray, bt: int) -> None:
        _ORIGINAL_XBX_INIT(self, body, bt)
        params = self.parse()
        out_silent = params.get(OUT_SILENT_TAG)
        if out_silent:
            self.out_silent = out_silent[0] == OUT_SILENT_VALUE

    MideaACDevice.__init__ = ac_init  # type: ignore[method-assign]
    MideaACDevice.set_attribute = set_attribute  # type: ignore[method-assign]
    MessageNewProtocolQuery._body = property(query_body)  # type: ignore[method-assign]  # noqa: SLF001
    MessageNewProtocolSet._body = property(set_body)  # type: ignore[method-assign]  # noqa: SLF001
    XBXMessageBody.__init__ = xbx_init  # type: ignore[method-assign]
    _OUT_SILENT_PATCHED = True


def ensure_out_silent_attribute(device: Any) -> None:  # noqa: ANN401
    """Ensure AC devices expose a storage slot for the local out_silent switch."""
    attributes = getattr(device, "_attributes", None)
    if isinstance(attributes, dict) and OUT_SILENT_ATTRIBUTE not in attributes:
        attributes[OUT_SILENT_ATTRIBUTE] = False


def apply_q1d_power_customize(customize: str, model: str, subtype: int) -> str:
    """Return customize JSON with the Q1D realtime power parser enabled.

    Returns:
        The original or updated customize JSON string.

    """
    try:
        subtype_int = int(subtype)
    except (TypeError, ValueError):
        return customize

    if model != Q1D_MODEL or subtype_int != Q1D_SUBTYPE:
        return customize

    try:
        params: Any = json.loads(customize) if customize else {}
    except json.JSONDecodeError:
        _LOGGER.warning(
            "Cannot apply Q1D power parser because customize is not valid JSON",
        )
        return customize

    if not isinstance(params, dict):
        _LOGGER.warning(
            "Cannot apply Q1D power parser because customize is not a JSON object",
        )
        return customize

    if params.get("power_analysis_method") == Q1D_POWER_ANALYSIS_METHOD:
        return customize

    if "power_analysis_method" in params:
        _LOGGER.info(
            "Overriding power_analysis_method=%s for Midea model %s subtype %s",
            params["power_analysis_method"],
            model,
            subtype,
        )

    params["power_analysis_method"] = Q1D_POWER_ANALYSIS_METHOD
    return json.dumps(params, separators=(",", ":"))
