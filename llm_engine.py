import atexit
import json
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

import httpx

from config import MAX_RESPONSE_TOKENS
from prompt_template import build_prompt


def _find_free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class LlamaConfig:
    """Configuration for llamafile arguments and request parameters."""

    # request parameters
    temperature: float = 0.8
    top_k: int = 40
    top_p: float = 0.95
    min_p: float = 0.05
    n_predict: int = MAX_RESPONSE_TOKENS
    n_keep: int = 0
    stream: bool = True
    stop: list[str] = field(default_factory=list)
    tfs_z: float = 1.0
    typical_p: float = 1.0
    repeat_penalty: float = 1.1
    repeat_last_n: int = 64
    penalize_nl: bool = True
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    penalty_prompt: Any | None = None
    mirostat: int = 0
    mirostat_tau: float = 5.0
    mirostat_eta: float = 0.1
    grammar: str | None = None
    seed: int = -1
    ignore_eos: bool = False
    logit_bias: Any | None = None
    n_probs: int = 0
    image_data: Any | None = None
    slot_id: int = -1
    cache_prompt: bool = False
    system_prompt: Any | None = None

    # server arguments
    ctx_size: int = 2048
    threads: int | None = None
    threads_batch: int | None = None
    n_gpu_layers: int | None = None
    main_gpu: int | None = None
    tensor_split: str | None = None
    batch_size: int | None = None
    memory_f32: bool = False
    mlock: bool = False
    no_mmap: bool = False
    numa: bool = False
    lora: str | None = None
    lora_base: str | None = None
    timeout: int | None = None
    host: str | None = None
    api_key: list[str] = field(default_factory=list)
    api_key_file: str | None = None
    embedding: bool = False
    parallel: int | None = None
    cont_batching: bool = False
    system_prompt_file: str | None = None
    mmproj: str | None = None
    grp_attn_n: int | None = None
    grp_attn_w: int | None = None

    def to_cli_args(self) -> list[str]:
        args: list[str] = []
        if self.threads is not None:
            args += ["--threads", str(self.threads)]
        if self.threads_batch is not None:
            args += ["--threads-batch", str(self.threads_batch)]
        if self.ctx_size is not None:
            args += ["--ctx-size", str(self.ctx_size)]
        if self.n_gpu_layers is not None:
            args += ["--n-gpu-layers", str(self.n_gpu_layers)]
        if self.main_gpu is not None:
            args += ["--main-gpu", str(self.main_gpu)]
        if self.tensor_split is not None:
            args += ["--tensor-split", self.tensor_split]
        if self.batch_size is not None:
            args += ["--batch-size", str(self.batch_size)]
        if self.memory_f32:
            args.append("--memory-f32")
        if self.mlock:
            args.append("--mlock")
        if self.no_mmap:
            args.append("--no-mmap")
        if self.numa:
            args.append("--numa")
        if self.lora is not None:
            args += ["--lora", self.lora]
        if self.lora_base is not None:
            args += ["--lora-base", self.lora_base]
        if self.timeout is not None:
            args += ["--timeout", str(self.timeout)]
        if self.host is not None:
            args += ["--host", self.host]
        if self.api_key:
            for key in self.api_key:
                args += ["--api-key", key]
        if self.api_key_file is not None:
            args += ["--api-key-file", self.api_key_file]
        if self.embedding:
            args.append("--embedding")
        if self.parallel is not None:
            args += ["--parallel", str(self.parallel)]
        if self.cont_batching:
            args.append("--cont-batching")
        if self.system_prompt_file is not None:
            args += ["--system-prompt-file", self.system_prompt_file]
        if self.mmproj is not None:
            args += ["--mmproj", self.mmproj]
        if self.grp_attn_n is not None:
            args += ["--grp-attn-n", str(self.grp_attn_n)]
        if self.grp_attn_w is not None:
            args += ["--grp-attn-w", str(self.grp_attn_w)]
        return args

    def to_payload(self, prompt: str) -> dict[str, Any]:
        """Return a /completion payload for *prompt*."""
        return {
            "prompt": prompt,
            "temperature": self.temperature,
            "top_k": self.top_k,
            "top_p": self.top_p,
            "min_p": self.min_p,
            "n_predict": self.n_predict,
            "n_keep": self.n_keep,
            "stream": self.stream,
            "stop": self.stop,
            "tfs_z": self.tfs_z,
            "typical_p": self.typical_p,
            "repeat_penalty": self.repeat_penalty,
            "repeat_last_n": self.repeat_last_n,
            "penalize_nl": self.penalize_nl,
            "presence_penalty": self.presence_penalty,
            "frequency_penalty": self.frequency_penalty,
            "penalty_prompt": self.penalty_prompt,
            "mirostat": self.mirostat,
            "mirostat_tau": self.mirostat_tau,
            "mirostat_eta": self.mirostat_eta,
            "grammar": self.grammar,
            "seed": self.seed,
            "ignore_eos": self.ignore_eos,
            "logit_bias": self.logit_bias or [],
            "n_probs": self.n_probs,
            "image_data": self.image_data or [],
            "slot_id": self.slot_id,
            "cache_prompt": self.cache_prompt,
            "system_prompt": self.system_prompt,
        }


class LLMEngine:
    """Manage a llamafile server process and stream responses."""

    def __init__(self, llamafile_path: str, config: LlamaConfig | None = None):
        if not llamafile_path:
            raise ValueError("LLAMAFILE environment variable not set")

        self.config = config or LlamaConfig()
        self.port = _find_free_port()

        cmd = [llamafile_path]
        print(cmd)

        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.server_url = f"http://127.0.0.1:{self.port}"

        atexit.register(self.shutdown)
        self._wait_until_ready()

    def _wait_until_ready(self) -> None:
        for _ in range(100):
            try:
                resp = httpx.get(f"{self.server_url}/health", timeout=1.0)
                if resp.json().get("status") == "ok":
                    return
            except Exception:
                pass
            time.sleep(0.1)
        raise RuntimeError("llamafile server failed to start")

    def shutdown(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - unlikely
                self.proc.kill()

    async def _count_tokens(self, client: httpx.AsyncClient, text: str) -> int:
        resp = await client.post(f"{self.server_url}/tokenize", json={"content": text})
        resp.raise_for_status()
        return len(resp.json().get("tokens", []))

    async def _trim_history(self, history: list[dict]) -> list[dict]:
        if not history:
            return history

        max_input = self.config.ctx_size - self.config.n_predict
        async with httpx.AsyncClient(timeout=None) as client:
            lo, hi = 0, len(history)
            while lo < hi:
                mid = (lo + hi) // 2
                prompt = build_prompt(history[mid:])
                tokens = await self._count_tokens(client, prompt)
                if tokens <= max_input:
                    hi = mid
                else:
                    lo = mid + 1
        return history[lo:]

    async def stream_response(self, history: list[dict]) -> AsyncGenerator[str, None]:
        """Stream a response from the llamafile server for *history* messages."""

        history = await self._trim_history(history)
        prompt = build_prompt(history)
        payload = self.config.to_payload(prompt)

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{self.server_url}/completion",
                json=payload,
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if not data_str:
                        continue
                    try:
                        data = json.loads(data_str)
                    except Exception:  # pragma: no cover - network noise
                        continue
                    if data.get("stop"):
                        break
                    content = data.get("content")
                    if content:
                        yield content
