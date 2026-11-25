"""Reviewer memory: one saved preference per reviewer, and who a run is for.

The preference (brief or full conclusions) lives in LangGraph's store, under a
namespace that starts with the reviewer's user ID. It changes what the approval
step shows a reviewer, never what the agent decides.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewContext:
    """Who is running the graph. Passed in by the caller with each call.

    This is LangGraph's "runtime context": facts about one call that sit
    beside the state. It is not a key in the state and it is not saved with
    the run, so a resumed run does not know who started it unless the caller
    passes the context again. No node passes it to a model: the user ID
    decides whose memory is opened, so it must come from the program that
    knows who is signed in.
    """

    user_id: str | None = None

    def __post_init__(self):
        # Checked here, when the caller creates the context, so a bad ID stops
        # the run before any model call and not at the approval step.
        if self.user_id is not None:
            check_user_id(self.user_id)


def check_user_id(user_id):
    """Refuse the two unusable user IDs most likely to be typed.

    LangGraph's store joins the parts of a namespace with dots, so a part may
    not contain one, and may not be empty. "alice.smith" is refused. The store
    has a few rarer rules of its own, which it reports itself.
    """
    if not user_id or "." in user_id:
        raise ValueError(f"a user ID cannot be empty or contain a dot: {user_id!r}")


STYLES = ("brief", "full")


def save_conclusion_style(memory, user_id, style):
    """Remember how one reviewer wants conclusions shown."""
    check_user_id(user_id)
    if style not in STYLES:
        raise ValueError(f"the style must be one of: {', '.join(STYLES)}")
    # The namespace starts with the user ID, so each reviewer's memory is apart.
    memory.put((user_id, "preferences"), "conclusion_style", {"style": style})


def saved_style(memory, user_id):
    """One reviewer's saved style, or None if they have none."""
    saved = memory.get((user_id, "preferences"), "conclusion_style")
    if saved is None:
        return None
    style = saved.value.get("style")
    if style not in STYLES:
        raise ValueError(f"the style saved for {user_id} is not usable: {saved.value!r}")
    return style


def conclusion_style(runtime):
    """The style to use for the reviewer this call was made for."""
    reviewer = runtime.context
    if reviewer is None or reviewer.user_id is None:
        return "full"  # nobody named, as in an evaluation run or a resumed one
    if runtime.store is None:
        raise RuntimeError("a reviewer was named, but the graph was built without a store")
    # The namespace is built from the caller's user ID and nothing else. This
    # function takes no user ID of its own, so code inside the graph cannot ask
    # for another reviewer's memory. Whoever calls the graph still chooses the
    # ID; nothing here proves who they are.
    return saved_style(runtime.store, reviewer.user_id) or "full"
