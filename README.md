# Minimal chatbot
**A minimal Flask + HTMX + LangChain (with llama.cpp) interface for learning purposes only**

> ❗ **This project is a personal learning experiment. It is not production-ready. Do not deploy this without major modifications.**

![screenshot](./doc/screenshot.png)

---

## About

This project was built as a **hands-on experiment** in combining:

- 🧠 A local **LLM backend** (via [llama.cpp](https://github.com/ggml-org/llama.cpp) and [LangChain](https://www.langchain.com/))
- ⚡ **HTMX** for seamless front-end interactivity without JavaScript frameworks
- 🌐 **Flask** for backend routing and SSE streaming
- 💅 A **Neumorphic UI** for minimal styling

It’s a **prototype**, intended for exploring techniques like:

- Streaming LLM output over Server-Sent Events (SSE)
- Managing chat history by session (in SQLite)
- Coordinating LLM calls with a safe in-process queue
- Building interactive web apps with minimal frontend JavaScript using [htmx](https://htmx.org/)

---

## 🚫 Not for Production

**This project is *not* suitable for deployment.**

- ❌ No authentication or session protection
- ❌ Very basic error handling
- ❌ Blocking, single-threaded queue for LLM calls
- ❌ Input is not sanitized or restricted
- ❌ SSE and stream parsing is naive

It’s meant for educational use only.

---

## Running the App

### Requirements

- [uv](https://docs.astral.sh/uv/)
- a compatible GGUF LLM model (e.g. Phi-3.5)
- a relatively fast computer (ideally with a strong GPU)

### Run
Download [Phi-3.5-mini-instruct-GGUF](https://huggingface.co/MaziyarPanahi/Phi-3.5-mini-instruct-GGUF) (tested with the [Q5_K_M](https://huggingface.co/MaziyarPanahi/Phi-3.5-mini-instruct-GGUF/blob/main/Phi-3.5-mini-instruct.Q5_K_M.gguf) quantization).
Set the `CHAT_MODEL_GGUF` environment variable to the full path of the `.gguf` file. Or edit the `.env` file to include: `CHAT_MODEL_GGUF=/path/to/your/model.gguf`

Install [uv](https://docs.astral.sh/uv/#installation). Then run:

```bash
uv run flask --app main run
```

Set `FLASK_DEBUG=1` for automatic reloading on code changes.

For CUDA support (Nvidia GPU) you must reinstall the [llama-cpp-python](https://github.com/inference-sh/llama-cpp-python) library:

``` bash
CMAKE_ARGS="\
 -DGGML_CUDA=on \
 -DLLAMA_BUILD_TESTS=OFF \
 -DLLAMA_BUILD_EXAMPLES=OFF \
 -DLLAMA_BUILD_TOOLS=OFF \
uv add --force-reinstall --no-cache-dir llama-cpp-python
```
