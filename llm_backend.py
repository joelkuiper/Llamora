import os
from langchain_community.llms import LlamaCpp
from langchain.chains import LLMChain
from langchain_core.prompts import PromptTemplate
from langchain_core.callbacks import CallbackManager, StreamingStdOutCallbackHandler
from langchain_core.runnables import RunnableSequence

# Callbacks support token-wise streaming
callback_manager = CallbackManager([StreamingStdOutCallbackHandler()])

llm = LlamaCpp(
    model_path=os.environ["MODEL_GGUF"],
    temperature=0.8,
    verbose=True,
    n_ctx=8192,
    streaming=True,
    n_gpu_layers=-1,
    callback_manager=callback_manager
)

template = """<|system|>
You are a helpful assistant.<|end|>
{history}
<|assistant|>"""

prompt = PromptTemplate.from_template(template)

def format_phi_history(history: list[dict]) -> str:
    """Convert chat history into Phi-compatible text block."""
    formatted = ""
    for msg in history:
        role = "assistant" if msg["role"] == "bot" else "user"
        content = msg["text"]
        formatted += f"<|{role}|>\n{content}<|end|>\n"
    return formatted

MAX_TOKENS = llm.n_ctx
RESERVED_FOR_RESPONSE = 512  # Conservative budget for the LLM output

def trim_history_to_fit(history: list[dict]) -> list[dict]:
    """Trim oldest user/bot message pairs to stay within context."""
    trimmed = history[:]
    while trimmed:
        formatted = format_phi_history(trimmed)
        token_count = llm.get_num_tokens(prompt.format(history=formatted))
        if token_count + RESERVED_FOR_RESPONSE <= MAX_TOKENS:
            break
        # Remove the oldest user+bot pair (2 messages)
        if len(trimmed) >= 2:
            trimmed = trimmed[2:]
        else:
            trimmed = trimmed[1:]
    return trimmed

chain = prompt | llm

def stream_response(history):
    """Call the LLM chain with user input and return the response."""
    trimmed_history = trim_history_to_fit(history)
    formatted_history = format_phi_history(trimmed_history)
    print(formatted_history)
    for token in chain.stream({"history": formatted_history}):
        yield token
