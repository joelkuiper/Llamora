import threading
import textwrap
import queue
import asyncio
from config import MAX_RESPONSE_TOKENS
from langchain_community.llms import LlamaCpp
from langchain.chains import LLMChain
from langchain_core.prompts import PromptTemplate
from langchain_core.callbacks import CallbackManager


class LLMEngine:
    """Handles queuing, streaming, and history formatting for an LLM chat interface."""

    def __init__(self, model_path: str, max_workers: int = 1, verbose: bool = False):
        self.verbose = verbose
        self.model_path = model_path
        self.llm = self._load_model()

        self.MAX_TOKENS = self.llm.n_ctx
        self.prompt = self._build_prompt()
        self.chain = self.prompt | self.llm
        self._request_queue = queue.Queue()
        self._start_workers(max_workers)

    def _load_model(self):
        return LlamaCpp(
            model_path=self.model_path,
            temperature=0.8,
            verbose=self.verbose,
            max_tokens=MAX_RESPONSE_TOKENS,
            n_ctx=1024 * 9,
            streaming=True,
            n_gpu_layers=-1,
        )

    def _build_prompt(self):
        template = textwrap.dedent(
            """\
            <|system|>
            “From shadow to light, a thread of understanding.”
            You are Llamora, a calm and perceptive assistant. Keep replies brief, clear, and quietly resonant.<|end|>
            {history}
            <|assistant|>"""
        )

        return PromptTemplate.from_template(template)

    def _start_workers(self, count: int):
        for _ in range(count):
            t = threading.Thread(target=self._worker_loop, daemon=True)
            t.start()

    def _worker_loop(self):
        while True:
            req = self._request_queue.get()
            if req is None:
                break
            try:
                trimmed = self._trim_history(req.history)
                formatted = self.format_history(trimmed)
                for token in self.chain.stream({"history": formatted}):
                    req.output_queue.put(token)
            except Exception as e:
                error_msg = f"⚠️ Error: {str(e)}"
                req.output_queue.put({"type": "error", "data": error_msg})

            finally:
                req.output_queue.put(None)
                req.done.set()
                self._request_queue.task_done()

    class Request:
        def __init__(self, history):
            self.history = history
            self.output_queue = queue.Queue()
            self.done = threading.Event()

    def format_history(self, history: list[dict]) -> str:
        """Convert chat history into Phi instruct-compatible prompt string."""
        return "".join(
            f"<|{msg['role']}|>\n{msg['content']}<|end|>\n" for msg in history
        )

    def _trim_history(self, history: list[dict]) -> list[dict]:
        """Trim oldest messages to fit within context window."""
        trimmed = []
        for message in reversed(history):
            temp = [message] + trimmed
            formatted = self.format_history(temp)
            prompt = self.prompt.format(history=formatted)
            token_count = self.llm.get_num_tokens(prompt)
            if token_count + MAX_RESPONSE_TOKENS > self.MAX_TOKENS:
                break
            trimmed = temp
        return trimmed

    def stream_response(self, history: list[dict]):
        """Submit a request to the queue and return an async generator for its output."""
        req = self.Request(history)
        self._request_queue.put(req)

        async def generator():
            while True:
                token = await asyncio.to_thread(req.output_queue.get)
                if token is None:
                    break
                yield token

        return generator()
