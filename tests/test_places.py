"""Tests for places — the rules that make the map trustworthy, not just the happy path."""
import json

import pytest

from src.agents.place_classifier import Placement
from src.places import (
    Place, build, classify, from_to_be_eaten, geocode, links, render, summary, travel_times,
)

HOME = (-0.0886, 51.5134)


class FakeHttp:
    def __init__(self, answers=None, raise_on=None):
        self.answers = answers or {}
        self.raise_on = raise_on
        self.gets, self.posts = [], []

    def get(self, url, params):
        self.gets.append(params)
        if self.raise_on == "get":
            raise ConnectionError("down")
        return self.answers.get(params["q"], [])

    def post(self, url, body):
        self.posts.append(body)
        if self.raise_on == "post":
            raise ConnectionError("down")
        n = len(body["targets"])
        base = 600 if body["costing"] == "pedestrian" else 240
        return {"sources_to_targets": [[{"time": base + 60 * i} for i in range(n)]]}


def venue(name, lon, lat, cls="amenity"):
    return {"name": name.split(",")[0], "display_name": name, "lon": str(lon), "lat": str(lat), "category": cls}


def area(name, lon, lat, kind, size_km):
    d = size_km / 111 / 2
    return {"display_name": name, "lon": str(lon), "lat": str(lat), "addresstype": kind,
            "boundingbox": [str(lat - d), str(lat + d), str(lon - d), str(lon + d)]}


class TestToBeEaten:
    def test_reads_unticked_lines_and_splits_venue_from_area(self, tmp_path):
        f = tmp_path / "To Be Eaten.md"
        f.write_text("---\ntitle: x\n---\n- [ ] Noodles Inn, Chinatown\n- [x] Eaten Already, Soho\n"
                     "- [ ] Tonkatsu, Clapham Junction\n- [ ] Just A Name\nsome prose\n")
        got = from_to_be_eaten(f)
        assert [(p.venue, p.area) for p in got] == [
            ("Noodles Inn", "Chinatown"), ("Tonkatsu", "Clapham Junction"), ("Just A Name", None)]
        assert all(p.category == "food" and p.source == "To Be Eaten" for p in got)

    def test_missing_file_is_empty_not_an_error(self, tmp_path):
        assert from_to_be_eaten(tmp_path / "nope.md") == []


