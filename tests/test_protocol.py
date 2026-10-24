"""Der C013-Draht: Dekodierer und Paketbauer gegen kaputte und extreme Eingaben.

Diese Suite braucht kein Home Assistant -- ``protocol.py`` importiert nur
``const``. Genau deshalb gehoert sie in ``tests/``: Alles, was ueber UDP
hereinkommt, ist feindlich. Ein Zug im Netzwerk sendet Muell, ein
kaputtes Geraet bricht das Paket ab, neue Firmware haengt Bytes an.
Der Dekodierer darf darauf niemals abuersten -- ein unbehandelter Fehler
in ``datagram_received`` toetet bei asyncio den Empfang und damit die
ganze Integration, ohne dass ein Log-Zeile auf die Ursache zeigt.
"""

from __future__ import annotations

import struct

import pytest

from espeasy_p2p_files import protocol  # noqa: E402  (conftest baut das Paket)
from espeasy_p2p_files.const import (
    PACKET_HEADER,
    PACKET_TYPE_INFO,
    PACKET_TYPE_SENSOR_CONFIG,
    PACKET_TYPE_SENSOR_DATA,
    PACKET_TYPE_SENSOR_DATA_EXT,
)


class Aufsammler:
    """Nimmt alles entgegen, was der Dekodierer herausholt."""

    def __init__(self) -> None:
        self.nodes: list[protocol.NodeInfo] = []
        self.tasks: list[protocol.TaskConfig] = []
        self.werte: list[protocol.TaskValues] = []

    def als_protokoll(self) -> protocol.ESPEasyP2PProtocol:
        return protocol.ESPEasyP2PProtocol(
            on_node=self.nodes.append,
            on_task=self.tasks.append,
            on_values=self.werte.append,
        )


# ── Muell auf dem Draht ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "daten",
    [
        b"",
        b"\xff",
        b"\x00\x00",
        b"\x00\x01",  # falsches Header-Byte, sonst gueltiger Aufbau
        b"\xff\x02",  # gueltiger Kopf, unbekannter Pakettyp
        b"\xff\x01\x00\x01",  # Typ 1, aber zu kurz fuer INFO_STRUCT
        b"\xff\x03" + b"\x00" * 20,  # Typ 3, aber unter der Mindestlaenge
        b"\xff\x05" + b"\x00" * 10,  # Typ 5, aber unter den 20 Bytes
    ],
)
def test_kaputte_dgramm_fuehren_zu_nichts(daten):
    """Muell darf niemanden wecken und niemanden umbringen."""
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(daten, ("10.0.0.1", 8266))
    assert s.nodes == [] and s.tasks == [] and s.werte == []


def test_fehler_im_callback_werden_nicht_abgefangen_auch_nicht_erwartet():
    """struct.error/ValueError werden vom Dekodierer geschluckt -- ein
    TypeError im eigenen Callback ist eine andere Klasse von kaputt und
    fliegt bewusst durch (der Protokolllayer kennt die Callbacks nicht)."""
    aufsammler = Aufsammler()
    p = aufsammler.als_protokoll()

    def explodiert(node):
        raise TypeError("Kaputter Callback")

    p._on_node = explodiert
    paket = protocol.build_info_packet(unit=1, name="x", ip="1.2.3.4", web_port=80)
    with pytest.raises(TypeError):
        p.datagram_received(paket, ("10.0.0.1", 8266))


def test_error_received_wird_nur_protokolliert():
    """UDP-Fehler (z.B. ICMP port unreachable als Rueckkopplung) toeten
    den Empfang nicht."""
    s = Aufsammler()
    p = s.als_protokoll()
    p.error_received(OSError("refused"))
    p.connection_made(None)
    assert p.transport is None


def test_langer_muell_mit_gueltigem_typ_wird_gedeutet():
    """C013 hat keine Pruefsumme: 43 passende Bytes mit Kopf und Typ 1
    werden als Knoten gedeutet, auch wenn sie Muell sind. Das ist
    Protokoll-Realitaet, kein Fehler -- der Dekodierer vertraut dem
    Typbyte. Dokumentiert, damit niemand hier spaeter eine Validierung
    'repariert' und dabei legitime Firmware aussperrt."""
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(b"\xff\x01" + b"\xff" * 200, ("10.0.0.1", 8266))
    assert len(s.nodes) == 1
    assert s.nodes[0].unit == 255


