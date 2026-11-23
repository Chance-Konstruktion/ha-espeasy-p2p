"""Schalter-Befehle: genau ein Kommando je Umschalten (#2).

Der alte Weg feuerte alle Kandidaten-Kommandos per C013 Type-0 an den
Knoten, bevor der HTTP-Weg ueberhaupt den ersten Kandidaten probiert
hatte -- auf RPiEasy (das Type-0 ausfuehrt; stock ESPEasy ignoriert es)
liefen damit alle Kandidaten plus der HTTP-Gewinner: Puls-/Timer-Befehle
feuerten mehrfach, und der zweitbeste Kandidat konnte direkt gegen den
ersten arbeiten. Die Tests hier pinnen die beabsichtigte Semantik des
HTTP-Wegs (Abbruch beim ersten Erfolg) fuer beide Transportwege: P2P ist
nur der letzte Rettungsanker, wenn der Knoten per HTTP gar nicht
geantwortet hat, und dann genau ein Kandidat.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

import aiohttp

# conftest.py installiert die HA-Attrappen, bevor dieses Modul eingesammelt
# wird; espeasy_p2p_files teilt das Verzeichnis mit der Integration und
# ueberspringt deren package __init__.
from espeasy_p2p_files.switch import ESPEasyP2PSwitch
from homeassistant.exceptions import HomeAssistantError

UNIT = 5
TASK = 2
VALUE_INDEX = 0
# Dokumentations-IP (RFC 5737), wie sie auch tests/test_protocol.py nutzt.
NODE_IP = "192.0.2.7"


class _P2PAufsammler:
    """Ersetzt coordinator.send_p2p_command und merkt jedes Paket."""

    def __init__(self):
        self.sent = []

    def __call__(self, ip, command):
        self.sent.append((ip, command))
        return True


class _FakeAntwort:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeSession:
    """Ersetzt die gemeinsame aiohttp-Session; das Verhalten je Kandidat
    kommt als Funktion rein."""

    def __init__(self, verhalten):
        self.calls = []
        self._verhalten = verhalten

    def get(self, url, params=None, timeout=None):
        cmd = params.get("cmd") if params else None
        self.calls.append(cmd)
        return self._verhalten(cmd)


def _koordinator(p2p, session):
    node = SimpleNamespace(
        ip=NODE_IP,
        web_port=80,
        mac="AA:BB:CC:DD:EE:05",
        name="Test-Unit",
        node_type_name="ESP_Easy",
        build=20107,
    )
    task = SimpleNamespace(
        src_unit=UNIT,
        task_index=TASK,
        task_name="Relay",
        value_names=["State"],
        enabled=True,
    )
    return SimpleNamespace(
        nodes={UNIT: node},
        tasks={(UNIT, TASK): task},
        values={},
        get_gpio_pin=lambda unit, task_name: 4,
        get_command_template=lambda unit, task_name: "",
        send_p2p_command=p2p,
        async_schedule_resync=lambda unit: None,
    )


def _entity(p2p, session):
    coordinator = _koordinator(p2p, session)
    entity = ESPEasyP2PSwitch(
        coordinator=coordinator,
        entry_id="entry-test",
        src_unit=UNIT,
        task_index=TASK,
        value_index=VALUE_INDEX,
    )
    entity.hass = SimpleNamespace(clientsession=session)
    return entity


# Kandidaten fuer Relay/State mit bekanntem GPIO-Pin, in der Reihenfolge
# des Codes: gpio-Form, RPiEasy-Pin-freie Form, Taskname-Form.
KANDIDATEN = [
    "gpio,4,1",
    "taskvaluesetandrun,Relay,State,1",
    "Relay,1",
]


class TestSchalterBefehl(IsolatedAsyncioTestCase):
    async def test_http_beantwortet_ersten_kandidaten_p2p_schweigt(self):
        """Der Knoten beantwortet HTTP mit Erfolg: kein P2P-Paket.

        Rot vor dem Fix: der P2P-Weg schickte alle drei Kandidaten los,
        bevor HTTP einen einzigen probierte."""
        p2p = _P2PAufsammler()
        session = _FakeSession(lambda cmd: _FakeAntwort(200, "OK"))
        entity = _entity(p2p, session)

        await entity._send_command(1)

        self.assertEqual([KANDIDATEN[0]], session.calls)
        self.assertEqual([], p2p.sent)

    async def test_http_lehnt_ab_p2p_schweigt(self):
        """Der Knoten antwortet, lehnt aber jeden Kandidaten ab: auch dann
        kein P2P -- derselbe Befehls-Parser wuerde das Kommando per UDP
        genauso verwerfen, und ein erneutes Senden kann ein Befehl
        doppelt ausfuehren, dessen HTTP-Antwort nur unverstaendlich war."""
        p2p = _P2PAufsammler()
        session = _FakeSession(lambda cmd: _FakeAntwort(200, "False"))
        entity = _entity(p2p, session)

        # Ohne Erfolg meldet der Schalter das Nein als Fehler (#4).
        with self.assertRaises(HomeAssistantError):
            await entity._send_command(1)

        self.assertEqual(KANDIDATEN, session.calls)
        self.assertEqual([], p2p.sent)

    async def test_http_tot_p2p_als_letzter_anker(self):
        """HTTP nie erreichbar (Knoten lebt, Webserver nicht): genau ein
        Kommando -- der erste, hoechstpriorisierte Kandidat -- geht per
        C013 Type-0 raus. Rot vor dem Fix: alle drei Kandidaten."""
        p2p = _P2PAufsammler()

        def webserver_tot(cmd):
            raise aiohttp.ClientError("web endpoint unreachable")

        session = _FakeSession(webserver_tot)
        entity = _entity(p2p, session)

        # UDP ist unbestaetigt: der Aufrufer erfaehrt trotzdem, dass kein
        # Erfolg feststeht (#4) -- das eine P2P-Paket ist trotzdem raus.
        with self.assertRaises(HomeAssistantError):
            await entity._send_command(1)

        self.assertEqual(KANDIDATEN, session.calls)
        self.assertEqual([(NODE_IP, KANDIDATEN[0])], p2p.sent)
