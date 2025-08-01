from langchain_community.llms import LlamaCpp
from langchain.chains import LLMChain
from langchain.prompts import ChatPromptTemplate

llm = LlamaCpp(
    model_path="/home/joelkuiper/Downloads/Phi-3.5-mini-instruct.Q5_K_M.gguf",
    temperature=0.8,
    verbose=True
)

prompt = ChatPromptTemplate.from_template("<|system|>You are a helpful assistant.<|end|>\n<|user|>{user_input}<|end|>\n<|assistant|>")

chain = prompt | llm

def generate_response(user_input: str) -> str:
    """Call the LLM chain with user input and return the response."""
    return chain.invoke({"user_input": user_input}).strip()
