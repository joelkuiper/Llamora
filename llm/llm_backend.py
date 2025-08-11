import threading
import textwrap
import queue
import asyncio
import logging
from llm.prompt_template import (
    CHAT_PROMPT_TEMPLATE,
    get_prompt,
    format_history,
    format_message,
)
from llm.token_counter import TokenCounter
from langchain_community.llms import LlamaCpp
from langchain.chains import LLMChain
from langchain_core.prompts import PromptTemplate


class LLMEngine:
    """Handles queuing, streaming, and history formatting for an LLM chat interface.

    Supports basic scalability by running multiple worker threads, each with its own
    model instance. Requests are distributed across the workers allowing concurrent
    chats when ``max_workers`` is greater than ``1``.
    """

    def __init__(
        self,
        model_path: str,
        max_workers: int = 1,
        verbose: bool = False,
        llama_cpp_kwargs: dict | None = None,
    ):
        self.verbose = verbose
        self.model_path = model_path
        self.llama_cpp_kwargs = llama_cpp_kwargs or {}

        # Allow context length and max tokens to be overridden via configuration
        self.n_ctx = self.llama_cpp_kwargs.get("n_ctx")
        self.max_response_tokens = self.llama_cpp_kwargs.get("max_tokens")

        self.token_counter = TokenCounter(model_path)
        self.system_tokens = self.token_counter.count(
            CHAT_PROMPT_TEMPLATE.format(history="")
        )
        self.prompt = get_prompt()
        self._request_queue = queue.Queue()
        self.llm = None  # primary model for token counting
        self.MAX_TOKENS = None
        self._start_workers(max_workers)

    def _load_model(self):
        """Instantiate a ``LlamaCpp`` model with configured parameters."""

        kwargs = {"model_path": self.model_path, **self.llama_cpp_kwargs}

        return LlamaCpp(**kwargs)

    def _start_workers(self, count: int):
        logger = logging.getLogger(__name__)
        for i in range(count):
            llm = self._load_model()
            if self.llm is None:
                self.llm = llm
                self.MAX_TOKENS = llm.n_ctx
            chain = self.prompt | llm
            t = threading.Thread(
                target=self._worker_loop,
                args=(chain,),
                daemon=True,
            )
            t.start()
            logger.debug("Started LLM worker thread %s", t.name)

    def _worker_loop(self, chain: LLMChain):
        logger = logging.getLogger(__name__)
        while True:
            req = self._request_queue.get()
            if req is None:
                break
            try:
                trimmed = self._trim_history(req.history)
                formatted = format_history(trimmed)
                for token in chain.stream({"history": formatted}):
                    req.output_queue.put(token)
            except Exception:
                logger.exception("LLM generation failed")
                error_msg = "⚠️ Unable to generate response. Please try again later."
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

    def _trim_history(self, history: list[dict]) -> list[dict]:
        if not history:
            return []
        messages = [format_message(m) for m in history]
        counts = [self.token_counter.count(m) for m in messages]
        prefix = [0]
        for c in counts:
            prefix.append(prefix[-1] + c)
        max_tokens = self.n_ctx - self.max_response_tokens
        n = len(history)
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            used = self.system_tokens + (prefix[n] - prefix[mid])
            if used <= max_tokens:
                hi = mid
            else:
                lo = mid + 1
        return history[lo:]

    def stream_response(self, history: list[dict]):
        """Submit a request to the queue and return an async generator for its output."""
        logger = logging.getLogger(__name__)
        req = self.Request(history)
        self._request_queue.put(req)
        logger.debug("Queued LLM request with %d messages", len(history))

        async def generator():
            while True:
                token = await asyncio.to_thread(req.output_queue.get)
                if token is None:
                    break
                yield token

        return generator()
