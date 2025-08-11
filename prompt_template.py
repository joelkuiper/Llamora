import textwrap

# Shared chat prompt template to avoid duplication between components.
CHAT_PROMPT_TEMPLATE = textwrap.dedent(
    """
    <|system|>
    “From shadow to light, a thread of understanding.”
    Keep replies brief, clear, and quietly resonant.<|end|>
    {history}<|assistant|>
    """
)

# System prompt text reused for OpenAPI-style chat messages.
SYSTEM_PROMPT = (
    "“From shadow to light, a thread of understanding.” "
    "Keep replies brief, clear, and quietly resonant."
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


def build_messages(history: list[dict]) -> list[dict]:
    """Return OpenAI-style messages including the system prompt."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history)
    return messages