class TestGeocode:
    def test_named_venue_is_pinned(self):
        http = FakeHttp({"Dishoom, London": [venue("Dishoom, Shoreditch", -0.0778, 51.5244)]})
        p = Place("food", "Dishoom", "Shoreditch")
        geocode(p, http, city="London", country="gb")
        assert p.point == (-0.0778, 51.5244) and p.note is None

    def test_a_road_with_the_venue_name_is_a_miss_not_a_pin(self):
        """'Fabric' is also a street. A confident wrong pin is worse than no pin."""
        http = FakeHttp({"Fabric, London": [venue("Fabric Road", -0.1, 51.5, cls="highway")]})
        p = Place("nightlife", "Fabric")
        geocode(p, http, city="London", country="gb")
        assert p.point is None and "couldn't find" in p.note

    def test_unknown_area_still_pins_the_venue(self):
        """'East London' is not a place Nominatim knows; that must not lose the pub."""
        http = FakeHttp({"The Royal Oak, London": [venue("The Royal Oak", -0.06, 51.53)]})
        p = Place("pub", "The Royal Oak", "East London")
        geocode(p, http, city="London", country="gb")
        assert p.point == (-0.06, 51.53)

    def test_a_different_venue_sharing_a_word_is_rejected(self):
        """Found live: 'Noodles Inn, Chinatown' came back as Meeting Noodles, King's Cross."""
        http = FakeHttp({"Noodles Inn, London": [venue("Meeting Noodles, Grays Inn Road", -0.12, 51.53)]})
        p = Place("food", "Noodles Inn", "Chinatown")
        geocode(p, http, city="London", country="gb")
        assert p.point is None and "couldn't find" in p.note

    def test_the_stated_area_picks_the_branch(self):
        """Found live: Master Wei, Chinatown was pinned to Hammersmith, listed first."""
        http = FakeHttp({
            "Master Wei, London": [venue("Master Wei Xi'An, Hammersmith Road", -0.218, 51.494),
                                   venue("Master Wei, Cosmo Place, Bloomsbury", -0.122, 51.522)],
            "Chinatown, London": [area("Chinatown", -0.131, 51.511, "suburb", 0.3)],
        })
        p = Place("food", "Master Wei", "Chinatown")
        geocode(p, http, city="London", country="gb")
        assert p.point == (-0.122, 51.522) and p.ambiguous == 2

    def test_an_area_hint_that_is_not_an_area_is_ignored(self):
        """Found live: 'East London' resolved to the University of East London and dragged the
        Columbia Road Royal Oak to one in Barking, 137 minutes away."""
        http = FakeHttp({
            "The Royal Oak, London": [venue("The Royal Oak, Longbridge Road, Barking", 0.09, 51.53),
                                      venue("The Royal Oak, Columbia Road", -0.07, 51.529)],
            "East London, London": [area("University of East London", 0.065, 51.508, "amenity", 0.5)],
        })
        p = Place("pub", "The Royal Oak", "East London")
        geocode(p, http, city="London", country="gb", home=HOME)
        assert p.point == (-0.07, 51.529), "the university is not an area; nearest to home wins"

    def test_a_region_is_too_vague_to_choose_a_branch(self):
        http = FakeHttp({
            "X, London": [venue("X, far", 0.09, 51.53), venue("X, near", -0.08, 51.51)],
            "South, London": [area("South", 0.09, 51.53, "borough", 20)],
        })
        p = Place("pub", "X", "South")
        geocode(p, http, city="London", country="gb", home=HOME)
        assert p.point == (-0.08, 51.51)

    def test_names_match_on_words_not_spelling(self):
        from src.places import same_name
        assert same_name("Master Wei", "Master Wei Xi'An") and same_name("BAO Soho", "Bao")
        assert same_name("The Royal Oak", "Royal Oak") and same_name("sketch", "Sketch")
        assert not same_name("Noodles Inn", "Meeting Noodles") and not same_name("Fabric", "")

    def test_same_name_far_apart_is_pinned_but_flagged(self):
        http = FakeHttp({"The Royal Oak, London": [
            venue("The Royal Oak, Columbia Road", -0.06, 51.53), venue("The Royal Oak, Tooting", -0.20, 51.45),
            venue("The Royal Oak, Walthamstow", 0.05, 51.60), venue("The Royal Oak, Columbia Road", -0.0601, 51.5301)]})
        p = Place("pub", "The Royal Oak")
        geocode(p, http, city="London", country="gb")
        assert p.point == (-0.06, 51.53) and p.ambiguous == 3, "3 distinct pubs; the duplicate is not one"

    def test_one_match_is_not_ambiguous(self):
        http = FakeHttp({"BAO Soho, London": [venue("BAO", -0.137, 51.513)]})
        p = Place("food", "BAO Soho", "Soho")
        geocode(p, http, city="London", country="gb")
        assert p.ambiguous == 0

    def test_search_is_bounded_to_the_region_when_home_is_known(self):
        http = FakeHttp()
        geocode(Place("food", "X"), http, city="London", country="gb", home=HOME, region_km=30)
        assert http.gets[0]["bounded"] == 1 and "viewbox" in http.gets[0]

    def test_no_venue_and_geocoder_down_both_say_why(self):
        p = Place("activity", None)
        geocode(p, FakeHttp(), city="London", country="gb")
        assert p.note == "no venue named"
        q = Place("food", "Dishoom")
        geocode(q, FakeHttp(raise_on="get"), city="London", country="gb")
        assert q.point is None and "unavailable" in q.note


class TestBuild:
    def test_travel_and_other_are_never_geocoded_onto_the_local_map(self):
        http = FakeHttp({"Dishoom, London": [venue("Dishoom", -0.07, 51.52)]})
        places = [Place("food", "Dishoom"), Place("travel", None, "Lisbon"), Place("other")]
        build(places, http, city="London", country="gb")
        assert len(http.gets) == 1, "only the local venue was looked up"
        s = summary(places)
        assert [p.venue for p in s["placed"]] == ["Dishoom"]
        assert [p.area for p in s["travel"]] == ["Lisbon"] and s["other"] == 1

    def test_walk_and_ride_times_come_from_one_batched_call_each(self):
        places = [Place("food", "a", point=(-0.07, 51.52)), Place("pub", "b", point=(-0.06, 51.53)),
                  Place("pub", "c")]
        http = FakeHttp()
        travel_times(places, HOME, http)
        assert len(http.posts) == 2 and {b["costing"] for b in http.posts} == {"pedestrian", "bicycle"}
        assert (places[0].walk_s, places[0].bike_s, places[1].walk_s) == (600, 240, 660)
        assert places[2].walk_s is None, "an unplaced venue gets no time"

    def test_routing_down_still_produces_a_map(self):
        places = [Place("food", "a", point=(-0.07, 51.52))]
        travel_times(places, HOME, FakeHttp(raise_on="post"))
        assert places[0].walk_s is None and places[0].point is not None


