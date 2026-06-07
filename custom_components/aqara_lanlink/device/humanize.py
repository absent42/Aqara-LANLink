"""PascalCase -> human-readable entity-name transform.

The V3 catalogue ships trait names in PascalCase exactly as Aqara
publishes them (e.g. ``OnOff``, ``CurrentLevel``, ``ColorTemperature``).
HA renders these as the entity sub-name when ``_attr_has_entity_name``
is True, so a user sees "My Bulb OnOff" rather than "My Bulb On off".

The transform splits PascalCase into space-separated words, lowercases
all-but-the-first, and preserves runs of 2+ uppercase letters as
acronyms (so ``URLPath`` becomes ``URL path``, not ``Url path``).
``TraitSpec.name`` is left untouched -- only the descriptor-sink form
that surfaces in the UI is rewritten. See ``build_descriptors``.
"""
from __future__ import annotations

import re

# Order of alternatives matters:
#   1. ``[A-Z]+(?=[A-Z][a-z])`` -- a run of caps followed by a TitleCase
#      word, e.g. ``URL`` in ``URLPath`` (acronym before next word).
#   2. ``[A-Z]+(?![a-z])``      -- a trailing all-caps run, e.g. ``RSSI``
#      at the end of ``WiFiRSSI`` (acronym at end-of-string).
#   3. ``[A-Z][a-z]*``          -- a normal TitleCase word.
#   4. ``[a-z]+``               -- already-lowercase run (idempotency on
#      already-humanized inputs).
#   5. ``\d+``                  -- digit runs (e.g. ``2`` in ``IPv4``).
_TOKEN_RE = re.compile(
    r'[A-Z]+(?=[A-Z][a-z])|[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+'
)


def humanize_name(s: str) -> str:
    """Turn a PascalCase trait name into a human-readable display name.

    Examples (from this codebase's actual trait surface)::

        humanize_name("OnOff")             -> "On off"
        humanize_name("CurrentLevel")      -> "Current level"
        humanize_name("ColorTemperature")  -> "Color temperature"
        humanize_name("BatPercentRemaining") -> "Bat percent remaining"
        humanize_name("HubConnectionStatus") -> "Hub connection status"
        humanize_name("URLPath")           -> "URL path"
        humanize_name("CameraRTSPURL")     -> "Camera RTSPURL"

    Rules:
      - Any all-uppercase token is treated as an acronym and kept as-is.
        That covers multi-letter acronyms (``URL``, ``RSSI``, ``RGB``)
        AND single-letter labels that the regex isolates as their own
        token, e.g. ``CurrentR`` -> [``Current``, ``R``] -> ``Current R``
        (color-channel labels R/G/B/X/Y).
      - The first token is capitalized; subsequent non-acronym tokens
        are lowercased.
      - Idempotent: ``humanize_name(humanize_name(x)) == humanize_name(x)``
        for any input.
      - Empty strings pass through unchanged.

    Adjacent acronyms in a single PascalCase run (e.g. ``RTSPURL``)
    cannot be split without a dictionary, so they're emitted as a single
    token. Models that need a custom split should override the
    descriptor name directly.
    """
    if not s:
        return s
    tokens = _TOKEN_RE.findall(s)
    if not tokens:
        return s
    out: list[str] = []
    for i, tok in enumerate(tokens):
        if tok.isupper() and tok.isalpha():
            out.append(tok)  # acronym -- preserve casing
        elif i == 0:
            out.append(tok[0].upper() + tok[1:].lower())
        else:
            out.append(tok.lower())
    return " ".join(out)
