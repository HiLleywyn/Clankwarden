"""clanklib/dehoist.py -- Clankwarden smart-dehoist detection (pure, dep-free).

Three jobs, deliberately with **no** Discord/numpy imports so the whole thing is
unit-testable under plain python:

  1. ``normalize_display`` -- fold a raw display name to a comparable ascii form
     (NFKC width fold, strip combining "zalgo" marks, fold common homoglyph
     confusables) so "Binance", "Binаnce" (cyrillic a) and fullwidth variants all
     compare equal.
  2. ``leading_hoist_run`` / ``clean_nick`` -- find and strip the leading
     punctuation/symbol run scammers use to climb the member list ("!!! FREE
     NITRO" -> "FREE NITRO"), producing the nick we rename them to.
  3. ``scan_guild_signals`` + ``classify`` -- learn what impersonation looks like
     for THIS server (staff terms + a topic lexicon templated into scam frames)
     and score a set of names against it, returning a tiered :class:`Detection`.

``cogs/clank.py`` listeners call :func:`classify` on the hot path (the cog owns
the floor-role gate, the rename, and the auto-clank); ``cogs/dehoist.py`` renders
the signals/preview panels and runs paced sweeps. ``scan_guild_signals`` accepts
any duck-typed guild (``.name``/``.description``/``.roles``/``.text_channels``/
``.members``) so tests can pass plain stand-ins.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #

# A small homoglyph table for the lookalikes scammers actually use. NFKC already
# folds fullwidth/compat forms, so this only needs the cross-script confusables.
_CONFUSABLES: dict[str, str] = {
    # Cyrillic -> Latin
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
    "х": "x", "у": "y", "и": "u", "к": "k", "м": "m",
    "н": "h", "т": "t", "в": "b",
    # Greek -> Latin
    "ο": "o", "α": "a", "ε": "e", "ρ": "p", "υ": "u",
    "κ": "k", "ι": "i", "ν": "v", "τ": "t",
    # Common digit/letter leetspeak folds
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s",
    "@": "a", "!": "i",
}

# Characters that "hoist" a name to the top of the member list -- anything that
# is not a unicode letter or digit, sitting at the FRONT of the name. We keep an
# explicit hint set for readability but the real test is ``_is_hoist_char``.
_HOIST_HINT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~•–—★"
                  "☆▲▼«»→·✱✿")

_PLACEHOLDER = "hoister"          # nick when stripping leaves nothing usable
_NICK_MAX = 32                    # Discord nickname length cap


def _is_hoist_char(ch: str) -> bool:
    """A leading char hoists if it is not alphanumeric and not whitespace-internal.

    We treat any non-alphanumeric, non-space leading character as a hoist char
    (covers ``!``, ``~``, bullets, arrows, box-drawing, leading emoji), which is
    exactly what climbs the Discord sidebar.
    """
    if ch.isspace():
        return True
    if ch.isalnum():
        return False
    return True


def leading_hoist_run(name: str) -> str:
    """Return the maximal leading run of hoisting characters (possibly empty)."""
    i = 0
    for ch in name:
        if _is_hoist_char(ch):
            i += 1
        else:
            break
    return name[:i]


def strip_zalgo(text: str) -> str:
    """Drop combining marks (the stacked-diacritic "zalgo" noise)."""
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def normalize_display(name: str) -> str:
    """Fold a display name to a lowercase ascii-ish comparison form.

    NFKC (width/compat fold) -> strip combining marks -> homoglyph fold ->
    lowercase. Non-ascii leftovers are dropped. Used for matching only; never
    shown to users.
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name)
    s = strip_zalgo(unicodedata.normalize("NFKD", s))
    s = "".join(_CONFUSABLES.get(c, c) for c in s)
    s = s.encode("ascii", "ignore").decode("ascii")
    return s.lower().strip()


