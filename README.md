CMAKE_ARGS="-DGGML_CUDA=on -DLLAVA_BUILD=off \
            -DLLAMA_BUILD_EXAMPLES=OFF \
            -DLLAMA_BUILD_TESTS=OFF" FORCE_CMAKE=1 CUDA_TOOLKIT_ROOT_DIR=/usr/lib/cuda \
uv pip install llama-cpp-python

FLASK_DEBUG=1 uv run flask --app main run 
 
 
 
 
 
 
 
 
 
 
