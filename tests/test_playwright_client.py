from __future__ import annotations

from amedas_rainfall.jma.playwright_client import JmaPlaywrightClient


class _Response:
    ok = True
    status = 200

    def text(self):
        return """
        <div class="station" title="地点名：テスト カナ：テスト 北緯：35度 30分 東経：139度 45分 標高：12m">
          <input name="stid" value="a0001">
          <input name="stname" value="テスト">
          <input name="prid" value="44">
          <input name="kansoku" value="1000">
        </div>
        """


class _Request:
    def post(self, *args, **kwargs):
        return _Response()


class _Context:
    request = _Request()


def test_station_title_parser_is_used_by_playwright_fallback():
    client = object.__new__(JmaPlaywrightClient)
    client._context = _Context()

    stations = client.fetch_stations_for_prefecture("44")

    assert len(stations) == 1
    assert stations[0].name_from_title == "テスト"
    assert stations[0].latitude == 35.5
    assert stations[0].longitude == 139.75