class TestLinksAndRender:
    def test_directions_links_only_exist_for_a_placed_venue(self):
        placed = links(Place("food", "Dishoom", "Shoreditch", point=(-0.0778, 51.5244), url="https://ig/p/1"))
        assert "query=Dishoom%20Shoreditch" in placed["google"]
        assert "ll=51.5244,-0.0778" in placed["apple"] and "endcoord=51.5244,-0.0778" in placed["citymapper"]
        assert placed["source"] == "https://ig/p/1"
        unplaced = links(Place("food", "Dishoom"))
        assert "apple" not in unplaced and "citymapper" not in unplaced, "no pin, no directions"

    def test_a_caption_cannot_break_out_of_the_page(self):
        evil = Place("food", "X", text="</script><script>alert(1)</script>", point=(-0.07, 51.52))
        page = render([evil])
        assert "alert(1)" in page, "the caption is still there, as data"
        assert "<script>alert" not in page and "</script><script>" not in page
        own = render([]).count("<script")
        assert page.count("<script") == own and page.count("</script>") == own, "only the page's own tags"

    def test_the_map_has_a_view_before_any_marker_is_added(self):
        """Found by screenshot: Leaflet threw on the first marker because fitBounds came after
        the loop, so the page showed a map with no pins, no filters and no lists."""
        page = render([Place("food", "X", point=(-0.07, 51.52))], home=(-0.089, 51.518))
        script = page[page.index("const map"):]
        assert script.index("fitBounds") < script.index("circleMarker")

    def test_page_never_uses_the_osm_tile_servers(self):
        """They require a Referer; a page opened from disk sends none and gets blocked."""
        page = render([Place("food", "X", point=(-0.07, 51.52))])
        assert "tile.openstreetmap.org" not in page
        assert "tiles.openfreemap.org" in page and "OpenStreetMap" in page, "attribution kept"

    def test_page_carries_every_section(self):
        places = [Place("food", "Dishoom", point=(-0.07, 51.52)), Place("pub", "Nowhere", note="couldn't find it"),
                  Place("travel", None, "Lisbon", text="rooftop"), Place("other"), Place("unsorted", text="?")]
        data = json.loads(render(places).split("const D = ", 1)[1].split(";\nconst el", 1)[0])
        assert len(data["placed"]) == 1 and data["other"] == 1 and len(data["travel"]) == 1
        assert {p["venue"] or p["text"] for p in data["unplaced"]} == {"Nowhere", "?"}, \
            "unplaced and unsorted are both listed, never silently dropped"


class TestClassifyCache:
    def test_results_are_cached_and_failures_retried(self, tmp_path):
        calls = []

        class Fake:
            def classify(self, texts):
                calls.append(list(texts))
                return [Placement("food", "Dishoom") if "daal" in t else Placement("unsorted") for t in texts]

        items = [{"text": "dishoom daal", "url": "u1"}, {"text": "mystery", "url": "u2"}]
        first = classify(items, Fake(), tmp_path)
        second = classify(items, Fake(), tmp_path)
        assert [p.category for p in first] == ["food", "unsorted"]
        assert calls == [["dishoom daal", "mystery"], ["mystery"]], "a success is not paid for twice; a failure is retried"
        assert second[0].url == "u1" and second[0].venue == "Dishoom"


class TestSharedLocations:
    def write(self, tmp_path, body):
        f = tmp_path / "locations.toml"
        f.write_text(body)
        return f

    def test_nothing_set_uses_the_shared_origin(self, tmp_path):
        from src.places import resolve_point
        f = self.write(tmp_path, 'origin = "moorgate"\n[locations]\nmoorgate = [-0.08906, 51.51825]\n')
        assert resolve_point(None, f) == (-0.08906, 51.51825)

    def test_a_name_and_explicit_coordinates(self, tmp_path):
        from src.places import resolve_point
        f = self.write(tmp_path, 'origin = "a"\n[locations]\na = [1, 2]\nb = [3, 4]\n')
        assert resolve_point("b", f) == (3.0, 4.0)
        assert resolve_point([5, 6], f) == (5.0, 6.0), "coordinates override the file"

    def test_no_file_means_no_times_not_a_guess(self, tmp_path):
        from src.places import resolve_point
        assert resolve_point(None, tmp_path / "missing.toml") is None

    def test_a_named_location_that_does_not_exist_is_an_error(self, tmp_path):
        """A typo must fail loudly, not silently measure everything from nowhere."""
        from src.places import resolve_point
        f = self.write(tmp_path, '[locations]\nmoorgate = [1, 2]\n')
        with pytest.raises(ValueError, match="moorgte"):
            resolve_point("moorgte", f)
        with pytest.raises(ValueError):
            resolve_point("moorgate", tmp_path / "missing.toml")
