import textwrap

PROMPT_TEMPLATE = textwrap.dedent(
    """
    <|system|>
    Your name is Llamora. A calm, wise companion. Keep replies concise, clear, and human. Lead with the answer; add one gentle insight if it helps. If uncertain, say so and propose a next step.<|end|>
    {history}
    <|assistant|>
    """
)


def format_message(msg: dict) -> str:
    """Render a single chat message in the llama.cpp chat format."""
    return f"<|{msg['role']}|>\n{msg['content']}<|end|>\n"


def format_history(history: list[dict]) -> str:
    """Render an entire history sequence for llama.cpp."""
    return "".join(format_message(m) for m in history)


def build_prompt(history: list[dict]) -> str:
    """Return a full prompt for *history* messages."""

    rendered = format_history(history)
    return PROMPT_TEMPLATE.format(history=rendered)
