from harvest.gallica import GallicaClient
from harvest.inventory import (
    build_inventory, harvest_stratum, parse_sru_response, sru_url,
)

SRU_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/">
  <srw:version>1.2</srw:version>
  <srw:numberOfRecords>1234</srw:numberOfRecords>
  <srw:records>
    <srw:record>
      <srw:recordData>
        <oai_dc:dc>
          <dc:identifier>https://gallica.bnf.fr/ark:/12148/bpt6k111aaa</dc:identifier>
          <dc:title>Le Constitutionnel : journal du commerce</dc:title>
          <dc:date>1852-03-14</dc:date>
          <dc:type>fascicule</dc:type>
        </oai_dc:dc>
      </srw:recordData>
    </srw:record>
    <srw:record>
      <srw:recordData>
        <oai_dc:dc>
          <dc:identifier>https://gallica.bnf.fr/ark:/12148/bpt6k222bbb</dc:identifier>
          <dc:title>La Presse</dc:title>
          <dc:date>1861-07-02</dc:date>
          <dc:type>fascicule</dc:type>
        </oai_dc:dc>
      </srw:recordData>
    </srw:record>
  </srw:records>
</srw:searchRetrieveResponse>
"""


class FakeClient(GallicaClient):
    """Returns the fixture for any URL; counts calls."""

    def __init__(self, tmp_path):
        super().__init__(cache_dir=tmp_path, delay_s=0)
        self.calls = []

    def _get(self, url, binary=False):
        self.calls.append(url)
        return SRU_FIXTURE.encode()


def test_sru_url_encoding():
    url = sru_url('(dc.type all "fascicule")', start=51, maximum=50)
    assert url.startswith("https://gallica.bnf.fr/SRU?")
    assert "startRecord=51" in url and "maximumRecords=50" in url
    assert "%22fascicule%22" in url  # quotes urlencoded


def test_parse_sru_response():
    total, records = parse_sru_response(SRU_FIXTURE.encode())
    assert total == 1234
    assert [r["ark"] for r in records] == ["bpt6k111aaa", "bpt6k222bbb"]
    assert records[0]["date"] == "1852-03-14"
    assert records[1]["title"] == "La Presse"


def test_harvest_stratum_dedup_and_seeded(tmp_path):
    c = FakeClient(tmp_path)
    recs = harvest_stratum(c, '(dc.type all "fascicule")', n_docs=5, seed=42)
    # Fixture only ever yields 2 unique ARKs regardless of offset.
    assert sorted(r["ark"] for r in recs) == ["bpt6k111aaa", "bpt6k222bbb"]
    # First call probes numberOfRecords with maximumRecords=1.
    assert "maximumRecords=1" in c.calls[0]

    c2 = FakeClient(tmp_path)
    recs2 = harvest_stratum(c2, '(dc.type all "fascicule")', n_docs=5, seed=42)
    assert [r["ark"] for r in recs] == [r2["ark"] for r2 in recs2]  # reproducible


def test_build_inventory_rows(tmp_path):
    c = FakeClient(tmp_path)
    rows = build_inventory(
        c,
        strata=[("presse", "1821-1880", '(dc.type all "fascicule")')],
        per_stratum=2,
    )
    assert len(rows) == 2
    assert rows[0]["doctype"] == "presse" and rows[0]["period"] == "1821-1880"
    assert set(rows[0]) == {"ark", "doctype", "period", "date", "title"}