# ── Typ 1: Knoteninfo ─────────────────────────────────────────────────


def test_info_paket_rundreise():
    """Was build_info_packet baut, liest der Dekodierer wieder -- so
    sieht HA im Netz aus und wie die Gegenseite es empfaengt."""
    paket = protocol.build_info_packet(
        unit=7, name="Keller", ip="192.0.2.7", web_port=80, build=20107, node_type=5
    )
    assert paket[0] == PACKET_HEADER and paket[1] == PACKET_TYPE_INFO

    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(paket, ("192.0.2.7", 8266))

    assert len(s.nodes) == 1
    knoten = s.nodes[0]
    assert knoten.unit == 7
    assert knoten.name == "Keller"
    assert knoten.ip == "192.0.2.7"
    assert knoten.build == 20107
    assert knoten.node_type == 5
    assert knoten.web_port == 80
    # MAC ist im gebauten Paket genullt: 00:00:... ist "keine Angabe".
    assert knoten.mac == "00:00:00:00:00:00"


def test_leerer_name_bekommt_einheiten_rückfall():
    """Ein Node ohne Namen ist trotzdem adressierbar -- sonst haengt
    spaeter eine Entitaet ohne Bezeichner in der Oberflaeche."""
    daten = bytearray(protocol.build_info_packet(unit=4, name="", ip="1.2.3.4", web_port=80))
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(bytes(daten), ("10.9.9.9", 8266))
    assert s.nodes[0].name == "unit-4"


def test_name_endet_beim_nullbyte():
    """Das Namensfeld ist 25 Bytes, aufgefuellt mit Nullen. Alles hinter
    dem ersten Nullbyte ist Muell und darf nicht im Namen landen."""
    daten = bytearray(protocol.build_info_packet(unit=1, name="ab", ip="1.2.3.4", web_port=80))
    # Muell hinter dem Nullterminator:
    daten[18:25] = b"MUELLMU"
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(bytes(daten), ("10.0.0.1", 8266))
    assert s.nodes[0].name == "ab"


def test_name_mit_umlauten_und_kaputten_bytes():
    """Firmware kodieren frei. Nicht dekodierbare Bytes werden ersetzt,
    nicht verworfen -- der Name ist langsamer lesbar als abwesend."""
    roh = "Küche".encode("utf-8") + b"\xff\xfe" + b"\x00" * 10
    daten = struct.pack(
        protocol.INFO_STRUCT.format,
        PACKET_HEADER, PACKET_TYPE_INFO,
        0, 0, 0, 0, 0, 0,
        10, 0, 0, 1,
        3, 1,
        roh[:25].ljust(25, b"\x00"),
        1, 80,
    )
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(daten, ("10.0.0.1", 8266))
    assert s.nodes[0].name == "Küche\ufffd\ufffd"


def test_ip_im_paket_schlaegt_absenderadresse():
    """Ein Relay hinter NAT meldet seine LAN-IP -- die zaehlt, nicht die
    des UDP-Umschlags."""
    paket = protocol.build_info_packet(unit=2, name="x", ip="192.168.4.2", web_port=80)
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(paket, ("10.10.10.10", 8266))
    assert s.nodes[0].ip == "192.168.4.2"


def test_nullip_faellt_auf_absender_zurueck():
    """Manche Firmware sendet 0.0.0.0 -- dann ist die UDP-Quelle die
    einzige Wahrheit."""
    daten = bytearray(protocol.build_info_packet(unit=2, name="x", ip="0.0.0.0", web_port=80))
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(bytes(daten), ("10.10.10.10", 8266))
    assert s.nodes[0].ip == "10.10.10.10"


def test_knotentypname_bekannt_und_unbekannt():
    bekannt = protocol.NodeInfo(unit=1, name="n", ip="1.2.3.4", mac="",
                                build=1, node_type=5, web_port=80)
    unbekannt = protocol.NodeInfo(unit=1, name="n", ip="1.2.3.4", mac="",
                                  build=1, node_type=99, web_port=80)
    assert bekannt.node_type_name == "RPiEasy"
    assert unbekannt.node_type_name == "Type 99"


def test_mac_wird_durch_doppelpunkte_gejagt():
    daten = bytearray(
        protocol.build_info_packet(unit=1, name="n", ip="1.2.3.4", web_port=80)
    )
    daten[2:8] = b"\xaa\xbb\xcc\xdd\xee\x0f"
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(bytes(daten), ("10.0.0.1", 8266))
    assert s.nodes[0].mac == "aa:bb:cc:dd:ee:0f"