def _tokens(name: str) -> set[str]:
    """Normalized word tokens, splitting CamelCase / digit / punctuation seams."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    s = normalize_display(s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return {t for t in s.split() if t}


def clean_nick(display_name: str) -> str:
    """The dehoisted nickname: drop the leading hoist run, collapse whitespace.

    Falls back to a neutral placeholder when nothing printable remains (e.g. the
    name was nothing but symbols), and clamps to Discord's 32-char nick cap.
    """
    stripped = display_name[len(leading_hoist_run(display_name)):].strip()
    stripped = re.sub(r"\s+", " ", stripped)
    # Require at least one alphanumeric char to count as a usable name.
    if not any(c.isalnum() for c in stripped):
        return _PLACEHOLDER
    return stripped[:_NICK_MAX]


# --------------------------------------------------------------------------- #
# Per-guild signal scanning
# --------------------------------------------------------------------------- #

# Topic seed lexicon: detected keyword -> topic bucket. Drives which scam frames
# we synthesize for a given server.
TOPIC_SEEDS: dict[str, tuple[str, ...]] = {
    "crypto": ("crypto", "bitcoin", "btc", "eth", "ethereum", "web3", "defi",
               "wallet", "nft", "token", "coin", "blockchain", "binance",
               "coinbase", "metamask", "ledger", "solana", "airdrop", "staking"),
    "investing": ("invest", "investing", "stocks", "stock", "trading", "trader",
                  "forex", "portfolio", "market", "options", "futures"),
    "gaming": ("gaming", "game", "gamer", "steam", "valorant", "fortnite",
               "roblox", "minecraft", "league", "csgo", "fps"),
    "art": ("art", "artist", "design", "nsfw", "commission", "commissions"),
    "tech": ("tech", "dev", "developer", "coding", "programming", "software",
             "it", "sysadmin", "linux"),
    "community": ("community", "chat", "hangout", "social", "friends"),
}

# Generic authority/impersonation words a scam handle leans on, independent of
# topic. Combined with brand/topic terms to synthesize the lexicon.
_AUTHORITY = ("support", "help", "helpdesk", "team", "staff", "admin", "mod",
              "moderator", "official", "service", "ticket", "tickets", "system",
              "security", "verify", "verification", "giveaway", "airdrop",
              "rewards", "claim", "bot", "mail", "modmail", "announcement")

# Words that, standing alone as a display name, are themselves impersonation
# (no brand needed -- "Support", "Admin", "Moderator", "Ticket Tool").
_SOLO_AUTHORITY = frozenset({
    "support", "admin", "administrator", "moderator", "staff", "helpdesk",
    "modmail", "ticket", "tickets", " tickettool".strip(), "tickettool",
    "system", "security", "official", "verification", "verify", "giveaway",
    "airdrop", "server support", "mod team", "support team", "help desk",
})

_STAFF_ROLE_HINTS = ("admin", "administrator", "mod", "moderator", "staff",
                     "owner", "support", "team", "helper", "council", "head")


@dataclass(frozen=True)
class GuildSignals:
    """What impersonation looks like for one server."""
    topics: frozenset[str] = frozenset()
    brand_terms: frozenset[str] = frozenset()
    staff_terms: frozenset[str] = frozenset()
    lexicon: frozenset[str] = frozenset()      # synthesized impersonation phrases

    def is_empty(self) -> bool:
        return not (self.lexicon or self.staff_terms)


def _text_attr(obj: object, *names: str) -> str:
    for n in names:
        v = getattr(obj, n, None)
        if isinstance(v, str) and v:
            return v
    return ""


def scan_guild_signals(guild: object, *, extra_topics: Iterable[str] = ()) -> GuildSignals:
    """Derive per-guild dehoist signals from a (duck-typed) guild.

    Reads ``guild.name``, ``guild.description``, role names, channel names and
    channel topics, plus the display tokens of members holding staff-ish roles.
    Returns the staff terms, detected topics, brand terms and the synthesized
    impersonation ``lexicon`` to match names against.
    """
    blob_tokens: set[str] = set()
    blob_tokens |= _tokens(_text_attr(guild, "name"))
    blob_tokens |= _tokens(_text_attr(guild, "description"))

    staff_terms: set[str] = set()
    for role in getattr(guild, "roles", []) or []:
        rname = _text_attr(role, "name")
        if not rname or rname == "@everyone":
            continue
        blob_tokens |= _tokens(rname)
        low = normalize_display(rname)
        if any(h in low for h in _STAFF_ROLE_HINTS):
            staff_terms.add(low)
            staff_terms |= _tokens(rname)

    for ch in getattr(guild, "text_channels", []) or []:
        blob_tokens |= _tokens(_text_attr(ch, "name"))
        blob_tokens |= _tokens(_text_attr(ch, "topic"))

    # Brand/topic terms: any seed keyword present in the server's vocabulary.
    topics: set[str] = set(t for t in extra_topics if t)
    brand_terms: set[str] = set()
    for topic, seeds in TOPIC_SEEDS.items():
        present = blob_tokens.intersection(seeds)
        if present:
            topics.add(topic)
            brand_terms |= present

    # Synthesize the impersonation lexicon: solo authority words, brand x
    # authority frames, and staff-term x authority frames.
    lexicon: set[str] = set(_SOLO_AUTHORITY)
    for brand in brand_terms:
        for auth in _AUTHORITY:
            lexicon.add(f"{brand} {auth}")
            lexicon.add(f"{auth} {brand}")
        lexicon.add(f"official {brand}")
    for term in staff_terms:
        for auth in ("support", "help", "team", "official", "service"):
            lexicon.add(f"{term} {auth}")

    return GuildSignals(
        topics=frozenset(topics),
        brand_terms=frozenset(brand_terms),
        staff_terms=frozenset(staff_terms),
        lexicon=frozenset(lexicon),
    )


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

@dataclass
class Detection:
    """Outcome of scoring a member's names against a guild's signals."""
    kind: str                       # "hoist" | "impersonation" | "both"
    matched: str                    # what matched (lexicon phrase or "hoist:<run>")
    confidence: float               # 0..1
    clean_nick: str                 # nick to rename the member to
    hoist_run: str = ""             # the stripped leading run, if any
    reasons: list[str] = field(default_factory=list)

    @property
    def is_impersonation(self) -> bool:
        return self.kind in ("impersonation", "both")

    @property
    def tier(self) -> str:
        return "high" if self.is_impersonation else "low"


def _phrase_hit(norm_name: str, name_tokens: set[str], lexicon: frozenset[str]) -> str | None:
    """Return the matched lexicon phrase, or None.

    A multi-word phrase matches if all its tokens are present in the name (order
    independent, so "Support - Binance" still hits "binance support"); a
    single-word authority phrase matches only as a whole token to avoid firing on
    substrings like "moderation".
    """
    for phrase in lexicon:
        parts = phrase.split()
        if len(parts) == 1:
            if parts[0] in name_tokens:
                return phrase
        elif all(p in name_tokens for p in parts):
            return phrase
    # Fallback: contiguous substring match on the normalized name (catches
    # run-together handles like "binancesupport").
    flat = norm_name.replace(" ", "")
    for phrase in lexicon:
        if len(phrase) >= 8 and phrase.replace(" ", "") in flat:
            return phrase
    return None


def classify(names: Iterable[str], signals: GuildSignals,
             *, display_name: str | None = None) -> Detection | None:
    """Score a member's names; return a :class:`Detection` or None for a clean name.

    ``names`` is the set of strings to inspect (username + display name);
    ``display_name`` is the one we would actually rename (defaults to the first
    name). Impersonation outranks a plain hoist; a name can be "both".
    """
    name_list = [n for n in names if n]
    if not name_list:
        return None
    primary = display_name if display_name is not None else name_list[0]

    # Hoist check on the *raw* display name (before folding).
    hoist_run = ""
    for n in name_list:
        run = leading_hoist_run(n)
        if len(run.strip()) >= 1 and run.strip():
            hoist_run = run
            break

    # Impersonation check on the folded names.
    matched = None
    for n in name_list:
        norm = normalize_display(n)
        if not norm:
            continue
        hit = _phrase_hit(norm, _tokens(n), signals.lexicon)
        if hit:
            matched = hit
            break

    if not matched and not hoist_run:
        return None

    if matched and hoist_run:
        kind, conf = "both", 0.97
    elif matched:
        kind, conf = "impersonation", 0.9
    else:
        kind, conf = "hoist", 0.5

    reasons: list[str] = []
    if matched:
        reasons.append(f"impersonation:{matched}")
    if hoist_run:
        reasons.append(f"hoist:{hoist_run.strip()[:8]!r}")

    return Detection(
        kind=kind,
        matched=matched or f"hoist:{hoist_run.strip()[:16]}",
        confidence=conf,
        clean_nick=clean_nick(primary),
        hoist_run=hoist_run,
        reasons=reasons,
    )
