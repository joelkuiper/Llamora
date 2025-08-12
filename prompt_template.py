import textwrap

# Shared chat prompt template to avoid duplication between components.
CHAT_PROMPT_TEMPLATE = textwrap.dedent(
    """
    <|system|>
    You are Llamora, a wise companion. Keep replies brief, clear, and quietly resonant.<|end|>
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
    return CHAT_PROMPT_TEMPLATE.format(history=rendered)