# ── build_info_packet: Grenzen des Baumeisters ────────────────────────


def test_bauen_mit_kaputter_ip_nullt_die_adresse():
    """Eine ungueltige IP darf den Baumeister nicht zum Absturz bringen --
    das Paket geht trotzdem raus (mit genullter Adresse)."""
    for kaputt in ("nicht.eine.ip", "1.2.3", "1.2.3.4.5", "999.999.999.999", ""):
        paket = protocol.build_info_packet(unit=1, name="n", ip=kaputt, web_port=80)
        ip_bytes = paket[8:12]
        assert ip_bytes == b"\x00\x00\x00\x00", kaputt


def test_name_wird_auf_25_bytes_gestutzt():
    paket = protocol.build_info_packet(
        unit=1, name="x" * 40, ip="1.2.3.4", web_port=80
    )
    felder = protocol.INFO_STRUCT.unpack_from(paket)
    assert felder[14] == ("x" * 25).encode()


def test_einheiten_und_build_werden_auf_die_wirebreite_gezwickt():
    """unit ist ein Byte, build 16 Bits -- Werte darueber werden leise
    gekuerzt statt das Paket zu sprengen."""
    paket = protocol.build_info_packet(
        unit=300, name="n", ip="1.2.3.4", web_port=70000, build=70000
    )
    felder = protocol.INFO_STRUCT.unpack_from(paket)
    assert felder[12] == 300 & 0xFF
    assert felder[13] == 70000 & 0xFFFF
    assert felder[16] == 70000 & 0xFFFF


# ── Typ 3: Aufgabenkonfiguration ──────────────────────────────────────


def _baue_typ3(src_unit=5, task_index=1, device=33,
               task_name="Relais", wert_namen=("State", "Zweit", "", ""),
               anhang=b""):
    """Baut ein Typ-3-Paket von Hand: 2 Kopf + 5 Routenbytes, dann
    Taskname und 4 Wertnamen als 26-Byte-Felder, dann Firmware-Anhang."""
    daten = bytes([PACKET_HEADER, PACKET_TYPE_SENSOR_CONFIG,
                   src_unit, 0, task_index, 0, device])
    for name in (task_name, *wert_namen):
        daten += name.encode()[:26].ljust(26, b"\x00")
    return daten + anhang


def test_typ3_wird_korrekt_gelesen():
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(_baue_typ3(), ("10.0.0.1", 8266))
    assert len(s.tasks) == 1
    t = s.tasks[0]
    assert (t.src_unit, t.task_index, t.device_number) == (5, 1, 33)
    assert t.task_name == "Relais"
    assert t.value_names == ["State", "Zweit", "", ""]


def test_typ3_vertraegt_firmware_anhang():
    """Neuere ESPEasy-Builds haengen hinter den Wertnamen weitere Felder
    an (GPIO, Sync-Flags). Der Dekodierer liest feste Offsets -- genau
    dafuer ist der Test da: mit fortlaufendem Slicing wuerde der Anhang
    die Namen verschieben."""
    anhang = bytes(range(40))
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(_baue_typ3(anhang=anhang), ("10.0.0.1", 8266))
    assert s.tasks[0].task_name == "Relais"
    assert s.tasks[0].value_names == ["State", "Zweit", "", ""]


def test_typ3_grenzfaelle_leerer_taskname_und_wertnamen():
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(
        _baue_typ3(task_name="", wert_namen=("", "", "", "")), ("10.0.0.1", 8266)
    )
    # Leere Namen kommen durch -- ob daraus eine Entitaet wird, entscheidet
    # der Koordinator, nicht der Draht.
    assert s.tasks[0].task_name == ""
    assert s.tasks[0].value_names == ["", "", "", ""]


def test_typ3_namen_werden_auf_26_bytes_gestutzt():
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(
        _baue_typ3(task_name="N" * 40, wert_namen=("V" * 40, "", "", "")),
        ("10.0.0.1", 8266),
    )
    assert s.tasks[0].task_name == "N" * 26
    assert s.tasks[0].value_names[0] == "V" * 26


# ── Typ 5/6: Sensorwerte ──────────────────────────────────────────────


