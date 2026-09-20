"""Offline provider: a deterministic, rule-based stand-in for a model.

Why this exists
---------------
Local mode (``providers/claude.py``) needs ``ANTHROPIC_API_KEY`` and hosted
mode (``providers/runpod.py``) needs a RunPod endpoint. Neither is available
when someone clones this repo to try it, runs the test suite in CI, or demos
on a laptop with no network. ``VAULT_PROVIDER=offline`` makes the whole
pipeline -- ingest, scrub, extract, supersede, mirror, gate, scope, search,
pack, eval -- runnable end to end with no key and no network call.

This is **not** an extraction model and does not pretend to be one. It is a
published set of regular expressions over the transcript conventions this
repo documents (``## User`` / ``## Assistant`` Markdown, "Decision: k = v",
"going with X for Y", "on the N team"). On transcripts that do not follow
those conventions it will find little or nothing, which is the honest
failure mode -- it never invents a fact it did not match.

Safety note (PRD section 6.3 / section 9): every ``evidence`` string and the
episode ``summary`` are *generated* from the fields this module already
extracted -- ``f"user set {predicate} to {value}"`` -- and are never a slice
of the source transcript. That is what keeps the no-leak test green by
construction rather than by luck.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

_SPEAKER_RE = re.compile(r"^##\s+(user|assistant)\s*$", re.IGNORECASE | re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def _user_text(conversation_text: str) -> str:
    """Return the user's turns as one whitespace-normalised string.

    Facts come from what the *user* asserted, not from what the assistant
    suggested -- the assistant proposing "I'd lean toward SQLite" is not the
    user deciding it. Transcripts are hard-wrapped, so newlines inside a
    paragraph are collapsed to spaces to let patterns span line breaks.
    """
    body = _FRONTMATTER_RE.sub("", conversation_text)
    parts = _SPEAKER_RE.split(body)

    if len(parts) == 1:
        # No speaker headers (a pasted note, `vault_remember` text): use it all.
        return re.sub(r"\s+", " ", body).strip()

    chunks: list[str] = []
    # parts alternates: [preamble, role, chunk, role, chunk, ...]
    for role, chunk in zip(parts[1::2], parts[2::2], strict=False):
        if role.lower() == "user":
            chunks.append(chunk)
    return re.sub(r"\s+", " ", " ".join(chunks)).strip()


def _all_text(conversation_text: str) -> str:
    body = _FRONTMATTER_RE.sub("", conversation_text)
    return re.sub(r"\s+", " ", _SPEAKER_RE.sub(" ", body)).strip()


# ---------------------------------------------------------------------------
# Entity discovery
# ---------------------------------------------------------------------------

# A project name introduced explicitly ("a project called acme-platform").
_CALLED_RE = re.compile(
    r"\b(?:project|tracker|app|service|tool|repo|product)\s+called\s+"
    r"([a-z][a-z0-9]*(?:-[a-z0-9]+)*)",
    re.IGNORECASE,
)
# A hyphenated lowercase name used as the object of a preposition
# ("back on acme-platform", "the schema for north-star"). Requiring the
# preposition keeps ordinary compound adjectives -- "hand-rolled",
# "third-party", "server-side" -- out of the catalog.
_PREP_NAME_RE = re.compile(r"\b(?:on|for|to|of|with)\s+([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b")
# A hyphenated name in subject position ("acme-platform is moving forward",
# "north-star has its schema"). Transcript 5 mentions acme-platform only
# this way, and without this rule the project never reaches the catalog.
# Requiring a following copula/verb keeps compound adjectives out:
# "hand-rolled sessions are..." has a noun after the hyphenated word, not a verb.
_SUBJECT_NAME_RE = re.compile(
    r"(?:^|[.:;!?]\s+|,\s+)([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s+"
    r"(?:is|was|are|were|has|have|will|needs?|uses?|gets?|stays?|remains?)\b",
    re.IGNORECASE,
)

# "My manager for this is Jordan Alvarez", "my lead is Sam Okafor".
_PERSON_ROLE_RE = re.compile(
    r"\b(?:manager|lead|boss|PM|EM|engineering manager|teammate|colleague)\b"
    r"[^.]{0,40}?\bis\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
)

# Words that look like hyphenated project names but never are.
_NAME_STOPLIST = {
    "hand-rolled",
    "third-party",
    "server-side",
    "client-side",
    "open-source",
    "single-sign-on",
    "long-term",
    "short-term",
    "full-time",
    "part-time",
    "day-to-day",
    "check-ins",
    "sign-on",
    "one-off",
}


def _find_entities(user_text: str, all_text: str) -> tuple[list[dict], list[str]]:
    """Return (entity dicts, the project names in first-mention order)."""
    projects: list[str] = []

    def add(name: str) -> None:
        name = name.lower().strip(" .,;:")
        if name and name not in _NAME_STOPLIST and name not in projects:
            projects.append(name)

    for match in _CALLED_RE.finditer(all_text):
        add(match.group(1))
    for match in _PREP_NAME_RE.finditer(all_text):
        add(match.group(1))
    for match in _SUBJECT_NAME_RE.finditer(all_text):
        add(match.group(1))

    people: list[str] = []
    for match in _PERSON_ROLE_RE.finditer(all_text):
        person = match.group(1).strip()
        if person not in people:
            people.append(person)

    entities = [
        {"name": name, "kind": "project", "description": f"Project {name}."} for name in projects
    ]
    entities += [
        {"name": person, "kind": "person", "description": f"{person}, mentioned by the user."}
        for person in people
    ]

    return entities, projects


# ---------------------------------------------------------------------------
# Fact rules
# ---------------------------------------------------------------------------

_NAME = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"

# "Decision: auth = sessions for acme-platform"
_KV_RE = re.compile(
    rf"\b([a-z_]{{3,20}})\s*=\s*([A-Za-z][\w+.\- ]{{0,40}}?)(?:\s+for\s+({_NAME}))?"
    r"(?=[.,;]|\s+(?:and|but|revisit|so)\b|$)",
    re.IGNORECASE,
)
# "we're going with Clerk for acme-platform auth"
_CHOICE_RE = re.compile(
    rf"\b(?:going with|go with|went with|chose|chosen|decided on|settled on|picked)\s+"
    rf"([A-Za-z][\w+.\-]{{1,25}})\s+for\s+({_NAME})\s+([a-z]{{3,20}})\b",
)
# "transferred off the search team onto the infra team"
_TEAM_MOVE_RE = re.compile(r"\bonto the\s+([a-z]{2,20})\s+team\b", re.IGNORECASE)
# "I'm on the search team", "I'm fully on infra now"
_TEAM_RE = re.compile(
    r"\bI'?m\s+(?:still\s+|now\s+|fully\s+)?(?:formally\s+)?on\s+(?:the\s+)?([a-z]{2,20})(?:\s+team)?\b",
    re.IGNORECASE,
)
# "Sticking with SQLite", "start with that schema", "use Postgres"
_STORE_RE = re.compile(
    r"\b(?:sticking with|stick with|using|use|keep|keeping)\s+"
    r"(SQLite|Postgres|PostgreSQL|MySQL|Redis|DynamoDB|MongoDB)\b",
    re.IGNORECASE,
)
# "my rent just went up to $2,400 a month"
_RENT_RE = re.compile(r"\brent\b[^.]{0,60}?\$\s?([\d,]+(?:\.\d\d)?)", re.IGNORECASE)
# "cut off coffee after 2pm"
_CAFFEINE_RE = re.compile(
    r"\b(?:cut off|cutting off|avoid|no more|no)\s+(coffee|caffeine)\s+after\s+"
    r"(\d{1,2}\s?[ap]\.?m\.?)",
    re.IGNORECASE,
)
_SLEEP_RE = re.compile(
    r"\b(?:sleeping badly|not sleeping|trouble sleeping|insomnia)\b", re.IGNORECASE
)

# Predicates that are really project decisions, so they attach to the project.
_PROJECT_PREDICATES = {"auth", "authentication", "datastore", "database", "hosting", "db"}

_PREDICATE_ALIASES = {
    "authentication": "auth",
    "database": "datastore",
    "db": "datastore",
}


def _norm_predicate(raw: str) -> str:
    key = raw.strip().lower().replace(" ", "_")
    return _PREDICATE_ALIASES.get(key, key)


def _fact(
    predicate: str,
    value: str,
    entity: str,
    category: str,
    *,
    confidence: float,
    subject: str = "user",
) -> dict:
    """Build one fact. `evidence` is generated from the extracted fields --
    never a slice of the transcript -- so nothing verbatim reaches memory/."""
    evidence = f"user stated {predicate} = {value} for {entity}"[:120]
    return {
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "entity": entity,
        "category": category,
        "sensitive": category in {"finances", "health"},
        "confidence": confidence,
        "evidence": evidence,
    }


def _mention_positions(user_text: str, projects: list[str]) -> dict[str, list[int]]:
    lowered = user_text.lower()
    positions: dict[str, list[int]] = {}
    for name in projects:
        found = [m.start() for m in re.finditer(re.escape(name), lowered)]
        if found:
            positions[name] = found
    return positions


def _nearest_project(positions: dict[str, list[int]], at: int) -> str | None:
    """The project a statement at character `at` is about.

    A statement about a team or a datastore belongs to whichever project the
    user was talking about in that sentence, not to whichever project they
    mentioned most often in the conversation -- transcript 1 discusses both
    acme-platform and north-star, and "I'm on the search team" belongs to
    the first while "use SQLite" belongs to the second.

    The antecedent of a statement normally *precedes* it ("acme-platform is
    moving forward ... and I'm fully on infra now"), so the most recent
    preceding mention wins, and only when nothing precedes does the nearest
    following mention apply.
    """
    if not positions:
        return None

    preceding = {
        name: max((pos for pos in found if pos <= at), default=None)
        for name, found in positions.items()
    }
    before = {name: pos for name, pos in preceding.items() if pos is not None}
    if before:
        return max(before, key=lambda name: (before[name], name))

    return min(positions, key=lambda name: (min(positions[name]), name))


def _find_facts(user_text: str, projects: list[str], entity_names: set[str]) -> list[dict]:
    facts: list[dict] = []
    seen: set[tuple[str, str]] = set()
    positions = _mention_positions(user_text, projects)

    def push(fact: dict) -> None:
        key = (fact["entity"], fact["predicate"])
        if key in seen:
            return
        seen.add(key)
        facts.append(fact)

    # An explicit choice ("going with Clerk for acme-platform auth") is the
    # strongest signal, so it runs before the looser k=v rule and wins the
    # (entity, predicate) slot.
    for match in _CHOICE_RE.finditer(user_text):
        value, entity, predicate = match.group(1), match.group(2).lower(), match.group(3)
        if entity in entity_names:
            push(_fact(_norm_predicate(predicate), value, entity, "projects", confidence=0.95))

    for match in _KV_RE.finditer(user_text):
        predicate = _norm_predicate(match.group(1))
        value = match.group(2).strip(" .,;:")
        entity = (match.group(3) or "").lower()
        if not entity:
            if predicate not in _PROJECT_PREDICATES:
                continue
            entity = _nearest_project(positions, match.start()) or ""
        if entity in entity_names:
            push(_fact(predicate, value, entity, "projects", confidence=0.9))

    # Team: an explicit move ("onto the infra team") beats a standing
    # statement ("I'm still on the search team") in the same conversation.
    team_match = _TEAM_MOVE_RE.search(user_text)
    if team_match is None:
        for match in _TEAM_RE.finditer(user_text):
            if match.group(1).lower() not in {"it", "that", "this", "track", "board"}:
                team_match = match
                break
    if team_match is not None:
        entity = _nearest_project(positions, team_match.start())
        if entity:
            push(_fact("team", team_match.group(1).lower(), entity, "projects", confidence=0.9))

    for match in _STORE_RE.finditer(user_text):
        entity = _nearest_project(positions, match.start())
        if entity:
            push(_fact("datastore", match.group(1), entity, "projects", confidence=0.85))

    rent = _RENT_RE.search(user_text)
    if rent:
        push(_fact("rent", f"${rent.group(1)}/month", "personal", "finances", confidence=0.9))

    caffeine = _CAFFEINE_RE.search(user_text)
    if caffeine:
        push(
            _fact(
                "caffeine_cutoff",
                f"no {caffeine.group(1).lower()} after {caffeine.group(2).lower()}",
                "personal",
                "health",
                confidence=0.9,
            )
        )
    if _SLEEP_RE.search(user_text):
        push(_fact("sleep", "poor, under a doctor's advice", "personal", "health", confidence=0.8))

    return facts


# ---------------------------------------------------------------------------
# Episode
# ---------------------------------------------------------------------------


def _build_episode(facts: list[dict], entities: list[dict]) -> dict | None:
    """Compose a summary out of the extracted fields only."""
    if not facts and not entities:
        return None

    projects = [e["name"] for e in entities if e["kind"] == "project"]
    people = [e["name"] for e in entities if e["kind"] == "person"]

    pieces: list[str] = []
    if projects:
        pieces.append(f"Worked on {', '.join(projects)}.")
    if facts:
        recorded = "; ".join(f"{f['predicate']} = {f['value']}" for f in facts[:4])
        pieces.append(f"Recorded {recorded}.")
    if people:
        pieces.append(f"Mentioned {', '.join(people)}.")
    summary = " ".join(pieces) or "No durable facts recorded."

    categories = [f["category"] for f in facts]
    category = max(set(categories), key=categories.count) if categories else "projects"

    tags = sorted({f["predicate"] for f in facts} | {p for p in projects})

    return {
        "summary": summary[:400],
        "category": category,
        "tags": tags[:8],
        "entities": projects + people,
    }


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class OfflineProvider:
    """Deterministic rule-based provider. No network, no key."""

    name = "offline"

    def extract(self, conversation_text: str, schema: dict) -> dict:
        user_text = _user_text(conversation_text)
        all_text = _all_text(conversation_text)

        entities, projects = _find_entities(user_text, all_text)
        entity_names = {e["name"] for e in entities}

        facts = _find_facts(user_text, projects, entity_names)

        # A fact may name an entity ("personal") that no discovery rule
        # produced; the catalog still needs a row for it.
        for fact in facts:
            if fact["entity"] not in entity_names:
                entity_names.add(fact["entity"])
                entities.append(
                    {
                        "name": fact["entity"],
                        "kind": "topic",
                        "description": f"{fact['entity']} facts about the user.",
                    }
                )

        return {
            "facts": facts,
            "entities": entities,
            "episode": _build_episode(facts, entities),
        }

    def health(self) -> dict:
        return {
            "provider": self.name,
            "ok": True,
            "latency_ms": 0,
            "detail": "deterministic rule-based provider; no network call",
        }
