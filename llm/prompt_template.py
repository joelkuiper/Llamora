import textwrap
from langchain_core.prompts import PromptTemplate

# Shared chat prompt template to avoid duplication between the web process
# and worker processes.
CHAT_PROMPT_TEMPLATE = textwrap.dedent(
    """
    <|system|>
    “From shadow to light, a thread of understanding.”
    Keep replies brief, clear, and quietly resonant.<|end|>
    {history}
    <|assistant|>
    """
)


def get_prompt() -> PromptTemplate:
    """Return a LangChain PromptTemplate for the chat prompt."""
    return PromptTemplate.from_template(CHAT_PROMPT_TEMPLATE)


def format_message(msg: dict) -> str:
    """Render a single chat message in the llama.cpp chat format."""
    return f"<|{msg['role']}|>\n{msg['content']}<|end|>\n"


def format_history(history: list[dict]) -> str:
    """Render an entire history sequence for llama.cpp."""
    return "".join(format_message(m) for m in history)