def _baue_typ5(ptype=PACKET_TYPE_SENSOR_DATA, src_unit=9, task_index=2,
               werte=(1.5, 2.5, 3.5, 4.5), anhang=b""):
    kopf = bytes([PACKET_HEADER, ptype, src_unit, 0, task_index, 0, 0, 0])
    return kopf + struct.pack("<4f", *werte) + anhang


@pytest.mark.parametrize("ptype", [PACKET_TYPE_SENSOR_DATA, PACKET_TYPE_SENSOR_DATA_EXT])
def test_typ5_und_6_gleich_decodiert(ptype):
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(_baue_typ5(ptype=ptype), ("192.0.2.9", 8266))
    assert len(s.werte) == 1
    w = s.werte[0]
    assert (w.src_unit, w.task_index) == (9, 2)
    assert w.values == [1.5, 2.5, 3.5, 4.5]
    assert w.src_ip == "192.0.2.9"


def test_typ5_anhaengende_bytes_ignoriert():
    """Typ 6 traegt pro Wert zusaetzliche Bytes -- die ersten 4 Floats
    bleiben die Wahrheit."""
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(_baue_typ5(anhang=b"\x01\x02\x03\x04\x05\x06"), ("10.0.0.1", 8266))
    assert s.werte[0].values == [1.5, 2.5, 3.5, 4.5]


def test_typ5_kaputte_floats_sind_kein_absturz():
    """NaN und Unendlich sind gueltige Floats auf dem Draht -- sie
    kommen durch; was die Entitaet daraus macht, ist nicht Sache des
    Dekodierers."""
    s = Aufsammler()
    p = s.als_protokoll()
    p.datagram_received(
        _baue_typ5(werte=(float("nan"), float("inf"), 0.0, -0.0)), ("10.0.0.1", 8266)
    )
    w = s.werte[0]
    assert w.values[0] != w.values[0]  # NaN bleibt NaN
    assert w.values[1] == float("inf")


# ── Typ 0: Befehlspaket bauen ─────────────────────────────────────────


def test_befehlspaket_aufbau():
    paket = protocol.build_command_packet("gpio,13,1")
    assert paket[0] == PACKET_HEADER
    assert paket[1] == 0  # PACKET_TYPE_COMMAND
    # Befehl, Nullterminator, dann Fuellung bis Byte 255.
    assert paket[2:12] == b"gpio,13,1\x00"
    assert paket[12:] == b"\x00" * 243
    assert len(paket) == 255


def test_befehlspaket_stutzt_auf_252_bytes():
    """253 Bytes Koerper, davon 1 Nullterminator -- ein laengerer Befehl
    wird auf 252 Zeichen gekuerzt, damit der Terminator Platz bleibt."""
    paket = protocol.build_command_packet("x" * 400)
    assert len(paket) == 255
    assert paket[2:255] == b"x" * 252 + b"\x00"


def test_befehlspaket_mit_umlauten():
    paket = protocol.build_command_packet("Lüfter,1")
    assert paket[2:].rstrip(b"\x00").startswith("Lüfter".encode())


# ── detect_local_ip ───────────────────────────────────────────────────


def test_detect_local_ip_bei_socketfehler_nullip(monkeypatch):
    """Ohne Routing (Container ohne Netz) ist 0.0.0.0 die ehrliche
    Antwort, kein Absturz."""

    class ExplodierenderSockel:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self, ziel):
            raise OSError("Kein Netz")

        def getsockname(self):
            return ("10.1.2.3", 0)

        def close(self):
            pass

    import espeasy_p2p_files.protocol as p

    monkeypatch.setattr(p.socket, "socket", ExplodierenderSockel)
    assert p.detect_local_ip() == "0.0.0.0"


def test_detect_local_ip_liefert_die_sockeladresse(monkeypatch):
    """Der Erfolgspfad: Der Kernel waehlt die Route, wir nehmen seine
    Antwort. Kein Paket verlaesst den Rechner (UDP-connect ist nur ein
    Routing-Lookup) -- der Fake-Sockel modelliert genau das."""

    class LokalerSockel:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self, ziel):
            assert ziel == ("10.255.255.255", 1)

        def getsockname(self):
            return ("192.168.7.10", 54321)

        def close(self):
            pass

    import espeasy_p2p_files.protocol as p

    monkeypatch.setattr(p.socket, "socket", LokalerSockel)
    assert p.detect_local_ip() == "192.168.7.10"
