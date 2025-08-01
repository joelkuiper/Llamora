from langchain_community.llms import LlamaCpp
from langchain.chains import LLMChain
from langchain.prompts import ChatPromptTemplate
from langchain_core.callbacks import CallbackManager, StreamingStdOutCallbackHandler
from langchain_core.runnables import RunnableSequence

# Callbacks support token-wise streaming
callback_manager = CallbackManager([StreamingStdOutCallbackHandler()])

llm = LlamaCpp(
    model_path="/home/joelkuiper/Downloads/Phi-3.5-mini-instruct.Q5_K_M.gguf",
    temperature=0.8,
    verbose=True,
    streaming=True,
    callback_manager=callback_manager
)

prompt = ChatPromptTemplate.from_template("<|system|>You are a helpful assistant.<|end|>\n<|user|>{user_input}<|end|>\n<|assistant|>")

chain = prompt | llm

def stream_response(user_input):
    """Call the LLM chain with user input and return the response."""
    for token in chain.stream({"user_input": user_input}):
        yield token
