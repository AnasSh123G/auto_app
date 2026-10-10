"""Which German federal state a job location is in (used for regional salary targets).

Only what a job ad's location line usually offers is used: a state name, a postcode, or a city. States other than
Bayern are recognised by name only; Bayern also by postcode area and by its larger cities and the Munich, Nuremberg
and Augsburg commuter belts. An unknown place gives ``None``: no regional amount is applied then.
"""

from __future__ import annotations

import re

from autoapp.discovery.normalize import norm_text

# key -> names as they appear in German and English ads (normalised: umlauts folded, punctuation as spaces)
STATES: dict[str, tuple[str, ...]] = {
    "baden-wuerttemberg": ("baden wuerttemberg", "baden wurttemberg"),
    "bayern": ("bayern", "bavaria", "freistaat bayern"),
    "berlin": ("berlin",),
    "brandenburg": ("brandenburg",),
    "bremen": ("bremen",),
    "hamburg": ("hamburg",),
    "hessen": ("hessen", "hesse"),
    "mecklenburg-vorpommern": ("mecklenburg vorpommern", "mecklenburg western pomerania"),
    "niedersachsen": ("niedersachsen", "lower saxony"),
    "nordrhein-westfalen": ("nordrhein westfalen", "north rhine westphalia", "nrw"),
    "rheinland-pfalz": ("rheinland pfalz", "rhineland palatinate"),
    "saarland": ("saarland",),
    "sachsen": ("sachsen", "saxony"),
    "sachsen-anhalt": ("sachsen anhalt", "saxony anhalt"),
    "schleswig-holstein": ("schleswig holstein",),
    "thueringen": ("thueringen", "thuringia"),
}

_BAYERN_CITIES = (
    "muenchen", "munich", "nuernberg", "nuremberg", "augsburg", "regensburg", "ingolstadt", "wuerzburg", "fuerth",
    "erlangen", "bamberg", "bayreuth", "landshut", "aschaffenburg", "kempten", "rosenheim", "neu ulm", "schweinfurt",
    "passau", "freising", "garching", "unterschleissheim", "ismaning", "aschheim", "gruenwald", "unterfoehring",
    "oberhaching", "haar", "planegg", "martinsried", "graefelfing", "starnberg", "dachau", "erding", "landsberg am lech",
    "memmingen", "kaufbeuren", "amberg", "weiden in der oberpfalz", "hof", "coburg", "straubing", "deggendorf", "ansbach",
    "schwabach", "zirndorf", "herzogenaurach", "forchheim", "lindau", "garmisch partenkirchen", "traunstein", "burghausen",
    "kulmbach", "bad reichenhall", "germering", "puchheim", "eching", "neubiberg", "ottobrunn", "taufkirchen", "karlsfeld",
    "holzkirchen", "feldkirchen", "poing", "vaterstetten", "kirchheim bei muenchen", "hallbergmoos", "oberschleissheim",
    "fuerstenfeldbruck", "olching", "penzberg", "weilheim", "ebersberg", "markt schwaben", "neufahrn", "moosburg",
    "pfaffenhofen", "neuburg an der donau", "eichstaett", "schrobenhausen", "gersthofen", "friedberg", "neusaess",
    "stadtbergen", "kissing", "roth", "lauf an der pegnitz", "nuernberger land", "eckental", "heroldsberg", "roettenbach",
    "hersbruck", "neumarkt in der oberpfalz", "cham", "schwandorf", "tirschenreuth", "marktredwitz", "selb", "wunsiedel",
    "hassfurt", "bad kissingen", "bad neustadt", "kitzingen", "ochsenfurt", "marktheidenfeld", "miltenberg", "obernburg",
    "alzenau", "grossostheim", "bad toelz", "miesbach", "rottach egern", "bad aibling", "wasserburg am inn", "muehldorf",
    "altoetting", "mettenheim", "dingolfing", "plattling", "vilsbiburg", "mainburg", "kelheim", "abensberg", "illertissen",
    "senden", "gundelfingen", "dillingen an der donau", "noerdlingen", "donauwoerth", "neuburg", "fuessen", "sonthofen",
    "immenstadt", "buchloe", "mindelheim", "bad woerishofen",
)
_BAYERN_PLZ = re.compile(r"(?<!\d)(?:8[0-7]\d{3}|9[0-7]\d{3}|88(?:13\d|14[0-7])|89231)(?!\d)")


def _names_pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    ordered = sorted(names, key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(n) for n in ordered) + r")(?![a-z0-9])")


_NAME_PATTERNS = {key: _names_pattern(names) for key, names in STATES.items()}
_BAYERN_CITY_PATTERN = _names_pattern(_BAYERN_CITIES)
# "Lower Saxony" and "Saxony-Anhalt" contain "saxony": check the longer, more specific states first.
_ORDER = sorted(STATES, key=lambda k: -max(len(n) for n in STATES[k]))


def german_state(location: str | None) -> str | None:
    """The state key (see ``STATES``) for a location line such as ``Munich, Bavaria, Germany``, else None."""
    text = norm_text(location or "")
    if not text:
        return None
    for key in _ORDER:
        if _NAME_PATTERNS[key].search(text):
            return key
    if _BAYERN_PLZ.search(location or ""):
        return "bayern"
    if _BAYERN_CITY_PATTERN.search(text):
        return "bayern"
    return None
