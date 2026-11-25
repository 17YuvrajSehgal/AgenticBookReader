"""Rules for the reader profile (design 8.4). Plain code, no LLM, no database."""
import re

LEVELS = ["beginner", "learning", "comfortable"]
# A new reader gets the simplest explanations and moves up from there.
DEFAULT_LEVEL = "beginner"

DOWN_SIGNALS = 3    # this many difficulty signals...
DOWN_WINDOW = 10    # ...within the last 10 signals lowers the level
UP_STREAK = 5       # this many comfort signals in a row makes the tool ask about going up
PROTECT_DAYS = 14   # a level the reader stated cannot be changed by evidence for this long

# The skill list starts here. The page tagger adds more names when a book needs them.
SEED_SKILLS = [
    "academic_english_vocabulary", "math_notation", "greek_letters_in_math", "calculus_notation",
    "probability_basics", "statistics_basics", "linear_algebra_basics", "programming_basics",
    "machine_learning_basics",
]
SKILL_NAME = re.compile(r"[a-z][a-z0-9]*(_[a-z0-9]+){0,5}")


def valid_skill(name: str) -> bool:
    return bool(SKILL_NAME.fullmatch(name)) and len(name) <= 60


def page_level(levels: list[str]) -> str:
    """A page is explained at the lowest level among the skills it needs."""
    return min(levels, key=LEVELS.index, default=DEFAULT_LEVEL)


def step(level: str, by: int) -> str:
    return LEVELS[min(max(LEVELS.index(level) + by, 0), len(LEVELS) - 1)]


def decide(level: str, source: str, age_days: float, signals: list[str]) -> tuple[str, str] | None:
    """What the evidence says should happen to one skill.

    signals: newest first, only signals from after the level was last changed.
    Returns ("lower", level): change it now. ("ask_up" | "ask_down", level): ask the reader first. None: nothing.
    """
    if source == "stated" and age_days < PROTECT_DAYS:
        return None
    i = LEVELS.index(level)
    if i > 0 and signals[:DOWN_WINDOW].count("difficulty") >= DOWN_SIGNALS:
        # Going simpler is safe, so it happens alone. But never against what the reader said.
        return ("ask_down" if source == "stated" else "lower", LEVELS[i - 1])
    if i < len(LEVELS) - 1 and len(signals) >= UP_STREAK and all(s == "comfort" for s in signals[:UP_STREAK]):
        return ("ask_up", LEVELS[i + 1])  # never switch to less simple silently
    return None


if __name__ == "__main__":
    # Self-check. Run: python reader_profile.py
    D, C = "difficulty", "comfort"
    assert decide("learning", "inferred", 0, [D, C, D, C, D]) == ("lower", "beginner")
    assert decide("learning", "inferred", 0, [D, D]) is None
    assert decide("learning", "inferred", 0, [C] * 10 + [D] * 3) == ("ask_up", "comfortable")  # 3 D are outside the window
    assert decide("beginner", "inferred", 0, [D] * 5) is None                    # already the lowest
    assert decide("beginner", "inferred", 0, [C] * 5) == ("ask_up", "learning")
    assert decide("beginner", "inferred", 0, [C] * 4) is None
    assert decide("beginner", "inferred", 0, [C, "declined", C, C, C, C]) is None  # "not yet" breaks the streak
    assert decide("comfortable", "inferred", 0, [C] * 9) is None                 # already the highest
    assert decide("comfortable", "stated", 3, [D] * 5) is None                   # protected for 14 days
    assert decide("comfortable", "stated", 15, [D] * 5) == ("ask_down", "learning")  # after that: ask, do not switch
    assert decide("learning", "stated", 15, [C] * 5) == ("ask_up", "comfortable")
    assert page_level(["comfortable", "beginner"]) == "beginner" and page_level([]) == DEFAULT_LEVEL
    assert step("beginner", -1) == "beginner" and step("learning", 1) == "comfortable"
    assert valid_skill("calculus_notation") and not valid_skill("Calculus Notation") and not valid_skill("")
    print("ok")
