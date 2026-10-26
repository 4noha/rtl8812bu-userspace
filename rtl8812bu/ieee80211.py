"""802.11 フレームの最小パーサ（IEEE 802.11 仕様に基づく独自実装）。

ここは観測ではなく規格に基づいて書いている（フレーム形式は公開仕様）。
RSSI などの無線情報は、まだ観測から確定していないので扱わない。
"""

from __future__ import annotations

import struct

TYPES = {0: "mgmt", 1: "ctrl", 2: "data", 3: "ext"}

MGMT_SUBTYPES = {
    0: "assoc-req", 1: "assoc-resp", 2: "reassoc-req", 3: "reassoc-resp",
    4: "probe-req", 5: "probe-resp", 8: "beacon", 9: "atim", 10: "disassoc",
    11: "auth", 12: "deauth", 13: "action", 14: "action-noack",
}

CTRL_SUBTYPES = {
    2: "trigger", 4: "beamforming", 5: "vht-ndp", 6: "ctrl-ext",
    7: "wrapper", 8: "bar", 9: "ba", 10: "ps-poll", 11: "rts",
    12: "cts", 13: "ack", 14: "cf-end", 15: "cf-end-ack",
}

DATA_SUBTYPES = {
    0: "data", 1: "data-cf-ack", 2: "data-cf-poll", 3: "data-cf-ack-poll",
    4: "null", 5: "cf-ack", 6: "cf-poll", 7: "cf-ack-poll",
    8: "qos-data", 12: "qos-null", 14: "qos-poll",
}


def _mac(b: bytes) -> str:
    return ":".join(f"{x:02x}" for x in b)


def parse(frame: bytes) -> dict:
    """フレームを辞書に要約する。壊れていても例外を投げない。"""
    out: dict = {"len": len(frame)}
    if len(frame) < 2:
        out["type"] = "truncated"
        return out

    fc0, fc1 = frame[0], frame[1]
    version = fc0 & 0x03
    ftype = (fc0 >> 2) & 0x03
    subtype = (fc0 >> 4) & 0x0F
    flags = fc1

    out["version"] = version
    out["type"] = TYPES.get(ftype, f"type{ftype}")
    out["subtype"] = subtype
    if ftype == 0:
        out["name"] = MGMT_SUBTYPES.get(subtype, f"mgmt-{subtype}")
    elif ftype == 1:
        out["name"] = CTRL_SUBTYPES.get(subtype, f"ctrl-{subtype}")
    elif ftype == 2:
        out["name"] = DATA_SUBTYPES.get(subtype, f"data-{subtype}")
    else:
        out["name"] = f"ext-{subtype}"

    out["to_ds"] = bool(flags & 0x01)
    out["from_ds"] = bool(flags & 0x02)
    out["retry"] = bool(flags & 0x08)
    out["protected"] = bool(flags & 0x40)
    out["order"] = bool(flags & 0x80)

    # 制御フレーム: ACK/CTS/CF-End 系は 10 バイト（RA のみ）、
    # RTS/PS-Poll/BA 系は 16 バイト（RA + TA）
    if ftype == 1:
        if len(frame) >= 10:
            out["ra"] = _mac(frame[4:10])
        if subtype in (11, 10, 8, 9, 2) and len(frame) >= 16:
            out["ta"] = _mac(frame[10:16])
        return out

    if len(frame) < 24:
        out["type"] = "truncated"
        return out

    out["duration"] = struct.unpack_from("<H", frame, 2)[0]
    a1, a2, a3 = frame[4:10], frame[10:16], frame[16:22]
    seq = struct.unpack_from("<H", frame, 22)[0]
    out["seq"] = seq >> 4
    out["frag"] = seq & 0x0F

    # ToDS/FromDS によるアドレスの意味の違い
    if not out["to_ds"] and not out["from_ds"]:
        out["da"], out["sa"], out["bssid"] = _mac(a1), _mac(a2), _mac(a3)
    elif out["to_ds"] and not out["from_ds"]:
        out["bssid"], out["sa"], out["da"] = _mac(a1), _mac(a2), _mac(a3)
    elif not out["to_ds"] and out["from_ds"]:
        out["da"], out["bssid"], out["sa"] = _mac(a1), _mac(a2), _mac(a3)
    else:
        out["ra"], out["ta"], out["da"] = _mac(a1), _mac(a2), _mac(a3)

    if ftype == 0 and subtype in (8, 5, 4, 11, 0, 1, 2, 3):
        out.update(_parse_body_mgmt(frame, subtype))
    return out


def _parse_body_mgmt(frame: bytes, subtype: int) -> dict:
    """ビーコン / プローブ / (再)アソシエーション の固定部と IE。"""
    body_off = 24
    res: dict = {}
    if subtype in (8, 5):          # beacon / probe-resp
        if len(frame) < body_off + 12:
            return res
        ts, interval, cap = struct.unpack_from("<QHH", frame, body_off)
        res["timestamp"] = ts
        res["beacon_interval"] = interval
        res["capability"] = cap
        res["ies"] = parse_ies(frame[body_off + 12:])
        ssid = res["ies"].get("ssid")
        if ssid is not None:
            res["ssid"] = ssid
    elif subtype == 4:             # probe-req
        res["ies"] = parse_ies(frame[body_off:])
        ssid = res["ies"].get("ssid")
        if ssid is not None:
            res["ssid"] = ssid
    elif subtype in (0, 2):        # assoc-req / reassoc-req
        if len(frame) >= body_off + 4:
            res["capability"] = struct.unpack_from("<H", frame, body_off)[0]
            res["listen_interval"] = struct.unpack_from("<H", frame, body_off + 2)[0]
            res["ies"] = parse_ies(frame[body_off + 4:])
            ssid = res["ies"].get("ssid")
            if ssid is not None:
                res["ssid"] = ssid
    elif subtype == 11:            # auth
        if len(frame) >= body_off + 6:
            algo, tseq, status = struct.unpack_from("<HHH", frame, body_off)
            res["auth_alg"] = algo
            res["auth_seq"] = tseq
            res["status"] = status
    return res


def parse_ies(data: bytes) -> dict:
    """情報要素（IE）を辞書にする。未知の ID は数値キーで長さだけ残す。"""
    ies: dict = {}
    i = 0
    while i + 2 <= len(data):
        eid, elen = data[i], data[i + 1]
        val = data[i + 2:i + 2 + elen]
        if len(val) < elen:
            break
        i += 2 + elen
        if eid == 0:
            ies["ssid"] = val.decode("utf-8", "replace")
        elif eid == 1:
            ies["rates"] = [r & 0x7F for r in val]
        elif eid == 3:
            ies["channel"] = val[0] if val else None
        elif eid == 48:
            ies["rsn"] = val
        elif eid == 221:
            ies.setdefault("vendor", []).append(val)
        else:
            ies.setdefault("ie", {})[eid] = len(val)
    return ies


def describe(info: dict) -> str:
    """1 行の要約。"""
    name = info.get("name", "?")
    parts = [f"{name:<12s}"]
    if "len" in info:
        parts.append(f"len={info['len']:<4d}")
    for key in ("bssid", "sa", "da", "ra", "ta"):
        if key in info:
            parts.append(f"{key}={info[key]}")
    if "ssid" in info:
        parts.append(f"ssid={info['ssid']!r}")
    if "channel" in info.get("ies", {}):
        parts.append(f"ch={info['ies']['channel']}")
    if info.get("protected"):
        parts.append("prot")
    if info.get("retry"):
        parts.append("retry")
    return " ".join(parts)
