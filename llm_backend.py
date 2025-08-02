import os
from langchain_community.llms import LlamaCpp
from langchain.chains import LLMChain
from langchain_core.prompts import PromptTemplate
from langchain_core.callbacks import CallbackManager, StreamingStdOutCallbackHandler
from langchain_core.runnables import RunnableSequence

# Callbacks support token-wise streaming
callback_manager = CallbackManager([StreamingStdOutCallbackHandler()])

MAX_RESPONSE_LENGTH = 1024

llm = LlamaCpp(
    model_path=os.environ["MODEL_GGUF"],
    temperature=0.8,
    max_tokens=MAX_RESPONSE_LENGTH,
    verbose=True,
    n_ctx=1024 * 9,  # 9216
    streaming=True,
    n_gpu_layers=-1,
    callback_manager=callback_manager,
)

template = """<|system|>
You are a helpful assistant. Keep your replies short, clear, and to the point.<|end|>
{history}
<|assistant|>"""

prompt = PromptTemplate.from_template(template)


def format_phi_history(history: list[dict]) -> str:
    """Convert chat history into Phi-compatible text block."""
    formatted = ""
    for msg in history:
        role = msg["role"]
        content = msg["content"]
        formatted += f"<|{role}|>\n{content}<|end|>\n"
    return formatted


MAX_TOKENS = llm.n_ctx


def trim_history_to_fit(
    history: list[dict],
    max_tokens=MAX_TOKENS,
    max_response_tokens=MAX_RESPONSE_LENGTH,
) -> list[dict]:
    """Trim the oldest entries in history to fit within context window."""
    trimmed = []

    # Work backwards so we prioritize the most recent context
    for message in reversed(history):
        # Temporarily prepend to a copy to test the size
        temp = [message] + trimmed
        formatted = format_phi_history(temp)
        token_count = llm.get_num_tokens(prompt.format(history=formatted))

        if token_count + max_response_tokens > max_tokens:
            break
        trimmed = temp

    return trimmed


chain = prompt | llm


def stream_response(history):
    """Call the LLM chain with user input and return the response."""
    trimmed_history = trim_history_to_fit(history)
    formatted_history = format_phi_history(trimmed_history)
    for token in chain.stream({"history": formatted_history}):
        yield token
